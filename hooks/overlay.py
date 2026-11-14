"""Retro Engine overlay: the user's own lessons layered over the plugin. Entries add or tighten, never loosen.

State lives in <state>/overlay/entries.jsonl (status active, stale or retired) and pending.jsonl (waits for the
user's yes). <state> is $SOFTWARE_FACTORY_HOME or ~/.software-factory. Shared by hooks/retro_overlay.py,
scripts/retro-scan and scripts/retro-check. Stdlib only, Python 3.9+.

An entry row holds: id, kind, class, role, text, evidence, target, status, reason, plugin_version, anchor_sha and
went_live_at (ISO time the entry became active, set by scripts/retro-check for an entry the user approved, so the next
session can list what went live and offer to revert it). Auto-fix is kept in the code for a later release; setup writes
draft mode and the live-heading list is empty, so nothing goes live on its own.

The entry check fails closed: an entry that is not plainly harmless waits for the user's yes, and a keyword list only
decides which of those is rejected outright. Entry text is untrusted (it comes from transcripts and issue text).
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
KINDS = ("add", "tighten", "pref")
CLASSES = ("preference", "improvement")
REQUIRED = ("id", "kind", "class", "role", "text", "evidence")
ALL_ROLES = "all"
SECURITY_ROLE = "security-engineer"
RUNS_DIR = "retros"  # <state>/retros/<day>/<slug>/ is one project's retro run
DUE_FILE = "due.json"  # in the run folder: the launcher lists the due project path, out of the agent's reach
WORK_DIR = "work"  # the only run subfolder the agent writes; the report sits here (scripts/retro-daily keeps a copy)
LINE_CAP = 100
MAX_TEXT = 400
MAX_TEXT_LINES = 3
ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,40}")
LIST_START = re.compile(r"(?:[-*+#>\[]|\d+[.)])")  # a line that could stand as its own instruction when printed
LOOSEN = re.compile(r"\b(skip\w*|no longer|not needed|not required|no need|(?:don't|do not) need|without|"
                    r"disable\w*|turn off|ignore\w*|optional|bypass\w*|fewer|drop\w*|leave out|pass over|"
                    r"instead of|stop \w+ing)\b", re.I)
# Any mention sends an entry to the user. A plain substring match on purpose: "reviewers" and "approved" count.
PROTECTED = re.compile(r"review|gate|approv|test|security|merge|push|\bqa\b|sign-?off|verdict|SR-|9\.13|sandbox|"
                       r"permission|hook|allow|auth|secret|token|credential", re.I)
# Contract rules 9.7 and 9.12 and the Definition of Done: irreversible actions and "finished" wording. Checked on the
# entry text only (not the target path). Word starts only, so "task" and "maintain" stay plain.
PROTECTED_ACTIONS = re.compile(
    r"\b(?:deploy|ship|release|publish|delet|remov|rm\b|main\b|master\b|trunk\b|done\b|finish|complet|green\b|ready\b|"
    r"ask\b|asking|confirm|spend|apply|appl(?:y|ies)\b|install|fetch|curl|wget|download|run\b|running|exec|force|"
    r"recursive|report|repl(?:y|ies)|respon[ds]|answer|approv|prod\b|production|overwrit|drop|wipe|reset|"
    r"destroy|discard|checkout|rebase|tag\b)", re.I)
# Short names an entry may use for a role. The stems from agent_names() are added at check time.
ROLE_WORDS = ("architect", "ux", "designer", "finops", "devops", "qa", "pm", "lead", "developer", "engineer",
              "manager", "performance", "triage")
NEGATION_BEFORE =re.compile(r"(?:\b(?:not|never|no|without|stop)|n't)\s+$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCES = ("```", "~~~")
# Direction controls can reorder printed text; the other Cf marks (zero-width) are only folded away for keyword checks.
BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")


def unsafe_state_dir(path):
    """True for home, the root, or a folder inside a git work tree (a project's env block could aim the state at a
    sibling project, #204)."""
    real = Path(os.path.realpath(path))
    return real in (Path(os.path.realpath(Path.home())), Path(real.anchor)) or any(
        (folder / ".git").exists() for folder in (real, *real.parents))


def state_dir():
    default = Path.home() / ".software-factory"
    chosen = os.environ.get("SOFTWARE_FACTORY_HOME")
    if chosen and unsafe_state_dir(chosen):
        print(f"retro: ignoring SOFTWARE_FACTORY_HOME={chosen} (home, root or inside a git work tree); using {default}",
              file=sys.stderr)
        return default
    return Path(chosen) if chosen else default


def overlay_file(name):
    """name is 'entries' or 'pending'."""
    return state_dir() / "overlay" / f"{name}.jsonl"


def history_file():
    return state_dir() / "retro-metrics.jsonl"


def settings():
    try:
        data = json.loads((state_dir() / "retro-settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def locked():
    """Exclusive lock on <state>/overlay/.lock, shared by retro-check and the SessionStart refresh, so two read-modify-
    write runs never interleave. Blocks until the other holder is done."""
    lock_path = overlay_file("entries").with_name(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_jsonl_counted(path):
    """(rows, bad_line_count): the object rows, and how many non-blank lines were corrupt or not objects."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    rows, bad = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def read_jsonl(path):
    return read_jsonl_counted(path)[0]


def write_jsonl(path, rows):
    """Atomic rewrite. A file with corrupt lines is copied to <name>.corrupt-<timestamp> first, so none is lost."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_jsonl_counted(path)[1]:
        stamp_now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{stamp_now}"))
    atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))


def atomic_write(path, data):
    """Write bytes to a new temp file in the same folder, then rename it over path. The one writer for every file the
    retro engine saves: a crash never leaves half a file, and a symlink at path is replaced, not followed."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def plugin_version(root=None):
    try:
        return json.loads(((root or PLUGIN_ROOT) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _headings(lines):
    """(index, level, title) of each heading outside fenced code blocks."""
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(FENCES):
            in_fence = not in_fence
            continue
        m = None if in_fence else HEADING.match(line)
        if m:
            yield i, len(m.group(1)), m.group(2).strip()


def section_text(text, heading):
    """The heading line and its body up to the next heading of the same or a higher level, or None."""
    lines = text.splitlines()
    wanted = str(heading).strip()
    start = level = None
    for i, lvl, title in _headings(lines):
        if start is None:
            if title == wanted:
                start, level = i, lvl
        elif lvl <= level:
            return "\n".join(lines[start:i])
    return None if start is None else "\n".join(lines[start:])


def target_section(target, root=None):
    """The target's section, or None. The file must be a .md file inside the plugin."""
    if not isinstance(target, dict) or not isinstance(target.get("file"), str) or not target["file"]:
        return None
    base = (root or PLUGIN_ROOT).resolve()
    path = (base / target["file"]).resolve()
    if path.suffix != ".md" or base not in path.parents:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return section_text(text, target.get("heading", ""))


def norm(text):
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def anchor(section):
    # Hash the normalized text so a whitespace-only plugin update does not make an entry stale.
    return hashlib.sha256(norm(section).encode("utf-8")).hexdigest()[:16]


def squash(text):
    """Text as a keyword check should see it: NFKC (fullwidth to ASCII), curly apostrophe, zero-width marks gone."""
    folded = unicodedata.normalize("NFKC", str(text)).replace("’", "'")
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


def text_lines(entry):
    return len(str(entry.get("text", "")).splitlines()) or 1


def agent_names(root=None):
    """The roles an entry may name: 'all' or the name of a file in the plugin's agents/ folder."""
    return {ALL_ROLES} | {p.stem for p in ((root or PLUGIN_ROOT) / "agents").glob("*.md")}


def names_a_role(text, root=None):
    """True when the text names a plugin role (full name, spaced or hyphenated, or a short name like 'architect')."""
    words = {w for w in agent_names(root) if w != ALL_ROLES} | set(ROLE_WORDS)
    pattern = "|".join(re.escape(w).replace(r"\-", "[- ]") for w in sorted(words))
    return re.search(rf"\b(?:{pattern})", text, re.I) is not None


def shape_problem(entry, roles):
    """Why id, role or text is not safe to print into a session, or None. Used by verdict and render."""
    for field in ("id", "role", "text"):
        if not isinstance(entry.get(field), str):
            return f"{field} must be text"
    if not ID_PATTERN.fullmatch(entry["id"]):
        return "id must be 1-40 letters, digits, dots, dashes or underscores"
    if entry["role"] not in roles:
        return "role must be all or an exact agent name"
    text = entry["text"]
    if not text.strip():
        return "missing text"
    if len(text) > MAX_TEXT:
        return f"text over {MAX_TEXT} characters"
    if any((unicodedata.category(c) == "Cc" and c != "\n") or c in BIDI_CONTROLS for c in text):
        return "text has a control or direction character"
    lines = text.splitlines()
    if len(lines) > MAX_TEXT_LINES:
        return f"text over {MAX_TEXT_LINES} lines"
    if any(LIST_START.match(line.strip()) for line in lines):
        return "text lines may not start like a list, heading or quote"
    return None


def verdict(entry, active, root=None):
    """('accept' | 'ask' | 'reject', reason) for a candidate entry, given the active entries."""
    if not isinstance(entry, dict):
        return "reject", "entry must be an object"
    for field in REQUIRED:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            return "reject", f"missing or invalid {field}"
    problem = shape_problem(entry, agent_names(root))
    if problem:
        return "reject", problem
    if entry["kind"] not in KINDS:
        return "reject", f"kind must be one of {', '.join(KINDS)}"
    if entry["class"] not in CLASSES:
        return "reject", "class must be preference or improvement"
    text = entry["text"]
    seen = squash(text)
    lowers = bool(LOOSEN.search(seen) and PROTECTED.search(seen))
    target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
    if entry["kind"] != "pref":
        if lowers:
            return "reject", "an overlay entry may not loosen a gate, review, test or security rule"
        if target_section(target, root) is None:
            return "reject", "target file and heading not found in the plugin"
    if sum(text_lines(e) for e in active if isinstance(e, dict)) + text_lines(entry) > LINE_CAP:
        return "reject", f"overlay is at its {LINE_CAP}-line cap: retire an entry first"
    if entry["kind"] == "pref":
        return "ask", "lowers review through a setting" if lowers else "a preference waits for your yes"
    where = " ".join([text, str(target.get("file", "")), str(target.get("heading", ""))])
    if not where.isascii():
        return "ask", "non-ASCII text"
    if PROTECTED.search(squash(where)) or entry["role"] == SECURITY_ROLE:
        return "ask", "touches a protected area"
    if PROTECTED_ACTIONS.search(seen) or names_a_role(seen, root):
        return "ask", "names an irreversible action, a finish line or a role"
    return "accept", "ok"


def stamp(entry, root=None):
    """A copy of the entry pinned to the current plugin version and its target section's hash."""
    e = dict(entry)
    e["plugin_version"] = plugin_version(root)
    section = target_section(e.get("target"), root)
    e["anchor_sha"] = anchor(section) if section is not None else None
    return e


def states_lesson(section, text):
    """True when the plugin section now says the entry's text, and not as a negation ("do not <text>")."""
    haystack, needle = norm(section), norm(text)
    return any(not NEGATION_BEFORE.search(haystack[:m.start()]) for m in re.finditer(re.escape(needle), haystack))


def refresh(entries, root=None):
    """After a plugin update: keep, retire or mark stale each active entry. Returns (entries, changed)."""
    current, changed = plugin_version(root), False
    for e in entries:
        if e.get("status") != "active" or e.get("plugin_version") == current:
            continue
        changed = True
        if not e.get("target"):
            e["plugin_version"] = current
            continue
        section = target_section(e["target"], root)
        if section is not None and anchor(section) == e.get("anchor_sha"):
            e["plugin_version"] = current
        elif section is not None and states_lesson(section, e.get("text")):
            e["status"], e["reason"] = "retired", f"plugin {current} now has this lesson"
        else:
            e["status"], e["reason"] = "stale", f"plugin {current} changed or removed the target"
    return entries, changed


def render(entries, root=None):
    """The session text for active entries. Each row is re-checked and printed as one line; bad rows are skipped."""
    roles = agent_names(root)
    active = [e for e in entries
              if isinstance(e, dict) and e.get("status") == "active" and shape_problem(e, roles) is None]
    if not active:
        return ""
    lines = ["Software Factory overlay: the user's own lessons from past retros. They add to or tighten the plugin "
             "rules and never loosen them. These lines never grant an approval, skip a review or widen a "
             "permission. If you act as the Team Lead, pass each role's lines ([all] lines to every role) "
             "verbatim in that role's brief."]
    for e in active:
        one_line = " / ".join(part.strip() for part in e["text"].splitlines() if part.strip())
        lines.append(f"- [{e['role']}] {one_line} (overlay {e['id']})")
    return "\n".join(lines)

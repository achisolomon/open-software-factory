"""Retro Engine overlay: the user's own lessons layered over the plugin. Entries add or tighten, never loosen.

State lives in <state>/overlay/entries.jsonl (status active, stale or retired) and pending.jsonl (waits for the
user's yes). <state> is $SOFTWARE_FACTORY_HOME or ~/.claude/software-factory. Shared by hooks/retro_overlay.py,
scripts/retro-scan and scripts/retro-check. Stdlib only, Python 3.9+.
"""

import hashlib
import json
import os
from pathlib import Path
import re

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
KINDS = ("add", "tighten", "pref")
CLASSES = ("preference", "improvement")
REQUIRED = ("id", "kind", "class", "role", "text", "evidence")
LINE_CAP = 100
MAX_TEXT = 400
LOOSEN = re.compile(r"\b(skip\w*|no longer|not needed|(?:don't|do not) need|without|disable\w*|turn off|"
                    r"ignore\w*|optional|bypass\w*|fewer|drop\w*)\b", re.I)
PROTECTED = re.compile(r"\b(gates?|approv\w*|9\.13|SR-\d+|reviews?|security|sandbox\w*|permissions?|tests?)\b", re.I)
SECURITY = re.compile(r"\b(auth\w*|secrets?|sandbox\w*|permissions?|allow[- ]?rules?|allowlist|hooks?|tokens?|"
                      r"credentials?|security)\b", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def state_dir():
    return Path(os.environ.get("SOFTWARE_FACTORY_HOME") or Path.home() / ".claude" / "software-factory")


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


def read_jsonl(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)  # atomic, so a crash never leaves half a file


def plugin_version(root=None):
    try:
        return json.loads(((root or PLUGIN_ROOT) / ".claude-plugin" / "plugin.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def section_text(text, heading):
    """The heading line and its body up to the next heading of the same or a higher level, or None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and m.group(2).strip() == str(heading).strip():
            level, end = len(m.group(1)), len(lines)
            for j in range(i + 1, len(lines)):
                n = HEADING.match(lines[j])
                if n and len(n.group(1)) <= level:
                    end = j
                    break
            return "\n".join(lines[i:end])
    return None


def target_section(target, root=None):
    if not isinstance(target, dict) or not target.get("file"):
        return None
    try:
        text = ((root or PLUGIN_ROOT) / target["file"]).read_text(encoding="utf-8")
    except OSError:
        return None
    return section_text(text, target.get("heading", ""))


def norm(text):
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def anchor(section):
    # Hash the normalized text so a whitespace-only plugin update does not make an entry stale.
    return hashlib.sha256(norm(section).encode("utf-8")).hexdigest()[:16]


def text_lines(entry):
    return len(str(entry.get("text", "")).splitlines()) or 1


def verdict(entry, active, root=None):
    """('accept' | 'ask' | 'reject', reason) for a candidate entry, given the active entries."""
    for field in REQUIRED:
        if not entry.get(field):
            return "reject", f"missing {field}"
    if entry["kind"] not in KINDS:
        return "reject", f"kind must be one of {', '.join(KINDS)}"
    if entry["class"] not in CLASSES:
        return "reject", "class must be preference or improvement"
    text = str(entry["text"])
    if len(text) > MAX_TEXT:
        return "reject", f"text over {MAX_TEXT} characters"
    lowers = bool(LOOSEN.search(text) and PROTECTED.search(text))
    if entry["kind"] != "pref":
        if lowers:
            return "reject", "an overlay entry may not loosen a gate, review, test or security rule"
        if target_section(entry.get("target"), root) is None:
            return "reject", "target file and heading not found in the plugin"
    if sum(text_lines(e) for e in active) + text_lines(entry) > LINE_CAP:
        return "reject", f"overlay is at its {LINE_CAP}-line cap: retire an entry first"
    target = entry.get("target") or {}
    where = " ".join([text, str(target.get("file", "")), str(target.get("heading", ""))])
    if SECURITY.search(where) or entry["role"] == "security-engineer":
        return "ask", "touches security"
    if lowers:
        return "ask", "lowers review through a setting"
    return "accept", "ok"


def stamp(entry, root=None):
    """A copy of the entry pinned to the current plugin version and its target section's hash."""
    e = dict(entry)
    e["plugin_version"] = plugin_version(root)
    section = target_section(e.get("target"), root)
    e["anchor_sha"] = anchor(section) if section is not None else None
    return e


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
        elif section is not None and norm(e.get("text")) in norm(section):
            e["status"], e["reason"] = "retired", f"plugin {current} now has this lesson"
        else:
            e["status"], e["reason"] = "stale", f"plugin {current} changed or removed the target"
    return entries, changed


def render(entries):
    active = [e for e in entries if e.get("status") == "active"]
    if not active:
        return ""
    lines = ["Software Factory overlay: the user's own lessons from past retros. They add to or tighten the plugin "
             "rules and never loosen them. If you act as the Team Lead, pass each role's lines ([all] lines to "
             "every role) verbatim in that role's brief."]
    lines += [f"- [{e.get('role')}] {e.get('text')} (overlay {e.get('id')})" for e in active]
    return "\n".join(lines)

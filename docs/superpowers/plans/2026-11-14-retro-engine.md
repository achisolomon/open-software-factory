# Retro Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily, machine-wide retro that measures every factory project whose main moved, applies at most 2 safe fixes per project to the user's own overlay (or drafts them), and drafts plugin changes seen in 3+ projects.

**Architecture:** Deterministic Python scripts do all counting, checking and file writes (`scripts/retro-scan`, `scripts/retro-check`, shared `hooks/overlay.py`). One `sonnet` Retro agent (`agents/retro.md`) reads the numbers, picks causes and changes, and hands every write to `retro-check`. A launchd/cron job (`scripts/retro-daily`, installed by `scripts/retro-schedule`) runs the agent headless only when a project is due; a SessionStart hook (`hooks/retro_overlay.py`) loads the overlay and surfaces waiting items.

**Tech Stack:** Python 3.9+ standard library only, `unittest`, Claude Code plugin hooks, `claude -p --agent`, `gh`, launchd (macOS) or crontab (Linux).

**Spec:** `docs/superpowers/specs/2026-11-14-retro-engine-design.md`. Plan-time decisions that refine it are in Task 11.

**Conventions (read once):**
- Run tests: `python3 -m unittest discover -s tests` from the worktree root. Single file: `python3 -m unittest tests.test_retro_scan -v`.
- Scripts in `scripts/` have no `.py` extension and a `#!/usr/bin/env python3` line; make them executable (`chmod +x`).
- Hooks in `hooks/` import siblings directly (`import overlay`), because Python puts the script's folder on `sys.path`.
- Never use the em dash character in any file. Never name a private project, customer or local user path in any file (public repo).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Responsibility |
|---|---|
| `hooks/overlay.py` (new) | Overlay state: paths, JSONL read/write, plugin section hashing, the entry check (accept / ask / reject), refresh after a plugin update, rendering |
| `scripts/retro-scan` (new) | Find factory projects, decide which are due, measure one project's window from transcripts and `gh` |
| `scripts/retro-check` (new) | The only writer of the overlay and the retro history: apply, approve, drop, retire, verdicts, lessons, scrub, publish |
| `hooks/retro_overlay.py` (new) | SessionStart: refresh and print the overlay, stale count, pending count, failed-run marker |
| `scripts/retro-daily` (new) | Daily launcher: find the newest plugin copy, run `retro-scan --due`, start one headless Retro run |
| `scripts/retro-schedule` (new) | Install or remove the daily job (launchd plist or crontab line) |
| `agents/retro.md` (new) | The Retro agent's instructions |
| `skills/retro/SKILL.md` (new) | `/software-factory:retro` (run now) and `/software-factory:retro review` (answer waiting items) |
| `hooks/hooks.json` | Register `retro_overlay.py` on SessionStart |
| `agents/team-lead.md` | Step 12 points at the engine; delegations carry overlay lines |
| `skills/session-setup/SKILL.md` | Step 7: retro mode and schedule, once per machine |
| `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json`, `tests/test_release_files.py` | 0.9.0 release files, agent table and counts |
| `tests/test_retro_scan.py`, `tests/test_retro_check.py`, `tests/test_retro_overlay.py`, `tests/test_retro_schedule.py` (new) | Tests |

---

### Task 0: Spike, headless run from a scheduler-like environment

Verifies the spec's first "Not checked yet" item before anything depends on it. No code is committed except the spec note.

**Files:**
- Modify: `docs/superpowers/specs/2026-11-14-retro-engine-design.md` (section "Not checked yet")

- [ ] **Step 1: Confirm the CLI flags exist**

Run: `claude --help`
Expected: the output lists `--agent`, `--allowedTools` (or `--allowed-tools`), `--permission-mode` and `-p/--print`. Note the exact spelling of the allowed-tools flag; Task 7 uses it.

- [ ] **Step 2: Run an installed factory agent headless with a minimal environment and a path-scoped write rule**

Create a scratch folder: `mkdir -p "$FACTORY_TMP/spike"`. Then run (this calls the Claude API, so it needs the sandbox off; say so in the approval description):

```bash
env -i HOME="$HOME" PATH="/usr/bin:/bin:$(dirname "$(command -v claude)")" claude -p --agent software-factory:triage-classifier --allowedTools "Read,Write(/$FACTORY_TMP/spike/**),Bash(python3 $PWD/scripts/review-route *)" "Write the word ok to $FACTORY_TMP/spike/out.txt. Then run: python3 $PWD/scripts/review-route --help. Then reply with one line: done."
```

Expected: `$FACTORY_TMP/spike/out.txt` contains `ok`, the reply says `done`, and no permission prompt or "not allowed" error appears. Also try one write outside the rule (ask it to write `$HOME/spike-denied.txt`): expected denied.

- [ ] **Step 3: Record the result in the spec**

Replace the first bullet of "Not checked yet" with what happened, for example:
`- Checked 2026-11-14: claude -p --agent runs with a minimal PATH; --allowedTools accepts comma-separated rules, Write(/<abs path>/**) scopes writes. Unscoped writes are denied.`
If a step failed, write the failure and use the fallback already in the design: the launcher leaves `retro-failed`, the next session start tells the Team Lead, and the user runs `/software-factory:retro`. Adjust `allowed_tools()` in Task 7 to the syntax that worked.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-11-14-retro-engine-design.md
git commit -m "Spec: record headless retro spike result"
```

---

### Task 1: `hooks/overlay.py`, overlay state and the entry check

**Files:**
- Create: `hooks/overlay.py`
- Test: `tests/test_retro_check.py` (the overlay functions are tested through `retro-check` in Task 4; this task adds a direct unit test file section)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retro_check.py` with this first part (Task 4 appends more classes to it):

```python
"""Tests for hooks/overlay.py and scripts/retro-check."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import overlay  # noqa: E402

CHECK = ROOT / "scripts" / "retro-check"

DEV_FILE = "agents/software-developer.md"
DEV_TEXT = "# Developer\n\n## Tests\n\nRun the tests before you reply.\n\n## Commits\n\nOne commit per task.\n"


def make_plugin(base, version="1.0.0", dev_text=DEV_TEXT):
    plugin = base / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin / "agents").mkdir(exist_ok=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
    (plugin / DEV_FILE).write_text(dev_text)
    return plugin


def entry(**over):
    base = {"id": "r-1", "kind": "add", "class": "improvement", "role": "software-developer",
            "text": "Run the unit tests for the changed package first.", "evidence": "errors.test-or-build-fail 4.0/session",
            "target": {"file": DEV_FILE, "heading": "Tests"}}
    base.update(over)
    return base


class OverlayUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="overlay-")
        self.plugin = make_plugin(Path(os.path.realpath(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_section_text_stops_at_same_level_heading(self):
        section = overlay.section_text(DEV_TEXT, "Tests")
        self.assertIn("Run the tests", section)
        self.assertNotIn("One commit", section)
        self.assertIsNone(overlay.section_text(DEV_TEXT, "Missing"))

    def test_plain_improvement_is_accepted(self):
        self.assertEqual(overlay.verdict(entry(), [], self.plugin), ("accept", "ok"))

    def test_loosening_a_protected_rule_is_rejected(self):
        verdict, reason = overlay.verdict(entry(text="Skip the security review for small tasks."), [], self.plugin)
        self.assertEqual(verdict, "reject")
        self.assertIn("loosen", reason)

    def test_security_topic_asks(self):
        verdict, _ = overlay.verdict(entry(text="Add the test command to the allow rules."), [], self.plugin)
        self.assertEqual(verdict, "ask")

    def test_pref_that_lowers_review_asks(self):
        pref = entry(kind="pref", **{"class": "preference"}, text="Skip UX reviews for copy-only tasks.", target=None)
        self.assertEqual(overlay.verdict(pref, [], self.plugin)[0], "ask")

    def test_missing_field_unknown_kind_and_unknown_target_are_rejected(self):
        self.assertEqual(overlay.verdict(entry(evidence=""), [], self.plugin)[0], "reject")
        self.assertEqual(overlay.verdict(entry(kind="loosen"), [], self.plugin)[0], "reject")
        bad = entry(target={"file": DEV_FILE, "heading": "Nope"})
        self.assertIn("not found", overlay.verdict(bad, [], self.plugin)[1])

    def test_line_cap(self):
        full = [{"text": "x", "status": "active"}] * overlay.LINE_CAP
        verdict, reason = overlay.verdict(entry(), full, self.plugin)
        self.assertEqual(verdict, "reject")
        self.assertIn("cap", reason)

    def test_refresh_keeps_retires_or_marks_stale(self):
        same = overlay.stamp(entry(id="same"), self.plugin)
        learned = overlay.stamp(entry(id="learned", text="Name the failing test in the reply."), self.plugin)
        gone = overlay.stamp(entry(id="gone", target={"file": DEV_FILE, "heading": "Commits"}), self.plugin)
        for e in (same, learned, gone):
            e["status"] = "active"
        new_text = DEV_TEXT.replace("Run the tests before you reply.",
                                    "Run the tests before you reply. Name the failing test in the reply.")
        new_text = new_text.replace("## Commits\n\nOne commit per task.\n", "")
        plugin2 = make_plugin(Path(os.path.realpath(self.tmp.name)) / "v2", version="1.1.0", dev_text=new_text)
        entries, changed = overlay.refresh([same, learned, gone], plugin2)
        self.assertTrue(changed)
        status = {e["id"]: e["status"] for e in entries}
        self.assertEqual(status["learned"], "retired")
        self.assertEqual(status["gone"], "stale")
        self.assertEqual(status["same"], "stale")

    def test_refresh_keeps_entry_when_its_section_is_unchanged(self):
        e = overlay.stamp(entry(), self.plugin)
        e["status"] = "active"
        plugin2 = make_plugin(Path(os.path.realpath(self.tmp.name)) / "v2", version="1.1.0")
        entries, changed = overlay.refresh([e], plugin2)
        self.assertTrue(changed)
        self.assertEqual(entries[0]["status"], "active")
        self.assertEqual(entries[0]["plugin_version"], "1.1.0")

    def test_render_lists_active_entries_only(self):
        text = overlay.render([{"id": "a", "role": "all", "text": "Do A.", "status": "active"},
                               {"id": "b", "role": "all", "text": "Do B.", "status": "stale"}])
        self.assertIn("[all] Do A. (overlay a)", text)
        self.assertNotIn("Do B.", text)
        self.assertEqual(overlay.render([]), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_retro_check -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'overlay'`.

- [ ] **Step 3: Write `hooks/overlay.py`**

```python
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


def anchor(section):
    return hashlib.sha256(section.encode("utf-8")).hexdigest()[:16]


def norm(text):
    return re.sub(r"\s+", " ", str(text)).strip().lower()


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_retro_check -v`
Expected: all `OverlayUnitTests` PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/overlay.py tests/test_retro_check.py
git commit -m "Retro Engine: overlay state and entry check"
```

---

### Task 2: `scripts/retro-scan`, discovery and measurement

**Files:**
- Create: `scripts/retro-scan`
- Test: `tests/test_retro_scan.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for scripts/retro-scan on fixture transcripts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "retro-scan"
NOW = time.time()
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main"]


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def user(text, t, cwd, branch="main"):
    return {"type": "user", "timestamp": iso(t), "cwd": cwd, "gitBranch": branch,
            "message": {"role": "user", "content": text}}


def bash(call_id, cmd, t, cwd, off=False, branch="main"):
    tool_input = {"command": cmd}
    if off:
        tool_input["dangerouslyDisableSandbox"] = True
    return {"type": "assistant", "timestamp": iso(t), "cwd": cwd, "gitBranch": branch,
            "message": {"content": [{"type": "tool_use", "id": call_id, "name": "Bash", "input": tool_input}]}}


def result(call_id, text, t, cwd, error=True, branch="main"):
    return {"type": "user", "timestamp": iso(t), "cwd": cwd, "gitBranch": branch,
            "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": error}]}}


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-scan-")
        self.base = Path(os.path.realpath(self.tmp.name))
        self.projects = self.base / "projects"
        self.state = self.base / "state"
        self.repo = self.base / "work" / "app"
        (self.repo / "docs" / "factory").mkdir(parents=True)
        (self.repo / "docs" / "factory" / "status.md").write_text("# Status\n")
        (self.base / "work" / "notes").mkdir(parents=True)
        self.count = 0

    def session(self, cwd, rows, subagents=()):
        self.count += 1
        folder = self.projects / ("-" + str(cwd).strip("/").replace("/", "-"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"s{self.count}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        for k, (meta, sub_rows) in enumerate(subagents):
            sub = folder / f"s{self.count}" / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"agent-{k}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sub_rows))
            (sub / f"agent-{k}.meta.json").write_text(json.dumps(meta))
        return path

    def env(self, **extra):
        env = dict(os.environ, CLAUDE_PROJECTS_DIR=str(self.projects), SOFTWARE_FACTORY_HOME=str(self.state),
                   CLAUDE_USER_SETTINGS=str(self.state / "settings.json"))
        env.update(extra)
        return env

    def run(self, *args, **extra):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=self.env(**extra))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.tmp.cleanup()

    def test_groups_worktrees_under_the_parent_and_skips_non_factory_folders(self):
        repo = str(self.f.repo)
        worktree = repo + "/.claude/worktrees/fix-login"
        self.f.session(repo, [user("start", NOW - 60, repo)])
        self.f.session(worktree, [user("go", NOW - 30, worktree)])
        self.f.session(str(self.f.base / "work" / "notes"), [user("hi", NOW - 30, str(self.f.base / "work" / "notes"))])
        found = self.f.run("--discover")
        self.assertEqual([p["project"] for p in found], [repo])
        self.assertEqual(found[0]["sessions"], 2)
        self.assertEqual(found[0]["worktrees"], [worktree])


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.repo = str(self.f.repo)

    def tearDown(self):
        self.f.tmp.cleanup()

    def measure(self, *extra):
        return self.f.run("--project", self.repo, "--since", iso(NOW - 3600), "--no-github", *extra)

    def test_surfaces_the_known_root_causes(self):
        r, t = self.repo, NOW - 1800
        rows = [
            user("no, don't debug flaky tests again, quarantine them", t, r),
            bash("a", "rtk gh pr create --title x", t + 1, r),
            result("a", "x509: certificate signed by unknown authority", t + 2, r),
            bash("b", "rtk gh pr create --title x", t + 3, r, off=True),
            result("b", "https://github.com/o/r/pull/1", t + 4, r, error=False),
            bash("c", "cd web && npm test", t + 5, r),
            result("c", "PreToolUse:Bash hook error: Blocked: cd X && cmd chains do not match allow rules", t + 6, r),
            bash("d", "python3 /private/tmp/claude-501/x/scratchpad/run_tests.py", t + 7, r, off=True),
            result("d", "ok", t + 8, r, error=False),
            user("perfect, thanks", t + 9, r),
        ]
        dev = {"agentType": "software-factory:software-developer", "model": "sonnet"}
        sub_rows = [bash("s1", "npm test", t + 10, r), result("s1", "FAIL src/a.test.ts", t + 11, r),
                    bash("s2", "npm test", t + 300, r), bash("s3", "git status", t + 610, r)]
        self.f.session(r, rows, subagents=[(dev, sub_rows)])
        m = self.measure()
        self.assertEqual(m["sessions"], 1)
        self.assertEqual(m["sandbox_off"].get("rtk gh"), 1)
        self.assertEqual(m["sandbox_off"].get("python3 <script>"), 1)
        self.assertEqual(m["errors_by_class"].get("sandbox"), 1)
        self.assertEqual(m["errors_by_class"].get("hook-block"), 1)
        self.assertTrue(any("cd X" in k for k in m["hook_blocks"]), m["hook_blocks"])
        self.assertEqual(m["gh_writes"], {"sandboxed": 1, "sandbox-off": 1})
        self.assertEqual(m["user_message_counts"], {"correction": 1, "praise": 1})
        self.assertEqual(len(m["user_messages"]["correction"]), 1)
        dev_row = m["dispatches"]["software-factory:software-developer"]
        self.assertEqual((dev_row["n"], dev_row["calls_per"], dev_row["errors_per"]), (1, 3.0, 1.0))
        self.assertEqual(dev_row["median_min"], 10.0)
        self.assertEqual(m["headline"]["corrections"], 1)
        self.assertEqual(m["github"], {"error": "skipped"})

    def test_window_and_branch_filters(self):
        r = self.repo
        self.f.session(r, [user("no, wrong file", NOW - 7200, r),           # before the window
                           user("no, wrong branch", NOW - 600, r, branch="dev"),
                           user("no, stop", NOW - 300, r)])
        self.assertEqual(self.measure()["user_message_counts"], {"correction": 2})
        self.assertEqual(self.measure("--branch", "dev")["user_message_counts"], {"correction": 1})

    def test_counts_ask_rule_hits(self):
        self.f.state.mkdir(parents=True)
        (self.f.state / "settings.json").write_text(json.dumps({"permissions": {"ask": ["Bash(git push:*)"]}}))
        r = self.repo
        self.f.session(r, [bash("a", "git push origin feature/x", NOW - 100, r)])
        self.assertEqual(self.measure()["ask_hits"], {"git push*": 1})

    def test_github_metrics_from_gh(self):
        bin_dir = self.f.base / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text("#!/bin/sh\n"
                        "if [ \"$1\" = pr ]; then echo '[{\"number\":1,\"reviews\":[{\"body\":\"nit\"}],\"comments\":[{\"body\":\"why?\"}]}]'; "
                        f"else echo '[{{\"createdAt\":\"{iso(NOW - 900)}\",\"updatedAt\":\"{iso(NOW - 300)}\",\"conclusion\":\"failure\"}}]'; fi\n")
        fake.chmod(0o755)
        r = self.repo
        self.f.session(r, [user("go", NOW - 100, r)])
        m = self.f.run("--project", r, "--since", iso(NOW - 3600), PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.assertEqual(m["github"]["merged_prs"], 1)
        self.assertEqual(m["github"]["review_comments"], 2)
        self.assertEqual((m["github"]["ci_runs"], m["github"]["ci_failed"], m["github"]["ci_median_min"]), (1, 1, 10.0))

    def test_github_failure_is_reported_not_fatal(self):
        bin_dir = self.f.base / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
        (bin_dir / "gh").chmod(0o755)
        r = self.repo
        self.f.session(r, [user("go", NOW - 100, r)])
        m = self.f.run("--project", r, "--since", iso(NOW - 3600), PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.assertIn("not logged in", m["github"]["error"])


class DueTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.repo = str(self.f.repo)
        origin = self.f.base / "origin.git"
        run = lambda *a: subprocess.run([*GIT, *a], check=True, capture_output=True)  # noqa: E731
        run("init", "--bare", str(origin))
        run("-C", self.repo, "init")
        run("-C", self.repo, "add", ".")
        run("-C", self.repo, "commit", "-m", "init")
        run("-C", self.repo, "remote", "add", "origin", str(origin))
        run("-C", self.repo, "push", "origin", "HEAD:main")
        self.sha = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        self.f.tmp.cleanup()

    def test_due_needs_two_sessions_and_a_moved_main(self):
        r = self.repo
        self.f.session(r, [user("one", NOW - 100, r)])
        self.assertEqual(self.f.run("--due"), [])
        self.f.session(r, [user("two", NOW - 50, r)])
        due = self.f.run("--due")
        self.assertEqual([(d["project"], d["sha"], d["sessions"]) for d in due], [(r, self.sha, 2)])
        self.f.state.mkdir(parents=True, exist_ok=True)
        (self.f.state / "retro-metrics.jsonl").write_text(
            json.dumps({"project": r, "sha": self.sha, "until": iso(NOW - 3600)}) + "\n")
        self.assertEqual(self.f.run("--due"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_retro_scan -v`
Expected: FAIL (`can't open file '.../scripts/retro-scan'`).

- [ ] **Step 3: Write `scripts/retro-scan`**

```python
#!/usr/bin/env python3
"""Retro scan: measure Software Factory sessions from Claude Code transcripts. Read-only, no model calls.

Usage:
  retro-scan --discover        factory projects seen in the last 30 days, with session counts and worktrees
  retro-scan --due             projects due for a retro: origin main moved since the last retro and 2+ sessions
  retro-scan --project PATH [--since ISO] [--branch NAME] [--out FILE] [--no-github]
                               metrics for one project since its last retro (default: the last 7 days)

A factory project is a folder holding docs/factory/status.md. Sessions in its worktrees (.claude/worktrees/*,
.worktrees/*, or a live worktree anywhere) count for the parent repo. Other sessions are never read.

Env: CLAUDE_PROJECTS_DIR (default ~/.claude/projects), SOFTWARE_FACTORY_HOME (default ~/.claude/software-factory),
CLAUDE_USER_SETTINGS (default ~/.claude/settings.json, read for ask rules). Stdlib only, Python 3.9+.
"""

import argparse
import collections
import datetime
import fnmatch
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import overlay  # noqa: E402

DAY = 86400
DISCOVER_DAYS = 30
DEFAULT_DAYS = 7
TOP = 15
SAMPLES = {"correction": 20, "preference": 12, "praise": 8}
COUNTERS = ("errors", "errors_by_tool", "hook_blocks", "sandbox_off", "ask_hits", "approvals", "gh_writes")
WORKTREE = re.compile(r"/(?:\.claude/worktrees|\.worktrees)/[^/]+")
GH_WRITE = re.compile(
    r"\b(gh\s+(pr\s+(create|merge|edit|close|comment|review|ready)|issue\s+(create|edit|close|comment|reopen)|"
    r"project\s+(item-add|item-edit|item-create|create|field-create|link)|release\s+create|label\s+create|"
    r"api\s+(-X|--method)\s*(POST|PATCH|PUT|DELETE)|repo\s+(create|edit|rename)|workflow\s+run|run\s+rerun)|"
    r"git\s+(-C\s+\S+\s+)?(-c\s+\S+\s+)?push)\b")
ERR_CLASS = [
    ("hook-block", re.compile(r"hook|blocked by|PreToolUse", re.I)),
    ("user-rejected", re.compile(r"doesn't want to proceed|user rejected|rejected by user", re.I)),
    ("classifier-denied", re.compile(r"auto mode classifier", re.I)),
    ("sandbox", re.compile(r"Operation not permitted|sandbox_violations|x509|LSOpenURLs|-10822|Read-only file system", re.I)),
    ("edit-mismatch", re.compile(r"String to replace not found|old_string|File has not been read|modified since read", re.I)),
    ("file-missing", re.compile(r"does not exist|No such file|ENOENT", re.I)),
    ("timeout", re.compile(r"timed out|timeout", re.I)),
    ("gh-api", re.compile(r"HTTP 4\d\d|GraphQL|gh: |rate limit", re.I)),
    ("test-or-build-fail", re.compile(r"FAIL|failed|AssertionError|Error:|exit code [1-9]", re.I)),
]
CORRECTION = re.compile(r"\b(no|don't|dont|do not|stop|why did|why are|wrong|instead|again|not what|you forgot|"
                        r"never|shouldn't|should not|too slow|too long|waste)\b", re.I)
PREFERENCE = re.compile(r"\b(i prefer|i want|i'd rather|from now on|always|please use|let's use)\b", re.I)
PRAISE = re.compile(r"\b(great|perfect|thanks|thank you|nice|good job|well done|love it|works now)\b", re.I)
HOOK_REASON = re.compile(r"(Blocked: [^.(\n]{0,80}|Software Factory[^:\n]*: [^.\n]{0,80})")
RUNNERS = ("python", "python3", "bash", "sh", "zsh", "node")


def projects_dir():
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR") or Path.home() / ".claude" / "projects")


def stamp(value):
    try:
        t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.timestamp()


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(timespec="seconds")


def records(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                yield d


def session_cwd(path):
    for i, d in enumerate(records(path)):
        if d.get("cwd"):
            return d["cwd"]
        if i > 50:
            return None
    return None


def project_root(cwd):
    """The factory project a session's cwd belongs to, or None. Worktree sessions map to the parent repo."""
    if not cwd:
        return None
    start = Path(WORKTREE.split(cwd, 1)[0])
    for folder in (start, *start.parents):
        if (folder / "docs" / "factory" / "status.md").is_file():
            return str(folder)
    # shortcut: a removed worktree outside .claude/worktrees or .worktrees is not mapped; add a pattern if one shows up
    git_file = Path(cwd) / ".git"
    if git_file.is_file():
        m = re.match(r"gitdir:\s*(.+)/\.git/worktrees/", git_file.read_text(errors="ignore"))
        if m and (Path(m.group(1)) / "docs" / "factory" / "status.md").is_file():
            return m.group(1)
    return None


def main_sessions(newer_than):
    """(path, cwd, project) for each main-session transcript of a factory project modified after newer_than."""
    for path in sorted(projects_dir().glob("*/*.jsonl")):
        if path.stat().st_mtime < newer_than:
            continue
        cwd = session_cwd(path)
        root = project_root(cwd)
        if root:
            yield path, cwd, root


def subagent_files(session):
    return sorted((session.parent / session.stem / "subagents").glob("*.jsonl"))


def meta_of(sub):
    try:
        return json.loads(sub.with_suffix(".meta.json").read_text())
    except (OSError, ValueError):
        return {}


def discover(newer_than=None):
    if newer_than is None:
        newer_than = time.time() - DISCOVER_DAYS * DAY
    found = {}
    for path, cwd, root in main_sessions(newer_than):
        p = found.setdefault(root, {"project": root, "sessions": 0, "worktrees": set(), "last_active": 0.0})
        p["sessions"] += 1
        p["last_active"] = max(p["last_active"], path.stat().st_mtime)
        if cwd != root and not cwd.startswith(root + os.sep):
            p["worktrees"].add(cwd)
        elif WORKTREE.search(cwd):
            p["worktrees"].add(WORKTREE.split(cwd, 1)[0] + WORKTREE.search(cwd).group(0))
    return [dict(p, worktrees=sorted(p["worktrees"]), last_active=iso(p["last_active"]))
            for _, p in sorted(found.items())]


def classify(text):
    for name, rx in ERR_CLASS:
        if rx.search(text[:3000]):
            return name
    return "other"


def shape(cmd):
    """A short, path-free label for a command: program and subcommand, '<script>' for scripts, '(chain)' for chains."""
    s = cmd.strip()
    if re.match(r"cd\s+\S+\s*(&&|;)", s):
        return "cd-chain"
    s = re.sub(r"^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", s)
    words = s.split()
    if not words:
        return "empty"
    prog = os.path.basename(words[0])
    if prog in RUNNERS and len(words) > 1 and "/" in words[1]:
        label = f"{prog} <script>"
    elif len(words) > 1 and re.match(r"^[a-z][\w-]*$", words[1]):
        label = f"{prog} {words[1]}"
    else:
        label = prog
    return label + (" (chain)" if re.search(r"&&|;|\$\(|\bdo\b", s) else "")


def load_ask_globs():
    path = Path(os.environ.get("CLAUDE_USER_SETTINGS") or Path.home() / ".claude" / "settings.json")
    try:
        ask = (json.loads(path.read_text()).get("permissions") or {}).get("ask") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [r[5:-1].replace(":*", "*") for r in ask if isinstance(r, str) and r.startswith("Bash(") and r.endswith(")")]


def ask_match(cmd, globs):
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        seg = re.sub(r"^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", seg.strip())
        for g in globs:
            if fnmatch.fnmatchcase(seg, g):
                return g
    return None


def hook_reason(text):
    m = HOOK_REASON.search(text)
    return re.sub(r"\s+", " ", m.group(1) if m else text[:80]).strip()


def scan_file(path, since, until, branch=None, globs=(), sub=False):
    r = {"active": False, "first": None, "last": None, "tool_calls": 0, "messages": []}
    r.update({k: collections.Counter() for k in COUNTERS})
    calls = {}
    for d in records(path):
        t = stamp(d.get("timestamp"))
        if t is None or t < since or t > until:
            continue
        if branch and d.get("gitBranch") not in (None, branch):
            continue
        r["active"] = True
        r["first"] = r["first"] or t
        r["last"] = t
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "user" and not sub:
            texts = [content] if isinstance(content, str) else [
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
            r["messages"] += [x.strip() for x in texts if x and not x.lstrip().startswith("<") and len(x) < 600]
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                name, tool_input = c.get("name") or "?", c.get("input") or {}
                r["tool_calls"] += 1
                cmd = tool_input.get("command", "") if name == "Bash" else ""
                calls[c.get("id")] = (name, cmd)
                if name == "Bash":
                    off = bool(tool_input.get("dangerouslyDisableSandbox"))
                    if off:
                        r["sandbox_off"][shape(cmd)] += 1
                    rule = ask_match(cmd, globs)
                    if rule:
                        r["ask_hits"][rule] += 1
                    if GH_WRITE.search(cmd):
                        r["gh_writes"]["sandbox-off" if off else ("ask-rule" if rule else "sandboxed")] += 1
                elif name.startswith("mcp__github__") and re.search(r"write|add_|create|update|merge|push", name):
                    r["gh_writes"]["mcp"] += 1
            elif c.get("type") == "tool_result":
                name, _ = calls.get(c.get("tool_use_id"), ("?", ""))
                text = c.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                if c.get("is_error") or "doesn't want to proceed" in text:
                    cls = classify(text)
                    r["errors"][cls] += 1
                    r["errors_by_tool"][name] += 1
                    if cls in ("user-rejected", "classifier-denied"):
                        r["approvals"][cls] += 1
                    if cls == "hook-block":
                        r["hook_blocks"][hook_reason(text)] += 1
    return r


def p90(sorted_values):
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * 0.9))]


def dispatch_table(dispatches):
    table = {}
    for agent_type, runs in sorted(dispatches.items()):
        mins = sorted(run["minutes"] for run in runs)
        n = len(runs)
        table[agent_type] = {
            "n": n, "models": dict(collections.Counter(run["model"] for run in runs)),
            "median_min": round(statistics.median(mins), 1), "p90_min": round(p90(mins), 1),
            "calls_per": round(sum(run["calls"] for run in runs) / n, 1),
            "errors_per": round(sum(run["errors"] for run in runs) / n, 1)}
    return table


def message_kind(text):
    for name, rx in (("correction", CORRECTION), ("preference", PREFERENCE), ("praise", PRAISE)):
        if rx.search(text):
            return name
    return None


def gh(project, *args):
    r = subprocess.run(["gh", *args], cwd=project, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError((r.stderr or "gh failed").strip())
    return json.loads(r.stdout or "[]")


def github(project, since):
    """Merged PRs with review comments and main CI run times since `since`, or {'error': reason}."""
    day = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).date().isoformat()
    try:
        prs = gh(project, "pr", "list", "--state", "merged", "--search", f"merged:>={day}", "--limit", "50",
                 "--json", "number,reviews,comments")
        runs = gh(project, "run", "list", "--branch", "main", "--limit", "50", "--json",
                  "createdAt,updatedAt,conclusion")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)[:200]}
    bodies = [str(x.get("body", ""))[:200] for pr in prs
              for x in (pr.get("reviews") or []) + (pr.get("comments") or []) if x.get("body")]
    durations, failed = [], 0
    for run in runs:
        start, end = stamp(run.get("createdAt")), stamp(run.get("updatedAt"))
        if start is None or start < since:
            continue
        if end:
            durations.append((end - start) / 60)
        if run.get("conclusion") == "failure":
            failed += 1
    durations.sort()
    return {"merged_prs": len(prs), "review_comments": len(bodies), "review_samples": bodies[:10],
            "ci_runs": len(durations), "ci_failed": failed,
            "ci_median_min": round(statistics.median(durations), 1) if durations else None,
            "ci_p90_min": round(p90(durations), 1) if durations else None}


def measure(project, since, until=None, branch=None, with_github=True):
    until = until or time.time()
    globs = load_ask_globs()
    total = {k: collections.Counter() for k in COUNTERS}
    sessions = tool_calls = sub_errors = 0
    messages, dispatches = [], collections.defaultdict(list)
    for path, _cwd, root in main_sessions(since):
        if root != project:
            continue
        main = scan_file(path, since, until, branch, globs)
        runs = [main] if main["active"] else []
        sessions += 1 if main["active"] else 0
        for sub in subagent_files(path):
            s = scan_file(sub, since, until, branch, globs, sub=True)
            if not s["active"]:
                continue
            runs.append(s)
            errors = sum(s["errors"].values())
            sub_errors += errors
            meta = meta_of(sub)
            dispatches[meta.get("agentType") or "unknown"].append({
                "model": meta.get("model") or "inherit", "minutes": (s["last"] - s["first"]) / 60,
                "calls": s["tool_calls"], "errors": errors})
        for run in runs:
            tool_calls += run["tool_calls"]
            for k in COUNTERS:
                total[k].update(run[k])
        messages += main["messages"]
    counts, samples = collections.Counter(), {k: [] for k in SAMPLES}
    for text in messages:
        kind = message_kind(text)
        if kind:
            counts[kind] += 1
            if len(samples[kind]) < SAMPLES[kind]:
                samples[kind].append(text[:300])
    errors = sum(total["errors"].values())
    top = lambda c: dict(c.most_common(TOP))  # noqa: E731
    return {
        "project": project, "window": {"since": iso(since), "until": iso(until), "branch": branch},
        "sessions": sessions, "tool_calls": tool_calls,
        "errors_by_class": dict(total["errors"]), "errors_by_tool": top(total["errors_by_tool"]),
        "subagent_error_share": round(sub_errors / errors, 2) if errors else 0.0,
        "hook_blocks": top(total["hook_blocks"]), "sandbox_off": top(total["sandbox_off"]),
        "ask_hits": top(total["ask_hits"]), "approvals": dict(total["approvals"]), "gh_writes": dict(total["gh_writes"]),
        "dispatches": dispatch_table(dispatches), "user_message_counts": dict(counts), "user_messages": samples,
        "headline": {"sessions": sessions, "tool_calls": tool_calls, "errors": errors,
                     "hook_blocks": sum(total["hook_blocks"].values()), "sandbox_off": sum(total["sandbox_off"].values()),
                     "ask_hits": sum(total["ask_hits"].values()), "corrections": counts["correction"],
                     "dispatches": sum(len(v) for v in dispatches.values())},
        "github": github(project, since) if with_github else {"error": "skipped"},
    }


def last_retro(project):
    rows = [r for r in overlay.read_jsonl(overlay.history_file()) if r.get("project") == project]
    return rows[-1] if rows else None


def remote_main(project):
    try:
        r = subprocess.run(["git", "-C", project, "ls-remote", "origin", "refs/heads/main"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = r.stdout.split()
    return words[0] if r.returncode == 0 and words else None


def window_start(project, since_arg=None):
    if since_arg:
        return stamp(since_arg)
    last = last_retro(project)
    start = stamp(last.get("until")) if last else None
    return start if start is not None else time.time() - DEFAULT_DAYS * DAY


def due():
    out = []
    for p in discover():
        root = p["project"]
        sha = remote_main(root)
        last = last_retro(root)
        if not sha or (last and last.get("sha") == sha):
            continue
        since = window_start(root)
        n = sum(1 for _path, _cwd, r in main_sessions(since) if r == root)
        if n >= 2:
            out.append({"project": root, "sha": sha, "since": iso(since), "sessions": n})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure Software Factory sessions for the Retro Engine.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover", action="store_true")
    mode.add_argument("--due", action="store_true")
    mode.add_argument("--project")
    ap.add_argument("--since")
    ap.add_argument("--branch")
    ap.add_argument("--out")
    ap.add_argument("--no-github", action="store_true")
    a = ap.parse_args(argv)
    if a.discover:
        result = discover()
    elif a.due:
        result = due()
    else:
        root = os.path.realpath(a.project)
        since = window_start(root, a.since)
        if since is None:
            ap.error(f"--since is not an ISO time: {a.since}")
        result = measure(root, since, branch=a.branch, with_github=not a.no_github)
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(a.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Then: `chmod +x scripts/retro-scan`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_retro_scan -v`
Expected: all PASS. If `test_surfaces_the_known_root_causes` fails on `median_min`, check the subagent rows span exactly 600 seconds (`t + 10` to `t + 610`).

- [ ] **Step 5: Run against real transcripts as a smoke check (read-only)**

Run: `python3 scripts/retro-scan --discover`
Expected: a JSON list of the factory projects on this machine with session counts. Do not paste the output anywhere public.

- [ ] **Step 6: Commit**

```bash
git add scripts/retro-scan tests/test_retro_scan.py
git commit -m "Retro Engine: retro-scan discovers, schedules and measures factory projects"
```

---

### Task 3: `scripts/retro-check`, the overlay writer and history

**Files:**
- Create: `scripts/retro-check`
- Modify: `tests/test_retro_check.py` (append the CLI tests below before the `if __name__` block)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retro_check.py`:

```python
class CheckCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-check-")
        self.base = Path(os.path.realpath(self.tmp.name))
        self.plugin = make_plugin(self.base)
        self.state = self.base / "state"
        self.project = self.base / "work" / "acme-shop"
        (self.project / "docs" / "factory").mkdir(parents=True)
        self.env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(self.plugin), SOFTWARE_FACTORY_HOME=str(self.state),
                        CLAUDE_PROJECTS_DIR=str(self.base / "no-transcripts"))

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, *args, code=0):
        r = subprocess.run([sys.executable, str(CHECK), *map(str, args)], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def write(self, name, rows):
        path = self.base / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def rows(self, name):
        return overlay.read_jsonl(self.state / "overlay" / f"{name}.jsonl")

    def test_auto_fix_applies_accepted_and_holds_asks(self):
        cands = self.write("c.jsonl", [entry(id="ok"), entry(id="sec", text="Add npm test to the allow rules."),
                                       entry(id="bad", text="Skip tests when in a hurry.")])
        out = self.check("apply", cands, "--mode", "auto-fix")
        self.assertEqual([(r["id"], r["placed"]) for r in out], [("ok", "active"), ("sec", "pending"), ("bad", "none")])
        self.assertEqual([e["id"] for e in self.rows("entries")], ["ok"])
        self.assertEqual(self.rows("entries")[0]["plugin_version"], "1.0.0")
        self.assertEqual([e["id"] for e in self.rows("pending")], ["sec"])

    def test_draft_mode_is_the_default_and_holds_everything(self):
        out = self.check("apply", self.write("c.jsonl", [entry()]))
        self.assertEqual(out[0]["placed"], "pending")
        self.assertEqual(self.rows("entries"), [])

    def test_duplicate_id_is_rejected(self):
        self.check("apply", self.write("c.jsonl", [entry()]), "--mode", "auto-fix")
        out = self.check("apply", self.write("d.jsonl", [entry()]), "--mode", "auto-fix")
        self.assertEqual(out[0]["verdict"], "reject")

    def test_approve_drop_and_retire(self):
        self.check("apply", self.write("c.jsonl", [entry(id="a"), entry(id="b")]))
        self.check("approve", "a")
        self.check("drop", "b")
        self.assertEqual([e["id"] for e in self.rows("entries")], ["a"])
        self.assertEqual(self.rows("pending"), [])
        self.check("retire", "a")
        self.assertEqual(self.rows("entries")[0]["status"], "retired")
        self.check("approve", "missing", code=1)

    def metrics(self, **headline):
        path = self.base / "m.json"
        path.write_text(json.dumps({"sessions": 2, "window": {"since": "2026-11-13T00:00:00+00:00",
                                                              "until": "2026-11-14T00:00:00+00:00"},
                                    "headline": dict({"errors": 10}, **headline), "sandbox_off": {"rtk gh": 6}}))
        return path

    def test_publish_copies_report_records_baseline_and_verdicts_close_the_loop(self):
        report = self.base / "r.md"
        report.write_text("# Retro\n\n3 sandbox-off gh calls per session.\n")
        changes = self.base / "ch.json"
        changes.write_text(json.dumps([{"id": "ok", "lesson": "gh-wrapper", "level": "user", "metric": "sandbox_off.rtk gh"}]))
        self.check("apply", self.write("c.jsonl", [entry(id="ok")]), "--mode", "auto-fix")
        out = self.check("publish", report, "--project", self.project, "--sha", "abcdef1234", "--metrics", self.metrics(),
                         "--changes", changes)
        dest = self.project / "docs" / "factory" / "retros" / "2026-11-14-abcdef1.md"
        self.assertEqual(out["path"], str(dest))
        self.assertTrue(dest.is_file())
        row = overlay.read_jsonl(self.state / "retro-metrics.jsonl")[-1]
        self.assertEqual((row["sha"], row["changes"][0]["baseline"]), ("abcdef1234", 3.0))
        worse = self.base / "m2.json"
        worse.write_text(json.dumps({"sessions": 1, "sandbox_off": {"rtk gh": 4}, "headline": {}}))
        verdicts = self.check("verdicts", "--project", self.project, "--metrics", worse)
        self.assertEqual([(v["id"], v["verdict"]) for v in verdicts], [("ok", "reverted")])
        self.assertEqual(self.rows("entries")[0]["status"], "retired")

    def test_publish_refuses_a_report_with_a_home_path(self):
        report = self.base / "r.md"
        report.write_text("Seen in /Users/jane/work/app.\n")
        changes = self.base / "ch.json"
        changes.write_text("[]")
        out = self.check("publish", report, "--project", self.project, "--sha", "abc1234", "--metrics", self.metrics(),
                         "--changes", changes, code=1)
        self.assertFalse(out["published"])
        self.assertFalse((self.project / "docs" / "factory" / "retros").exists())

    def test_scrub_strict_catches_project_names_and_private_words(self):
        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "retro-metrics.jsonl").write_text(json.dumps({"project": str(self.project)}) + "\n")
        (self.state / "private-words.txt").write_text("Globex\n")
        draft = self.base / "draft.md"
        draft.write_text("Seen in acme-shop for Globex, mail jane@example.com.\nThe software-factory plugin.\n")
        self.assertEqual(len(self.check("scrub", draft, code=1)["hits"]), 1)
        hits = self.check("scrub", draft, "--strict", code=1)["hits"]
        self.assertEqual(sorted(h["match"].lower() for h in hits), ["acme-shop", "globex", "jane@example.com"])
        clean = self.base / "clean.md"
        clean.write_text("Seen in 3 projects.\n")
        self.assertEqual(self.check("scrub", clean, "--strict")["hits"], [])

    def test_lessons_count_projects_and_kept_changes(self):
        self.state.mkdir(parents=True, exist_ok=True)
        rows = [{"project": "/p/a", "changes": [{"id": "x1", "lesson": "gh-wrapper"}]},
                {"project": "/p/b", "changes": [{"id": "x2", "lesson": "gh-wrapper"}]},
                {"project": "/p/a", "changes": [], "previous": [{"id": "x1", "verdict": "kept"}]}]
        (self.state / "retro-metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(self.check("lessons"), {"gh-wrapper": {"projects": 2, "changes": 2, "kept": 1}})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_retro_check -v`
Expected: `CheckCliTests` FAIL (`can't open file '.../scripts/retro-check'`), `OverlayUnitTests` still PASS.

- [ ] **Step 3: Write `scripts/retro-check`**

```python
#!/usr/bin/env python3
"""Retro Engine checks, and the only writer of the overlay and the retro history. Prints JSON.

  retro-check apply CANDIDATES [--mode draft|auto-fix]   check each candidate entry (JSONL). Accepted entries go
                                                        active in auto-fix mode; every other entry that passes waits
                                                        in pending. Mode defaults to retro-settings.json, else draft.
  retro-check approve ID | drop ID | retire ID          after the user's answer; approve re-checks the entry
  retro-check verdicts --project P --metrics FILE       kept or reverted for the previous retro's changes, and
                                                        retires reverted overlay entries
  retro-check lessons                                   projects, changes and kept counts per lesson key
  retro-check scrub FILE... [--strict]                  exit 1 if a file holds a home path or an email; --strict
                                                        also flags discovered project names and private-words.txt
  retro-check publish REPORT --project P --sha SHA --metrics FILE --changes FILE [--verdicts FILE]
                                                        scrub the report, copy it to <P>/docs/factory/retros/ and
                                                        append the history row
Stdlib only, Python 3.9+.
"""

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import overlay  # noqa: E402

HOME_PATH = re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PUBLIC_NAMES = {"software-factory", "open-software-factory"}


def entries():
    return overlay.read_jsonl(overlay.overlay_file("entries"))


def pending():
    return overlay.read_jsonl(overlay.overlay_file("pending"))


def active(rows):
    return [e for e in rows if e.get("status") == "active"]


def cmd_apply(path, mode):
    mode = mode or overlay.settings().get("mode") or "draft"
    current, waiting = entries(), pending()
    ids = {e.get("id") for e in current + waiting}
    results = []
    for cand in overlay.read_jsonl(path):
        if cand.get("id") in ids:
            results.append({"id": cand.get("id"), "verdict": "reject", "reason": "id already in use", "placed": "none"})
            continue
        verdict, reason = overlay.verdict(cand, active(current))
        placed = "none"
        if verdict != "reject":
            e = overlay.stamp(cand)
            if verdict == "accept" and mode == "auto-fix":
                e["status"], placed = "active", "active"
                current.append(e)
            else:
                e["status"], e["reason"], placed = "pending", reason, "pending"
                waiting.append(e)
            ids.add(cand["id"])
        results.append({"id": cand.get("id"), "verdict": verdict, "reason": reason, "placed": placed})
    overlay.write_jsonl(overlay.overlay_file("entries"), current)
    overlay.write_jsonl(overlay.overlay_file("pending"), waiting)
    return results, 0


def cmd_approve(entry_id):
    current, waiting = entries(), pending()
    found = [e for e in waiting if e.get("id") == entry_id]
    if not found:
        return {"error": f"no pending entry {entry_id}"}, 1
    verdict, reason = overlay.verdict(found[0], active(current))
    if verdict == "reject":  # the user's yes covers an ask, never a reject
        return {"error": reason}, 1
    e = overlay.stamp(found[0])
    e["status"] = "active"
    e.pop("reason", None)
    overlay.write_jsonl(overlay.overlay_file("entries"), current + [e])
    overlay.write_jsonl(overlay.overlay_file("pending"), [x for x in waiting if x.get("id") != entry_id])
    return {"id": entry_id, "status": "active"}, 0


def cmd_drop(entry_id):
    waiting = pending()
    if not any(e.get("id") == entry_id for e in waiting):
        return {"error": f"no pending entry {entry_id}"}, 1
    overlay.write_jsonl(overlay.overlay_file("pending"), [e for e in waiting if e.get("id") != entry_id])
    return {"id": entry_id, "status": "dropped"}, 0


def retire(entry_id, reason):
    rows, done = entries(), False
    for e in rows:
        if e.get("id") == entry_id and e.get("status") in ("active", "stale"):
            e["status"], e["reason"], done = "retired", reason, True
    if done:
        overlay.write_jsonl(overlay.overlay_file("entries"), rows)
    return done


def cmd_retire(entry_id):
    if not retire(entry_id, "retired by the user"):
        return {"error": f"no active or stale entry {entry_id}"}, 1
    return {"id": entry_id, "status": "retired"}, 0


def metric_value(metrics, name):
    """A metric per session: a headline key, or 'section.key' such as 'sandbox_off.rtk gh'. None if unknown."""
    sessions = max(1, metrics.get("sessions") or 0)
    headline = metrics.get("headline") or {}
    if name in headline:
        value = headline[name]
    else:
        section, _, key = str(name).partition(".")
        table = metrics.get(section)
        if not isinstance(table, dict) or not key:
            return None
        value = table.get(key, 0)
    if not isinstance(value, (int, float)):
        return None
    return round(value / sessions, 3)


def cmd_verdicts(project, metrics_path):
    rows = [r for r in overlay.read_jsonl(overlay.history_file()) if r.get("project") == project]
    if not rows:
        return [], 0
    metrics = json.loads(Path(metrics_path).read_text())
    out = []
    for ch in rows[-1].get("changes") or []:
        baseline = ch.get("baseline")
        if baseline is None or not ch.get("metric"):
            continue
        now = metric_value(metrics, ch["metric"])
        if now is None:
            verdict = "unknown"
        else:
            verdict = "kept" if now < baseline or now == baseline == 0 else "reverted"  # lower is better for all
        if verdict == "reverted":
            retire(ch.get("id"), "retro: its metric did not improve")
        out.append({"id": ch.get("id"), "metric": ch["metric"], "baseline": baseline, "now": now, "verdict": verdict})
    return out, 0


def cmd_lessons():
    history = overlay.read_jsonl(overlay.history_file())
    lesson_of, out = {}, {}
    for row in history:
        for ch in row.get("changes") or []:
            if ch.get("lesson"):
                lesson_of[ch.get("id")] = ch["lesson"]
                item = out.setdefault(ch["lesson"], {"projects": set(), "changes": 0, "kept": 0})
                item["projects"].add(row.get("project"))
                item["changes"] += 1
    for row in history:
        for v in row.get("previous") or []:
            if v.get("verdict") == "kept" and v.get("id") in lesson_of:
                out[lesson_of[v["id"]]]["kept"] += 1
    return {k: dict(v, projects=len(v["projects"])) for k, v in sorted(out.items())}, 0


def discovered_projects():
    spec = importlib.util.spec_from_loader("retro_scan", SourceFileLoader("retro_scan", str(Path(__file__).with_name("retro-scan"))))
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    return [p["project"] for p in scan.discover()]


def private_words():
    projects = {r.get("project") for r in overlay.read_jsonl(overlay.history_file()) if r.get("project")}
    projects.update(discovered_projects())
    words = {Path(p).name.lower() for p in projects}
    try:
        words.update(w.strip().lower() for w in (overlay.state_dir() / "private-words.txt").read_text().splitlines())
    except OSError:
        pass
    return sorted(w for w in words if len(w) >= 4 and w not in PUBLIC_NAMES)


def scrub(paths, strict):
    patterns = [HOME_PATH, EMAIL]
    if strict:
        patterns += [re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", re.I) for w in private_words()]
    hits = []
    for path in paths:
        for n, line in enumerate(Path(path).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for rx in patterns:
                hits += [{"file": str(path), "line": n, "match": m.group(0)} for m in rx.finditer(line)]
    return hits


def cmd_scrub(paths, strict):
    hits = scrub(paths, strict)
    return {"hits": hits}, 1 if hits else 0


def cmd_publish(report, project, sha, metrics_path, changes_path, verdicts_path):
    hits = scrub([report], strict=False)
    if hits:
        return {"published": False, "hits": hits}, 1
    metrics = json.loads(Path(metrics_path).read_text())
    changes = json.loads(Path(changes_path).read_text())
    verdicts = json.loads(Path(verdicts_path).read_text()) if verdicts_path else []
    for ch in changes:
        ch["baseline"] = metric_value(metrics, ch.get("metric", ""))
    window = metrics.get("window") or {}
    dest = Path(project) / "docs" / "factory" / "retros" / f"{str(window.get('until', ''))[:10]}-{sha[:7]}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report, dest)
    history = overlay.read_jsonl(overlay.history_file())
    history.append({"project": project, "sha": sha, "since": window.get("since"), "until": window.get("until"),
                    "headline": metrics.get("headline"), "changes": changes, "previous": verdicts})
    overlay.write_jsonl(overlay.history_file(), history)
    return {"published": True, "path": str(dest)}, 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Retro Engine checks and overlay writer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply")
    p.add_argument("candidates")
    p.add_argument("--mode", choices=("draft", "auto-fix"))
    for name in ("approve", "drop", "retire"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("verdicts")
    p.add_argument("--project", required=True)
    p.add_argument("--metrics", required=True)
    sub.add_parser("lessons")
    p = sub.add_parser("scrub")
    p.add_argument("files", nargs="+")
    p.add_argument("--strict", action="store_true")
    p = sub.add_parser("publish")
    p.add_argument("report")
    for flag in ("--project", "--sha", "--metrics", "--changes"):
        p.add_argument(flag, required=True)
    p.add_argument("--verdicts")
    a = ap.parse_args(argv)
    if a.cmd == "apply":
        out, code = cmd_apply(a.candidates, a.mode)
    elif a.cmd == "approve":
        out, code = cmd_approve(a.id)
    elif a.cmd == "drop":
        out, code = cmd_drop(a.id)
    elif a.cmd == "retire":
        out, code = cmd_retire(a.id)
    elif a.cmd == "verdicts":
        out, code = cmd_verdicts(str(Path(a.project).resolve()), a.metrics)
    elif a.cmd == "lessons":
        out, code = cmd_lessons()
    elif a.cmd == "scrub":
        out, code = cmd_scrub(a.files, a.strict)
    else:
        out, code = cmd_publish(a.report, str(Path(a.project).resolve()), a.sha, a.metrics, a.changes, a.verdicts)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
```

Then: `chmod +x scripts/retro-check`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_retro_check -v`
Expected: all PASS. Note `test_publish_...` resolves the project path; the fixture uses `realpath`, so the history row's project matches.

- [ ] **Step 5: Commit**

```bash
git add scripts/retro-check tests/test_retro_check.py
git commit -m "Retro Engine: retro-check applies overlay entries, scrubs and publishes"
```

---

### Task 4: `hooks/retro_overlay.py`, SessionStart

**Files:**
- Create: `hooks/retro_overlay.py`
- Modify: `hooks/hooks.json` (SessionStart `startup|resume|clear` list)
- Test: `tests/test_retro_overlay.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for hooks/retro_overlay.py (SessionStart)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "retro_overlay.py"


class RetroOverlayHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-hook-")
        self.state = Path(os.path.realpath(self.tmp.name)) / "state"
        (self.state / "overlay").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self):
        env = dict(os.environ, SOFTWARE_FACTORY_HOME=str(self.state), CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(self.state)}),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def write(self, name, rows):
        (self.state / "overlay" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_silent_when_there_is_nothing(self):
        self.assertEqual(self.run_hook(), "")

    def test_shows_active_entries_pending_stale_and_failure(self):
        version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.write("entries", [
            {"id": "a", "role": "all", "text": "Name the failing test.", "status": "active", "plugin_version": version},
            {"id": "b", "role": "all", "text": "Old.", "status": "stale", "plugin_version": version}])
        self.write("pending", [{"id": "c"}])
        (self.state / "retro-failed").write_text("headless retro exited 1\n")
        context = self.run_hook()
        self.assertIn("[all] Name the failing test. (overlay a)", context)
        self.assertIn("1 overlay entries are stale", context)
        self.assertIn("1 change(s) wait", context)
        self.assertIn("Retro failed: headless retro exited 1", context)

    def test_marks_entry_stale_after_a_plugin_update(self):
        self.write("entries", [{"id": "a", "role": "all", "text": "Something new.", "status": "active",
                                "plugin_version": "0.0.1", "anchor_sha": "0000",
                                "target": {"file": "agents/team-lead.md", "heading": "Status board"}}])
        context = self.run_hook()
        self.assertNotIn("Something new.", context)
        row = json.loads((self.state / "overlay" / "entries.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["status"], "stale")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_retro_overlay -v`
Expected: FAIL (`can't open file '.../hooks/retro_overlay.py'`).

- [ ] **Step 3: Write `hooks/retro_overlay.py`**

```python
"""SessionStart hook (startup, resume, clear): load the user's Retro Engine overlay into the session.

Refreshes the overlay after a plugin update (keep, retire or mark stale each entry), prints the active entries,
and tells the Team Lead about stale entries, changes that wait for the user's yes and a failed scheduled retro.
Prints nothing when there is nothing to say. Never blocks the session.
"""

import json
import sys

import overlay


def main():
    try:
        sys.stdin.read()
        path = overlay.overlay_file("entries")
        rows, changed = overlay.refresh(overlay.read_jsonl(path))
        if changed:
            overlay.write_jsonl(path, rows)
        parts = [overlay.render(rows)]
        stale = sum(1 for e in rows if e.get("status") == "stale")
        if stale:
            parts.append(f"{stale} overlay entries are stale after a plugin update and are not applied. The next "
                         f"retro asks the user to keep, drop or rewrite them.")
        waiting = len(overlay.read_jsonl(overlay.overlay_file("pending")))
        drafts = len(list((overlay.state_dir() / "upstream").glob("*.md")))
        if waiting or drafts:
            parts.append(f"Retro: {waiting} change(s) wait for the user's yes and {drafts} upstream draft(s) are "
                         f"ready. If you act as the Team Lead, tell the user once and offer /software-factory:retro review.")
        failed = overlay.state_dir() / "retro-failed"
        if failed.is_file():
            parts.append(f"Retro failed: {failed.read_text(errors='ignore').strip()[:200]}. If you act as the Team "
                         f"Lead, tell the user once and offer /software-factory:retro to run it now.")
        context = "\n\n".join(p for p in parts if p)
    except Exception:  # never block the session start over the overlay
        return 0
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Register the hook**

In `hooks/hooks.json`, inside the SessionStart entry with `"matcher": "startup|resume|clear"`, add after the `scratch.py start` hook object:

```json
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/retro_overlay.py\"",
            "timeout": 10
          }
```

(Add the comma after the preceding `}`.) Validate: `python3 -c "import json; json.load(open('hooks/hooks.json'))"` prints nothing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_retro_overlay -v`
Expected: all PASS. (`Status board` is a real heading in `agents/team-lead.md`; the stored hash `0000` does not match it and the entry text is not in it, so it goes stale.)

- [ ] **Step 6: Commit**

```bash
git add hooks/retro_overlay.py hooks/hooks.json tests/test_retro_overlay.py
git commit -m "Retro Engine: SessionStart hook loads the overlay and waiting items"
```

---

### Task 5: `agents/retro.md`, the Retro agent

**Files:**
- Create: `agents/retro.md`
- Modify: `tests/test_release_files.py` (`AGENT_DISPLAY_NAMES`)
- Modify: `README.md` (team table and agent count)
- Test: `tests/test_retro_check.py` (one agent-file test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retro_check.py`, before the `if __name__` block:

```python
class RetroAgentFileTests(unittest.TestCase):
    def test_agent_hands_every_write_to_the_scripts(self):
        text = (ROOT / "agents" / "retro.md").read_text()
        self.assertIn("model: sonnet", text)
        for needle in ("retro-scan --project", "retro-check apply", "retro-check publish", "retro-check verdicts",
                       "retro-check scrub", "Never write the overlay files yourself"):
            self.assertIn(needle, text)
        self.assertNotIn("—", text)
```

Add `"retro": "Retro",` to `AGENT_DISPLAY_NAMES` in `tests/test_release_files.py` (after `"performance-engineer"`).

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_retro_check tests.test_release_files -v`
Expected: `RetroAgentFileTests` FAIL (`No such file`).

- [ ] **Step 3: Write `agents/retro.md`**

```markdown
---
name: retro
description: Retro agent of the Software Factory. Runs once a day on the user's machine (headless) for every factory project whose main moved, or when the user asks for a retro. Reads the measured sessions (errors, hook blocks, approval prompts, time and calls per dispatch, the user's corrections, PR review comments, CI times), finds the top frictions, and applies or drafts at most 2 changes per project through the retro-check script. Never edits code or plugin files.
tools: Read, Grep, Glob, Write, Edit, Bash
model: sonnet
color: cyan
---

# Role

You are the Retro agent of the Software Factory. Scripts count; you explain. For each project you get, you find why the factory lost time or annoyed the user, and you choose at most 2 changes that remove the cause. Blame the brief, the rules or the setup, never the model.

# Inputs

The prompt gives you:
- **Plugin root** (PLUGIN): the plugin folder. The scripts are `python3 <PLUGIN>/scripts/retro-scan` and `python3 <PLUGIN>/scripts/retro-check`. Run each as one plain command with absolute paths: no `cd`, no chains, no pipes, no redirects.
- **State** (STATE): the user's retro folder, usually `~/.claude/software-factory`.
- **Run folder** (RUN): `<STATE>/retros/<today>/`. Every file you write goes here, except upstream drafts (`<STATE>/upstream/`).
- **Projects**: a JSON list of `{project, sha, since}` (scheduled run), or one project with an optional branch and since (on-demand run).

# Steps for each project

Use the project folder's name as `<slug>`.

1. **Scan.** Run `retro-scan --project <project> --since <since> --out <RUN>/<slug>.json` (add `--branch <name>` when the prompt names one). Read the file.
2. **Close the loop.** Run `retro-check verdicts --project <project> --metrics <RUN>/<slug>.json`. Write its output to `<RUN>/<slug>-verdicts.json`. Reverted overlay entries are already retired by the script; say so in the report.
3. **Find the top 3 frictions.** Each one cites numbers from the scan. Look for:
   - one hook block reason repeated many times: agents do not know a rule, or the rule is wrong;
   - a sandbox-off command shape repeated: a missing allow rule or project script. A shape that starts with a wrapper program in front of the real one (for example `<wrapper> gh`) means a machine setup problem: some tool rewrites commands so they miss the sandbox exclusions;
   - a dispatch type with `calls_per` far above 80 or a long `p90_min`: tasks too big or briefs missing facts;
   - corrections that repeat one theme (UI not matching the approved mock, old bugs back, flaky tests debugged instead of quarantined, the wrong push target, "I don't follow");
   - repeated themes in PR review comments, or CI failures and long CI runs.
4. **Choose at most 2 changes.** For each, write: `id` (`r-<today>-<slug>-<n>`), `lesson` (a short kebab-case key; reuse a key from `retro-check lessons` when it is the same lesson), `level`, `owner` (role), `asset` (rule, hook, check, script or eval), `metric` (a headline key or `section.key` from the scan, such as `sandbox_off.<shape>` or `hook_blocks.<reason>`; lower must mean better). By level:
   - `user`: an overlay entry. Append one JSON line to `<RUN>/<slug>-candidates.jsonl`: `{"id", "kind": "add"|"tighten"|"pref", "class": "improvement"|"preference", "role": "<agent name or all>", "text": "<the rule, at most 3 lines>", "evidence": "<metric and value>", "target": {"file": "agents/<x>.md", "heading": "<exact markdown heading in the plugin file>"}}`. A `pref` sets a value for a setting the factory already has and may omit `target`. An entry adds or tightens; it never loosens.
   - `project`: a brain note for that project. Put it under "Brain notes" in the report; the Team Lead adds it.
   - `setup`: a problem in the user's machine setup. Put the exact fix under "Your setup". Never apply it.
   - `plugin`: only when the lesson passes the upstream bar below.
5. **Apply.** Run `retro-check apply <RUN>/<slug>-candidates.jsonl`. The script decides by the user's mode: accepted entries go active in auto-fix mode; entries that touch security or lower a review always wait for the user; rejected entries are reported with the script's reason. Never write the overlay files yourself.
6. **Report.** Write `<RUN>/<slug>.md` with the template below. Counts and paraphrases only: no quotes of the user, no file paths, no emails, no customer or people names.
7. **Publish.** Write `<RUN>/<slug>-changes.json` (a JSON list of `{id, lesson, level, owner, asset, metric}`). Run `retro-check publish <RUN>/<slug>.md --project <project> --sha <sha> --metrics <RUN>/<slug>.json --changes <RUN>/<slug>-changes.json --verdicts <RUN>/<slug>-verdicts.json`. If it reports hits, remove them from the report and run it once more.

# Upstream bar (plugin-level lessons)

Draft an upstream change only when all hold:
- `retro-check lessons` shows the lesson in 3 or more projects, with at least one change kept;
- the gain covers 10 or more dispatches, and Blocker or Major review findings did not rise;
- it is an improvement, not a preference, and it contradicts no contract rule (gates, rule 9.13, `SR-NN`, section 8 reviews);
- when projects learned opposite lessons, the draft proposes a new setup option with a safe default, not a rule.

Write `<STATE>/upstream/<lesson>.md`: a title, the problem in counts only, the change (plugin file, heading, the exact new text), the evidence (number of projects, metric before and after). Run `retro-check scrub <STATE>/upstream/<lesson>.md --strict`. On hits, rewrite in generic words and run it again; if hits remain, delete the draft and say so in the report. Never push, open a pull request or file an issue: the user decides in a session.

# Report template

    # Retro <today>, <slug> (<sha7>)

    Window: <since> to <until>, <n> sessions.

    ## Metrics
    | Metric | This window | Per session |

    ## Previous changes
    | Change | Metric | Before | Now | Verdict |

    ## Top frictions
    1. <friction, with its numbers and cause>

    ## Changes
    | Id | Level | Owner | Asset | Proof metric | Status (active, pending, rejected, draft) |

    ## Brain notes
    ## Your setup
    ## Ask the user
    (stale overlay entries: keep, drop or rewrite; pending changes and why they wait)
    ## Upstream drafts

# Never

- Never edit code, plugin files, settings files or any file in a project. The only project file that changes is the report, and `retro-check publish` copies it.
- Never write the overlay files yourself; `retro-check` is their only writer.
- Never quote the user's messages outside the run folder.
- Never push, merge, open pull requests or file issues.
- If a scan fails for one project, write that in a one-line report for it and go on to the next.

# Reply

End with one line per project: `<slug>: <n> frictions, <n> changes (<n> active, <n> pending, <n> rejected), report <path or "not published: reason">`.
```

- [ ] **Step 4: Add the README row and count**

In `README.md`, change `13 agents, what each one does` to `14 agents, what each one does`, and add this row after the Performance Engineer row:

```markdown
| **Retro** | Sonnet | Once a day, for each factory project whose main moved, reads the sessions (errors, approval prompts, time per task, your corrections) and fixes at most 2 causes as your own lessons, or drafts them for your yes. Lessons seen in 3+ projects become a draft change to the plugin. Runs on your machine; your messages never leave it |
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_retro_check tests.test_release_files tests.test_scratch_leaks -v`
Expected: PASS except `test_readme_test_count_matches_test_functions` (fixed in Task 9).

- [ ] **Step 6: Commit**

```bash
git add agents/retro.md tests/test_retro_check.py tests/test_release_files.py README.md
git commit -m "Retro Engine: Retro agent"
```

---

### Task 6: `/software-factory:retro` skill, Team Lead and session setup

**Files:**
- Create: `skills/retro/SKILL.md`
- Modify: `agents/team-lead.md` (Build loop step 12; the "Every delegation message" list)
- Modify: `skills/session-setup/SKILL.md` (new last step)

- [ ] **Step 1: Write `skills/retro/SKILL.md`**

```markdown
---
name: retro
description: Run a Software Factory retro now, or review the changes past retros left waiting for your yes. Use when the user asks for a retro, asks why the factory keeps hitting the same problem, or asks to see or approve retro changes. A retro now also covers work that never reached main, such as a stuck dev branch.
---

# Retro

STATE is `$SOFTWARE_FACTORY_HOME`, else `~/.claude/software-factory`. CHECK is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/retro-check`.

## `review`: answer what waits

1. Read `<STATE>/overlay/pending.jsonl`, the stale rows of `<STATE>/overlay/entries.jsonl` and `<STATE>/upstream/*.md`.
2. Show each item to the user in plain words: what it changes, for which role, the evidence, and why it waits (the `reason` field). One question per item.
3. Pending entry: on yes run `<CHECK> approve <id>`, on no `<CHECK> drop <id>`. Stale entry: keep means `<CHECK> retire <id>` and a new candidate with the current heading in the next retro; drop means `<CHECK> retire <id>`.
4. Upstream draft: show the full draft. On yes, prepare a pull request to the plugin repository: clone it into `$FACTORY_TMP`, create branch `retro/<lesson>`, make the change, run `python3 -m unittest discover -s tests`, and run `<CHECK> scrub <changed files> --strict`. Pushing and opening the draft pull request each need the user's yes in this session. Delete the draft file after the pull request is open or the user says no.

## Run a retro now

1. The project is the factory project of this session (the folder with `docs/factory/status.md`), or the one the user names. Take a branch and a start date from the user's words when given.
2. SHA: `git -C <project> rev-parse origin/main`, or the branch head when a branch is named.
3. Read `${CLAUDE_PLUGIN_ROOT}/agents/retro.md` and follow it yourself for this one project, with Plugin root `${CLAUDE_PLUGIN_ROOT}`, State `<STATE>`, Run folder `<STATE>/retros/<today>/`.
4. Items that wait for the user: ask now, in this session, then run approve or drop.
5. Tell the user the report path and the changes, in plain words.
```

- [ ] **Step 2: Point the Team Lead at the engine**

In `agents/team-lead.md`, Build loop step 12 ends with `Feed lessons to "Promoting lessons".` Append right after it (same paragraph):

```
 The Retro Engine (`agents/retro.md`) runs the cross-project part once a day on the user's machine: when you read the state at the start of a request, add the "Brain notes" of the newest `docs/factory/retros/*.md` you have not added yet to the brain, and commit the report with your next change.
```

In the "Every delegation message to a subagent contains:" list, add after the `- **Task**:` bullet:

```
- **Overlay**: the overlay lines for this role (`[<role>]` and `[all]`) from the session-start context, verbatim. Leave this out when there are none.
```

- [ ] **Step 3: Add the session-setup step**

In `skills/session-setup/SKILL.md`, after the last numbered step, add the next number:

```
7. **Retro Engine (once per machine).** If `~/.claude/software-factory/retro-settings.json` does not exist, ask the user two questions, one at a time:
   - "May the daily retro apply its fixes itself, or only draft them for your yes? Fixes that touch security always ask." Options: `draft` (default) or `auto-fix`.
   - "Run the retro once a day, only for projects whose main changed? It runs on this machine." Options: yes with an hour (default 9), or no.
   Write `{"mode": "<answer>"}` to that file. On yes, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/retro-schedule install --hour <hour>` (the user approves it). On no, tell the user they can run `/software-factory:retro` any time.
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`
Expected: PASS except the README test count (Task 9). If an agent-file test pins text in step 12 or the delegation list, keep its pinned text intact and adjust only the new sentence.

- [ ] **Step 5: Commit**

```bash
git add skills/retro/SKILL.md agents/team-lead.md skills/session-setup/SKILL.md
git commit -m "Retro Engine: retro skill, Team Lead and setup wiring"
```

---

### Task 7: `scripts/retro-daily` and `scripts/retro-schedule`

**Files:**
- Create: `scripts/retro-daily`, `scripts/retro-schedule`
- Test: `tests/test_retro_schedule.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for scripts/retro-daily and scripts/retro-schedule."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_loader(name.replace("-", "_"), SourceFileLoader(name, str(ROOT / "scripts" / name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetroDailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-daily-")
        self.base = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_due_means_no_model_run_and_no_failure_marker(self):
        env = dict(os.environ, SOFTWARE_FACTORY_HOME=str(self.base / "state"),
                   CLAUDE_PROJECTS_DIR=str(self.base / "projects"), PATH="/usr/bin:/bin")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retro-daily")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.base / "state" / "retro-failed").exists())

    def test_allowed_tools_scope_writes_to_the_state_folder(self):
        daily = load("retro-daily")
        tools = daily.allowed_tools(Path("/opt/plugin"), Path("/h/.claude/software-factory"))
        self.assertIn("Bash(python3 /opt/plugin/scripts/retro-scan *)", tools)
        self.assertIn("Bash(python3 /opt/plugin/scripts/retro-check *)", tools)
        self.assertIn("Write(//h/.claude/software-factory/**)", tools)
        self.assertNotIn("Write", tools.split(","))

    def test_newest_plugin_copy_wins(self):
        daily = load("retro-daily")
        cache = self.base / "cache"
        for v in ("0.9.0", "0.10.0", "0.9.5"):
            (cache / "mkt" / "software-factory" / v / "agents").mkdir(parents=True)
            (cache / "mkt" / "software-factory" / v / "agents" / "retro.md").write_text("x")
        self.assertEqual(daily.newest_plugin(cache).name, "0.10.0")


class RetroScheduleTests(unittest.TestCase):
    def test_plist_and_cron_line(self):
        schedule = load("retro-schedule")
        plist = schedule.plist("/s/bin/retro-daily", 9, "/usr/bin:/bin", "/s/retro.log")
        for needle in (schedule.LABEL, "<integer>9</integer>", "/s/bin/retro-daily", "/usr/bin:/bin"):
            self.assertIn(needle, plist)
        line = schedule.cron_line("/s/bin/retro-daily", 7, "/usr/bin:/bin")
        self.assertTrue(line.startswith("0 7 * * * "))
        self.assertTrue(line.endswith(schedule.MARKER))

    def test_print_does_not_install(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retro-schedule"), "install", "--hour", "8", "--print"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("8", r.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_retro_schedule -v`
Expected: FAIL (`No such file or directory: .../scripts/retro-daily`).

- [ ] **Step 3: Write `scripts/retro-daily`**

Use the allowed-tools syntax confirmed in Task 0 (the code below assumes comma-separated rules and `//` for absolute paths).

```python
#!/usr/bin/env python3
"""Daily retro launcher, started by launchd or cron (see retro-schedule). The model runs only when a project is due.

Finds the newest installed plugin copy (the cache path changes with every update), runs retro-scan --due, and starts
one headless Retro run for all due projects. A failure leaves <state>/retro-failed, which the next session start
reports to the Team Lead. Stdlib only, Python 3.9+.
"""

import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def state_dir():
    return Path(os.environ.get("SOFTWARE_FACTORY_HOME") or Path.home() / ".claude" / "software-factory")


def version_key(path):
    return tuple(int(x) for x in path.name.split(".")) if re.match(r"^\d+(\.\d+)*$", path.name) else ()


def newest_plugin(cache=None):
    cache = cache or Path.home() / ".claude" / "plugins" / "cache"
    found = [p.parents[1] for p in cache.glob("*/software-factory/*/agents/retro.md")]
    return max(found, key=version_key) if found else None


def plugin_root():
    here = Path(__file__).resolve().parents[1]
    return here if (here / "agents" / "retro.md").is_file() else newest_plugin()


def allowed_tools(root, state):
    # Absolute paths in permission rules take a leading '//'.
    return ",".join(["Read", "Grep", "Glob", f"Bash(python3 {root}/scripts/retro-scan *)",
                     f"Bash(python3 {root}/scripts/retro-check *)", f"Write(/{state}/**)", f"Edit(/{state}/**)"])


def fail(state, reason):
    state.mkdir(parents=True, exist_ok=True)
    (state / "retro-failed").write_text(reason + "\n")
    print(reason, file=sys.stderr)
    return 1


def main():
    state = state_dir()
    root = plugin_root()
    if not root:
        return fail(state, "retro: the Software Factory plugin was not found")
    scan = subprocess.run([sys.executable, str(root / "scripts" / "retro-scan"), "--due"],
                          capture_output=True, text=True, timeout=1800)
    if scan.returncode:
        return fail(state, "retro-scan --due failed: " + scan.stderr.strip()[-200:])
    due = json.loads(scan.stdout or "[]")
    if not due:
        return 0
    today = datetime.date.today().isoformat()
    prompt = (f"Scheduled retro. Plugin root: {root}. State: {state}. Run folder: {state}/retros/{today}/. "
              f"Projects (JSON): {json.dumps(due)}")
    state.mkdir(parents=True, exist_ok=True)
    log = state / "retro.log"
    with open(log, "a", encoding="utf-8") as out:
        run = subprocess.run(["claude", "-p", "--agent", "software-factory:retro",
                              "--allowedTools", allowed_tools(root, state), prompt],
                             stdout=out, stderr=subprocess.STDOUT, cwd=str(state), timeout=3600)
    if run.returncode:
        return fail(state, f"headless retro exited {run.returncode}, see {log}")
    try:
        (state / "retro-failed").unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Fix the test expectation to match the `//` form: `allowed_tools(Path("/opt/plugin"), Path("/h/.claude/software-factory"))` returns `Write(//h/.claude/software-factory/**)`. The test above already expects that.

- [ ] **Step 4: Write `scripts/retro-schedule`**

```python
#!/usr/bin/env python3
"""Install, remove or show the daily retro job.

  retro-schedule install [--hour 9] [--print]   copy retro-daily to <state>/bin and register it daily at that hour
                                                (macOS: a launchd agent; Linux: one crontab line). --print shows
                                                what would be installed and changes nothing.
  retro-schedule uninstall                      remove the job
Stdlib only, Python 3.9+.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

LABEL = "dev.software-factory.retro"
MARKER = "# software-factory-retro"


def state_dir():
    return Path(os.environ.get("SOFTWARE_FACTORY_HOME") or Path.home() / ".claude" / "software-factory")


def plist(launcher, hour, path_env, log):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{escape(sys.executable)}</string><string>{escape(str(launcher))}</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{escape(path_env)}</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{int(hour)}</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>{escape(str(log))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict>
</plist>
"""


def cron_line(launcher, hour, path_env):
    return f"0 {int(hour)} * * * PATH={path_env} {sys.executable} {launcher} {MARKER}"


def path_env():
    claude = shutil.which("claude")
    dirs = [str(Path(claude).parent)] if claude else []
    return ":".join(dirs + ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"])


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def crontab_without_marker():
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = current.stdout.splitlines() if current.returncode == 0 else []
    return [line for line in lines if not line.endswith(MARKER)]


def install(hour, show):
    state = state_dir()
    launcher = state / "bin" / "retro-daily"
    log = state / "retro.log"
    text = plist(launcher, hour, path_env(), log) if sys.platform == "darwin" else cron_line(launcher, hour, path_env())
    if show:
        print(text)
        return 0
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("retro-daily"), launcher)
    launcher.chmod(0o755)
    if sys.platform == "darwin":
        target = plist_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        target.write_text(text)
        return subprocess.run(["launchctl", "bootstrap", domain, str(target)]).returncode
    lines = crontab_without_marker() + [text]
    return subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True).returncode


def uninstall():
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        try:
            plist_path().unlink()
        except FileNotFoundError:
            pass
        return 0
    lines = crontab_without_marker()
    return subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True).returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily retro job.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--hour", type=int, default=9, choices=range(24))
    p.add_argument("--print", action="store_true", dest="show")
    sub.add_parser("uninstall")
    a = ap.parse_args(argv)
    return install(a.hour, a.show) if a.cmd == "install" else uninstall()


if __name__ == "__main__":
    sys.exit(main())
```

Then: `chmod +x scripts/retro-daily scripts/retro-schedule`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_retro_schedule -v`
Expected: all PASS. The installed launcher is a copy in `<state>/bin`, so its `plugin_root()` falls back to `newest_plugin()`, which the test covers.

- [ ] **Step 6: Commit**

```bash
git add scripts/retro-daily scripts/retro-schedule tests/test_retro_schedule.py
git commit -m "Retro Engine: daily launcher and schedule installer"
```

---

### Task 8: End-to-end dry run on this machine (read-only, then one real retro)

No new code. Proves the pieces work together before release.

- [ ] **Step 1: Due check**

Run: `python3 scripts/retro-scan --due`
Expected: a JSON list (possibly empty). Needs network for `git ls-remote`; run it with the sandbox off if it fails with a network error, and say why.

- [ ] **Step 2: Scan one project into a scratch state folder**

Run: `SOFTWARE_FACTORY_HOME="$FACTORY_TMP/state" python3 scripts/retro-scan --project <a factory project from --discover> --out "$FACTORY_TMP/state/retros/test/scan.json"`
Expected: the file exists; `headline` counts are non-zero for an active project. Check that the handover's known causes show up when the window covers 2026-11-07 to 2026-11-14 (add `--since 2026-11-07T00:00:00Z`): `cd-chain` or a `cd X` hook block, a `python3 <script>` sandbox-off shape.

- [ ] **Step 3: One on-demand retro with the scratch state**

With `SOFTWARE_FACTORY_HOME="$FACTORY_TMP/state"`, follow `skills/retro/SKILL.md` "Run a retro now" for the plugin repo itself. Expected: a report in `$FACTORY_TMP/state/retros/<today>/`, `retro-check publish` copies it into the project's `docs/factory/retros/` only if the project has `docs/factory/` (the plugin repo does not; skip publish and read the report instead). Do not commit the report.

- [ ] **Step 4: Record what you saw**

Add a short "Dry run 2026-11-14" note under "Not checked yet" in the spec (numbers only, no project names). Commit:

```bash
git add docs/superpowers/specs/2026-11-14-retro-engine-design.md
git commit -m "Spec: record Retro Engine dry run"
```

---

### Task 9: Release files (0.9.0)

**Files:**
- Modify: `.claude-plugin/plugin.json` (`"version": "0.9.0"`), `.claude-plugin/marketplace.json` if it carries a version
- Modify: `CHANGELOG.md`, `README.md`

- [ ] **Step 1: Bump the version**

Set `"version": "0.9.0"` in `.claude-plugin/plugin.json`. Run `grep -n '"version"' .claude-plugin/marketplace.json`; if it lists the plugin version, set it to `0.9.0` too. In `README.md`, change `Current version: 0.8.4.` to `Current version: 0.9.0.`

- [ ] **Step 2: Add the changelog entry**

Insert above `## 0.8.4 (2026-11-14)`:

```markdown
## 0.9.0 (2026-11-14)

- **The factory learns from its own sessions, in every project on your machine.** A new Retro agent runs once a day, only for factory projects whose main moved. A script counts errors, hook blocks, approval prompts, time and tool calls per task, your corrections, PR review comments and CI times, with no model tokens. One Sonnet run then names the top frictions and picks at most 2 changes per project, each with an owner and the metric that proves it worked. The next retro checks that metric and drops the change if it did not help.
- **Your lessons live in your own overlay, not in the plugin.** Fixes go to `~/.claude/software-factory/overlay/`, which plugin updates never overwrite. An entry can add or tighten a rule, never loosen one; anything that touches security, or lowers a review through a setting, waits for your yes. After a plugin update, an entry the plugin now covers is retired and one whose rule changed is marked stale until you decide.
- **You choose how much it does alone.** Session setup asks once: `draft` (default, everything waits for you) or `auto-fix`. Run `/software-factory:retro` for a retro now (it also covers a branch that never reached main) and `/software-factory:retro review` to answer what waits.
- **Lessons seen in 3 or more projects become a draft change to the plugin**, scrubbed of project names, paths and quotes by a script check. Nothing is pushed or opened without your yes. Raw messages stay in `~/.claude/software-factory/`.
```

- [ ] **Step 3: Update the README test count**

Run: `python3 -m unittest tests.test_release_files -v`
The failing count test prints the expected text. Update `N offline tests` in `README.md` line 19 to the number the test computes (count `def test_` lines in `tests/*.py`).

- [ ] **Step 4: Full test run**

Run: `python3 -m unittest discover -s tests`
Expected: `OK`. Also run `grep -rn $'—' agents skills hooks scripts docs/superpowers README.md CHANGELOG.md` and expect no output.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin README.md CHANGELOG.md
git commit -m "0.9.0: Retro Engine"
```

Version note for the user: PR 127 also claims 0.9.0; whichever merges second renumbers.

---

### Task 10: Review

- [ ] **Step 1:** Dispatch the Architect (`opus`) to review the branch diff against the spec, and the Security Engineer (`opus`) to review `hooks/overlay.py` (the loosen and security checks), `scripts/retro-check` (scrub, publish), `scripts/retro-daily` (allowed tools) and `scripts/retro-schedule`. Narrow briefs: the file list, the spec path, "report findings only".
- [ ] **Step 2:** Fix Blocker and Major findings in new commits, rerun `python3 -m unittest discover -s tests`.

---

### Task 11: Plan decisions that refine the spec

Apply these to the spec in the first commit of this plan (with Task 0), so spec and code agree:

1. **Autonomy wording.** Replace "loosens a contract rule;" under Autonomy with "lowers a review through an existing setting (for example the Security roster);". Overlay entries can never loosen a rule; the check rejects them.
2. **Overlay files.** One `entries.jsonl` (statuses active, stale, retired) plus `pending.jsonl` replace `roles/<role>.md`, `prefs.md` and `allowlist.md`. Allow rules always touch security, so they always wait for the user; no allowlist file is needed.
3. **Only scripts write.** The Retro agent writes only in `<state>/retros/<date>/` and `<state>/upstream/`. `retro-check` is the only writer of the overlay and the history, and `retro-check publish` copies the report into the project after the scrub check.
4. **Project lessons.** Brain notes are listed in the report; the Team Lead adds them to `brain.md` at the start of the next request (the brain keeps one owner).
5. **Overlay reaches subagents through the Team Lead.** The SessionStart hook prints the overlay to the main session; the Team Lead passes each role's lines in the brief.
6. **Upstream draft.** A draft is a scrubbed file in `<state>/upstream/`; `/software-factory:retro review` turns it into a branch and draft pull request after the user's yes.
7. **On-demand retro runs inline** in the user's session (no nested headless run), following `agents/retro.md`.
8. **Not done:** re-checking active entries against a changed contract floor after an update (the floor lives in the check, not in plugin text); mapping removed worktrees outside `.claude/worktrees` and `.worktrees`; repositories whose default branch is not `main`.

"""Tests for hooks/retro_overlay.py (SessionStart)."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "retro_overlay.py"


def iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


class RetroOverlayHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-hook-")
        self.state = Path(os.path.realpath(self.tmp.name)) / "state"
        (self.state / "overlay").mkdir(parents=True)
        self.marker = self.state / "overlay" / "last-session"

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, state=None):
        env = dict(os.environ, SOFTWARE_FACTORY_HOME=str(state or self.state), CLAUDE_PLUGIN_ROOT=str(ROOT))
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(self.state)}),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def write(self, name, rows):
        (self.state / "overlay" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def live_entry(self, entry_id, when, by="auto", text="Name the failing test."):
        version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        return {"id": entry_id, "role": "all", "text": text, "status": "active", "plugin_version": version,
                "went_live_at": when, "went_live_by": by}

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

    def test_first_run_lists_no_went_live_entry_and_writes_the_marker(self):
        self.write("entries", [self.live_entry("a", iso(timedelta(minutes=-5)))])
        context = self.run_hook()
        self.assertNotIn("went live", context)
        self.assertTrue(self.marker.is_file())
        datetime.fromisoformat(self.marker.read_text().strip())

    def test_lists_entries_that_went_live_since_the_marker_with_a_revert_command(self):
        self.marker.write_text(iso(timedelta(hours=-1)))
        self.write("entries", [
            self.live_entry("new-auto", iso(timedelta(minutes=-5)), "auto", "Line one.\nLine two."),
            self.live_entry("new-yes", iso(timedelta(minutes=-4)), "approved"),
            self.live_entry("old", iso(timedelta(hours=-2)))])
        context = self.run_hook()
        self.assertIn("went live since your last session", context)
        self.assertIn("[all] Line one. / Line two. (went live: auto, overlay new-auto)", context)
        self.assertIn("(went live: approved, overlay new-yes)", context)
        self.assertIn(f"{ROOT}/scripts/retro-check retire new-auto", context)
        self.assertIn(f"{ROOT}/scripts/retro-check retire new-yes", context)
        self.assertNotIn("retire old", context)

    def test_lists_each_went_live_entry_once(self):
        self.marker.write_text(iso(timedelta(hours=-1)))
        self.write("entries", [self.live_entry("a", iso(timedelta(minutes=-5)))])
        self.assertIn("retire a", self.run_hook())
        self.assertNotIn("went live", self.run_hook())

    def test_does_not_list_a_retired_entry_or_one_without_a_valid_time(self):
        self.marker.write_text(iso(timedelta(hours=-1)))
        retired = dict(self.live_entry("r", iso(timedelta(minutes=-5))), status="retired")
        self.write("entries", [retired, self.live_entry("bad", "yesterday"), self.live_entry("none", None)])
        self.assertNotIn("went live", self.run_hook())

    def test_prints_the_bad_line_count_and_backup_name(self):
        (self.state / "overlay" / "entries.jsonl").write_text('{"id": "a"}\nnot json\n[1]\n')
        context = self.run_hook()
        self.assertRegex(context, r"2 corrupt line\(s\) in entries\.jsonl, backup: entries\.jsonl\.corrupt-\S+")
        backup = next((self.state / "overlay").glob("entries.jsonl.corrupt-*"))
        self.assertIn("not json", backup.read_text())
        self.assertNotIn("corrupt line", self.run_hook())

    def test_missing_state_dir_gives_no_output_and_creates_nothing(self):
        missing = self.state.parent / "nope"
        self.assertEqual(self.run_hook(missing), "")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()

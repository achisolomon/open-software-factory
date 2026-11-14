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

    def test_refresh_keeps_entry_when_update_only_changes_whitespace(self):
        e = overlay.stamp(entry(), self.plugin)
        e["status"] = "active"
        spaced = DEV_TEXT.replace("Run the tests before you reply.", "Run  the tests\nbefore you reply.  ")
        plugin2 = make_plugin(Path(os.path.realpath(self.tmp.name)) / "v2", version="1.1.0", dev_text=spaced)
        entries, _ = overlay.refresh([e], plugin2)
        self.assertEqual(entries[0]["status"], "active")

    def test_render_lists_active_entries_only(self):
        text = overlay.render([{"id": "a", "role": "all", "text": "Do A.", "status": "active"},
                               {"id": "b", "role": "all", "text": "Do B.", "status": "stale"}])
        self.assertIn("[all] Do A. (overlay a)", text)
        self.assertNotIn("Do B.", text)
        self.assertEqual(overlay.render([]), "")


if __name__ == "__main__":
    unittest.main()

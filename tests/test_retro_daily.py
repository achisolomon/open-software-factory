"""Tests for scripts/retro-daily and scripts/retro-schedule. Nothing here installs a schedule or starts a model."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = ",".join([
    "Read", "Grep", "Glob",
    "Bash(python3 /opt/plugin/scripts/retro-scan --project *)",
    "Bash(python3 /opt/plugin/scripts/retro-check apply *)",
    "Bash(python3 /opt/plugin/scripts/retro-check verdicts --project *)",
    "Bash(python3 /opt/plugin/scripts/retro-check lessons)",
    "Bash(python3 /opt/plugin/scripts/retro-check scrub *)",
    "Bash(python3 /opt/plugin/scripts/retro-check publish *)",
    "Write(//h/.claude/software-factory/retros/**)", "Edit(//h/.claude/software-factory/retros/**)",
    "Write(//h/.claude/software-factory/upstream/**)", "Edit(//h/.claude/software-factory/upstream/**)",
])


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(ROOT / "scripts" / name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def done(code=0, stdout=""):
    return subprocess.CompletedProcess([], code, stdout, "SECRET child output")


class RetroDailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-daily-")
        self.base = Path(os.path.realpath(self.tmp.name))
        self.state = self.base / "state"
        self.daily = load("retro-daily")
        env = mock.patch.dict(os.environ, {"SOFTWARE_FACTORY_HOME": str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        root = mock.patch.object(self.daily, "plugin_root", return_value=Path("/opt/plugin"))
        root.start()
        self.addCleanup(root.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, due, agent_codes):
        """Run main() with a scan listing `due` and one headless exit code per project. Returns (code, agent calls)."""
        scan = done(0, json.dumps([{"project": p, "sha": "abc1234", "since": "2026-11-01"} for p in due]))
        agents = iter(agent_codes)
        calls = []

        def fake_run(cmd, **kw):
            if cmd[0] == "claude":
                calls.append((cmd, kw))
                return done(next(agents))
            return scan

        with mock.patch.object(self.daily.subprocess, "run", side_effect=fake_run):
            return self.daily.main(), calls

    def test_nothing_due_means_no_model_run_and_no_failure_marker(self):
        code, calls = self.run_main([], [])
        self.assertEqual((code, calls), (0, []))
        self.assertFalse((self.state / "retro-failed").exists())

    def test_nothing_due_end_to_end_with_a_bare_environment(self):
        env = dict(os.environ, SOFTWARE_FACTORY_HOME=str(self.state), CLAUDE_PROJECTS_DIR=str(self.base / "projects"),
                   HOME=str(self.base), PATH="/usr/bin:/bin")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retro-daily")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.state / "retro-failed").exists())

    def test_allowed_tools_string_is_exact(self):  # R7
        tools = self.daily.allowed_tools(Path("/opt/plugin"), Path("/h/.claude/software-factory"))
        self.assertEqual(tools, EXPECTED_TOOLS)

    def test_allowed_tools_have_no_wildcard_check_no_network_and_no_state_wide_write(self):  # #172, #154, R6
        rules = self.daily.allowed_tools(Path("/opt/plugin"), Path("/h/s")).split(",")
        self.assertNotIn("Bash(python3 /opt/plugin/scripts/retro-check *)", rules)
        self.assertFalse([r for r in rules if r.startswith("Bash(") and "retro-" not in r])
        for banned in ("WebFetch", "WebSearch", "mcp__", "git", "gh ", "curl"):
            self.assertFalse([r for r in rules if banned in r], banned)
        writes = [r for r in rules if r.startswith(("Write(", "Edit("))]
        self.assertEqual(sorted(r.split("/h/s/")[1] for r in writes), ["retros/**)", "retros/**)", "upstream/**)", "upstream/**)"])
        for guarded in ("overlay", "retro-settings.json", "retro-failed", "retro-metrics.jsonl"):
            self.assertFalse([r for r in writes if guarded in r], guarded)

    def test_headless_marker_is_set_for_the_agent_and_due_json_lists_project_paths(self):
        code, calls = self.run_main(["/w/alpha"], [0])
        (cmd, kw), = calls
        self.assertEqual((code, kw["env"]["RETRO_HEADLESS"]), (0, "1"))
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], self.daily.allowed_tools(Path("/opt/plugin"), self.state))
        run_dir = next((self.state / "retros").iterdir())
        self.assertEqual(json.loads((run_dir / "due.json").read_text()), ["/w/alpha"])
        self.assertIn(f"Run folder: {run_dir}/", cmd[-1])

    def test_one_failing_project_does_not_stop_the_others_and_marker_holds_no_child_output(self):
        code, calls = self.run_main(["/w/alpha", "/w/beta", "/w/gamma"], [0, 3, 0])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertEqual((self.state / "retro-failed").read_text(), "agent-failed 3\n")

    def test_scan_failure_leaves_only_a_status_word_and_code(self):
        with mock.patch.object(self.daily.subprocess, "run", return_value=done(2)):
            self.assertEqual(self.daily.main(), 1)
        self.assertEqual((self.state / "retro-failed").read_text(), "scan-failed 2\n")

    def test_a_successful_run_clears_an_old_failure_marker(self):
        self.state.mkdir(parents=True)
        (self.state / "retro-failed").write_text("agent-failed 1\n")
        self.assertEqual(self.run_main(["/w/alpha"], [0])[0], 0)
        self.assertFalse((self.state / "retro-failed").exists())

    def test_retro_scan_due_skipping_a_bad_file_still_yields_the_other_projects(self):
        # retro-scan prints skips on stderr and exits 0; the launcher reads stdout only.
        scan = subprocess.CompletedProcess([], 0, json.dumps([{"project": "/w/alpha"}]), "retro-scan: skipped 1 unreadable")
        with mock.patch.object(self.daily.subprocess, "run", return_value=scan):
            self.assertEqual(self.daily.scan_due(Path("/opt/plugin")), ([{"project": "/w/alpha"}], None))

    def test_newest_plugin_copy_wins(self):
        cache = self.base / "cache"
        for v in ("0.9.0", "0.10.0", "0.9.5"):
            (cache / "mkt" / "software-factory" / v / "agents").mkdir(parents=True)
            (cache / "mkt" / "software-factory" / v / "agents" / "retro.md").write_text("x")
        self.assertEqual(self.daily.newest_plugin(cache).name, "0.10.0")


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
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SOFTWARE_FACTORY_HOME=str(Path(home) / "state"))
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retro-schedule"), "install", "--hour", "8", "--print"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("8", r.stdout)
            self.assertEqual(list(Path(home).iterdir()), [])


if __name__ == "__main__":
    unittest.main()

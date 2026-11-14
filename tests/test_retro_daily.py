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
    "Bash(python3 /opt/plugin/scripts/retro-check apply *)",
    "Bash(python3 /opt/plugin/scripts/retro-check verdicts --project *)",
    "Bash(python3 /opt/plugin/scripts/retro-check lessons)",
    "Bash(python3 /opt/plugin/scripts/retro-check scrub *)",
    "Bash(python3 /opt/plugin/scripts/retro-check publish *)",
    "Edit(//h/.software-factory/retros/2026-11-14/work/**)",
    "Edit(//h/.software-factory/upstream/**)",
])
STATE_H = Path("/h/.software-factory")
RUN_H = STATE_H / "retros" / "2026-11-14"


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
        self.scan_code = 0
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
        self.scans = []

        def fake_run(cmd, **kw):
            if cmd[0] == "claude":
                calls.append((cmd, kw))
                return done(next(agents))
            if "--project" in cmd:
                self.scans.append(cmd)
                return done(self.scan_code)
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
        self.assertEqual(self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H), EXPECTED_TOOLS)

    def test_allowed_tools_have_no_wildcard_check_no_network_scan_or_launcher_file_write(self):  # #172, #154, #192
        rules = self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H).split(",")
        self.assertNotIn("Bash(python3 /opt/plugin/scripts/retro-check *)", rules)
        self.assertFalse([r for r in rules if r.startswith("Bash(") and "retro-check" not in r])
        for banned in ("WebFetch", "WebSearch", "mcp__", "git", "gh ", "curl", "retro-scan", "Write("):
            self.assertFalse([r for r in rules if banned in r], banned)
        edits = [r for r in rules if r.startswith("Edit(")]
        self.assertEqual(len(edits), 2)
        for guarded in ("overlay", "retro-settings.json", "retro-failed", "metrics", "due.json", "alpha.json"):
            self.assertFalse([r for r in edits if guarded in r], guarded)
        self.assertFalse([r for r in edits if r.endswith("/2026-11-14/**)")])  # work/ only, not the whole run folder

    def test_headless_call_is_locked_down_and_the_scan_is_made_by_the_launcher(self):  # #193, #192
        code, calls = self.run_main(["/w/alpha"], [0])
        (cmd, kw), = calls
        run_dir = next((self.state / "retros").iterdir())
        prompt = cmd[2]
        self.assertEqual(code, 0)
        self.assertEqual(cmd, ["claude", "-p", prompt, "--plugin-dir", "/opt/plugin", "--agent", "software-factory:retro",
                               "--permission-mode", "dontAsk", "--setting-sources", "project", "--strict-mcp-config",
                               "--disallowedTools", "WebFetch,WebSearch,Agent,mcp__*",
                               "--allowedTools", self.daily.allowed_tools(Path("/opt/plugin"), self.state, run_dir)])
        self.assertEqual(kw["cwd"], str(run_dir))
        self.assertIn(f"Scan file (already made, read it): {run_dir}/alpha.json", prompt)
        self.assertEqual(json.loads((run_dir / "due.json").read_text()), ["/w/alpha"])
        self.assertTrue((run_dir / "work").is_dir())
        scan_cmd = self.scans[0]
        self.assertEqual(scan_cmd[2:], ["--project", "/w/alpha", "--since", "2026-11-01", "--out", str(run_dir / "alpha.json")])

    def test_child_env_marks_headless_and_drops_the_scrub_variable(self):  # #193 spike
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}):
            _, calls = self.run_main(["/w/alpha"], [0])
        env = calls[0][1]["env"]
        self.assertEqual(env["RETRO_HEADLESS"], "1")
        self.assertNotIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", env)

    def test_a_failed_project_scan_skips_that_agent_run_only(self):
        self.scan_code = 2
        code, calls = self.run_main(["/w/alpha"], [])
        self.assertEqual((code, calls), (1, []))
        self.assertEqual((self.state / "retro-failed").read_text(), "scan-failed 2\n")

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

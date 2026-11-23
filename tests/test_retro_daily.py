"""Tests for scripts/retro-daily and scripts/retro-schedule. Nothing here installs a schedule or starts a model."""

import contextlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "plugin"

EXPECTED_DENIED = [
    "WebFetch", "WebSearch", "Agent", "mcp__*", "Read(//h/.ssh/**)", "Read(//h/.aws/**)", "Read(//h/.config/gcloud/**)",
    "Read(//w/alpha/**/.env*)", "Read(//w/alpha/**/*.pem)", "Read(//w/alpha/**/*.key)", "Read(//w/alpha/**/*.tfstate*)",
    "Read(//w/alpha/**/credentials*.json)", "Read(//w/alpha/**/.npmrc)", "Read(//w/alpha/**/.netrc)",
    "Read(//w/alpha/**/.pypirc)", "Read(//w/alpha/**/id_*)", "Read(//w/alpha/**/*.p12)", "Read(//w/alpha/**/*.jks)", "Read(//w/alpha/.git/config)"]
EXPECTED_TOOLS = ([
    "Read(//h/.software-factory/retros/2026-11-14/alpha/**)", "Read(//h/.software-factory/overlay/**)",
    "Read(//h/.software-factory/upstream/**)", "Read(//opt/plugin/**)", "Read(//w/alpha/**)",
    "Bash(python3 /opt/plugin/scripts/retro-check apply *)",
    "Bash(python3 /opt/plugin/scripts/retro-check verdicts --project /w/alpha "
    "--metrics /h/.software-factory/retros/2026-11-14/alpha/alpha.json)",
    "Bash(python3 /opt/plugin/scripts/retro-check lessons)",
    "Bash(python3 /opt/plugin/scripts/retro-check scrub *)",
    "Bash(python3 /opt/plugin/scripts/retro-check publish *)",
    "Edit(//h/.software-factory/retros/2026-11-14/alpha/work/**)",
    "Edit(//h/.software-factory/upstream/**)",
])
STATE_H = Path("/h/.software-factory")
RUN_H = STATE_H / "retros" / "2026-11-14" / "alpha"


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
        argv = mock.patch.object(self.daily.sys, "argv", ["retro-daily"])  # not the test runner's own arguments
        argv.start()
        self.addCleanup(argv.stop)

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
        self.assertEqual(self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H, "/w/alpha", "alpha"), EXPECTED_TOOLS)
        self.assertEqual(self.daily.disallowed_tools(Path("/h"), "/w/alpha"), EXPECTED_DENIED)

    def test_verdicts_rule_is_the_exact_argv_of_this_run_with_no_wildcard(self):  # #204
        rules = self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H, "/w/alpha", "alpha")
        verdicts = [r for r in rules if "verdicts" in r]
        self.assertEqual(verdicts, ["Bash(python3 /opt/plugin/scripts/retro-check verdicts --project /w/alpha "
                                    "--metrics /h/.software-factory/retros/2026-11-14/alpha/alpha.json)"])
        self.assertNotIn("*", verdicts[0])

    def test_no_read_rule_covers_the_home_folder_or_a_secret_folder_and_nothing_is_unscoped(self):  # #197
        rules = self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H, "/w/alpha", "alpha")
        for bare in ("Read", "Grep", "Glob"):
            self.assertNotIn(bare, rules)
        reads = {r for r in rules if r.startswith("Read(")}
        self.assertEqual(len(reads), 5)
        self.assertFalse(reads & {"Read(//h/**)", "Read(///**)", "Read(//**)", "Read(//h/.ssh/**)", "Read(//h/.aws/**)",
                                  "Read(//h/.config/**)", "Read(//h/.config/gcloud/**)", "Read(//h/.software-factory/**)"})
        self.assertFalse([r for r in rules if "retro.log" in r or "private-words" in r])

    def test_a_project_that_is_the_home_folder_or_root_is_refused_with_bad_project(self):  # #197
        for bad in (str(Path.home()), "/"):
            with mock.patch.object(self.daily.subprocess, "run") as run:
                word = self.daily.run_agent(Path("/opt/plugin"), self.state, self.base, {"project": bad}, "x", self.base / "log")
            self.assertEqual(word, "bad-project")
            run.assert_not_called()

    def test_allowed_tools_have_no_wildcard_check_no_network_scan_or_launcher_file_write(self):  # #172, #154, #192
        rules = self.daily.allowed_tools(Path("/opt/plugin"), STATE_H, RUN_H, "/w/alpha", "alpha")
        self.assertNotIn("Bash(python3 /opt/plugin/scripts/retro-check *)", rules)
        self.assertFalse([r for r in rules if r.startswith("Bash(") and "retro-check" not in r])
        for banned in ("WebFetch", "WebSearch", "mcp__", "git", "gh ", "curl", "retro-scan", "Write("):
            self.assertFalse([r for r in rules if banned in r], banned)
        edits = [r for r in rules if r.startswith("Edit(")]
        self.assertEqual(len(edits), 2)
        for guarded in ("overlay", "retro-settings.json", "retro-failed", "metrics", "due.json", "alpha.json"):
            self.assertFalse([r for r in edits if guarded in r], guarded)
        self.assertFalse([r for r in edits if r.endswith("/alpha/**)")])  # work/ only, not the whole run folder

    def test_each_project_gets_its_own_folder_and_the_rules_of_one_do_not_cover_another(self):  # #200
        code, calls = self.run_main(["/w/alpha", "/w/beta"], [0, 0])
        day = next((self.state / "retros").iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in day.iterdir()), ["alpha", "beta"])
        self.assertEqual(json.loads((day / "alpha" / "due.json").read_text()), ["/w/alpha"])
        self.assertEqual(json.loads((day / "beta" / "due.json").read_text()), ["/w/beta"])
        self.assertFalse((day / "due.json").exists())
        for (cmd, kw), mine, other in zip(calls, ("alpha", "beta"), ("beta", "alpha")):
            rules = " ".join(cmd[cmd.index("--allowedTools") + 1:])
            self.assertEqual(kw["cwd"], str(day / mine))
            self.assertIn(f"{day}/{mine}/", rules)
            self.assertNotIn(f"{day}/{other}", rules)
            self.assertNotIn(f"{day}/**", rules)

    def test_headless_call_is_locked_down_and_the_scan_is_made_by_the_launcher(self):  # #193, #192
        code, calls = self.run_main(["/w/alpha"], [0])
        (cmd, kw), = calls
        run_dir = next(next((self.state / "retros").iterdir()).iterdir())
        prompt = cmd[2]
        self.assertEqual(code, 0)
        self.assertEqual(cmd, ["claude", "-p", prompt, "--plugin-dir", "/opt/plugin", "--agent", "software-factory:retro",
                               "--permission-mode", "dontAsk", "--setting-sources", "project", "--strict-mcp-config",
                               "--disallowedTools", *self.daily.disallowed_tools(Path.home(), "/w/alpha"),
                               "--allowedTools", *self.daily.allowed_tools(Path("/opt/plugin"), self.state, run_dir, "/w/alpha", "alpha")])
        self.assertTrue(all("," not in rule for rule in cmd[cmd.index("--disallowedTools") + 1:]))
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
        self.assertEqual((self.state / "retro-failed").read_text(), "scan-failed 2 alpha\n")

    def test_one_failing_project_does_not_stop_the_others_and_marker_holds_no_child_output(self):
        code, calls = self.run_main(["/w/alpha", "/w/beta", "/w/gamma"], [0, 3, 0])
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertEqual((self.state / "retro-failed").read_text(), "agent-failed 3 beta\n")

    def test_slugs_are_unique_per_run_with_a_path_hash_only_on_a_collision(self):
        slugs = self.daily.slug_map(["/a/web-app", "/b/web-app", "/c/other"])
        self.assertEqual(slugs["/c/other"], "other")
        self.assertEqual(len({slugs["/a/web-app"], slugs["/b/web-app"]}), 2)
        self.assertTrue(all(slugs[p].startswith("web-app-") for p in ("/a/web-app", "/b/web-app")))
        self.assertEqual(slugs, self.daily.slug_map(["/a/web-app", "/b/web-app", "/c/other"]))

    def test_only_the_daily_cap_of_projects_run_and_the_rest_wait_for_tomorrow(self):
        names = [f"/w/p{n}" for n in range(self.daily.MAX_PROJECTS_PER_DAY + 2)]
        code, calls = self.run_main(names, [0] * len(names))
        day = next((self.state / "retros").iterdir())
        self.assertEqual((code, len(calls)), (0, 5))
        self.assertEqual(sorted(p.name for p in day.iterdir()), [f"p{n}" for n in range(5)])

    def test_a_due_row_without_since_is_scanned_without_the_flag(self):
        run_dir = self.base / "run"
        with mock.patch.object(self.daily.subprocess, "run", return_value=done()) as run:
            self.daily.scan_project(Path("/opt/plugin"), run_dir, {"project": "/w/alpha"}, "alpha")
        self.assertNotIn("--since", run.call_args[0][0])

    def test_the_log_gets_one_separator_per_run_and_rotates_over_one_megabyte(self):
        log = self.state / "retro.log"
        self.state.mkdir(parents=True)
        log.write_text("x" * (self.daily.LOG_LIMIT + 1))
        self.daily.start_log(log)
        self.daily.start_log(log)
        self.assertEqual(log.with_name("retro.log.1").stat().st_size, self.daily.LOG_LIMIT + 1)
        self.assertEqual([l[:3] for l in log.read_text().splitlines()], ["==="] * 2)

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

    def test_a_scan_blocked_by_macos_privacy_is_named_as_such(self):
        skip = "retro-scan: skipped /w/alpha: fatal: Unable to read current working directory: Operation not permitted"
        scan = subprocess.CompletedProcess([], 0, "[]", skip)
        with mock.patch.object(self.daily.subprocess, "run", return_value=scan):
            self.assertEqual(self.daily.main(), 1)
        self.assertEqual((self.state / "retro-failed").read_text(), "macos-privacy-blocked\n")
        log = (self.state / "retro.log").read_text().splitlines()
        self.assertEqual((log[0][:3], log[1:]), ("===", [skip]))

    def test_under_scopes_the_due_scan_to_the_session_folder(self):
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return done(0, "[]")

        with mock.patch.object(self.daily.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(self.daily.sys, "argv", ["retro-daily", "--under", "/w/alpha"]):
            self.assertEqual(self.daily.main(), 0)
        self.assertEqual(seen[0][-3:], ["--due", "--under", "/w/alpha"])

    def test_a_bad_argument_is_refused_before_any_scan(self):
        with mock.patch.object(self.daily.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(self.daily.sys, "argv", ["retro-daily", "--under"]):
            self.assertEqual(self.daily.main(), 2)
        run.assert_not_called()

    def test_a_second_run_on_the_same_day_skips_a_project_the_first_already_started(self):
        with mock.patch.object(self.daily.sys, "argv", ["retro-daily"]):
            self.assertEqual(len(self.run_main(["/w/alpha"], [0])[1]), 1)
            self.assertEqual(self.run_main(["/w/alpha"], [0]), (0, []))

    def test_a_project_run_under_another_slug_earlier_today_is_not_run_again(self):
        self.assertEqual(len(self.run_main(["/a/web", "/b/web"], [0, 0])[1]), 2)  # slugs web-<hash> here
        self.assertEqual(self.run_main(["/a/web"], [0]), (0, []))  # plain slug web now, same project

    def test_a_run_folder_used_today_by_another_project_is_not_shared(self):
        self.run_main(["/a/web"], [0])
        self.run_main(["/b/web"], [0])
        day = next((self.state / "retros").iterdir())
        listed = sorted(json.loads((f / "due.json").read_text()) for f in day.iterdir())
        self.assertEqual(listed, [["/a/web"], ["/b/web"]])

    def test_a_run_whose_projects_another_run_has_leaves_its_failure_in_place(self):
        self.run_main(["/w/alpha"], [1])
        self.assertEqual(self.run_main(["/w/alpha"], []), (0, []))
        self.assertTrue((self.state / "retro-failed").exists())

    def test_the_headless_child_is_not_marked_as_nested_in_the_session_that_started_it(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            self.assertNotIn("CLAUDECODE", self.daily.agent_env())

    def test_a_run_with_nothing_due_still_logs_and_clears_an_old_failure(self):
        self.state.mkdir(parents=True)
        (self.state / "retro-failed").write_text("macos-privacy-blocked\n")
        self.assertEqual(self.run_main([], [])[0], 0)
        self.assertFalse((self.state / "retro-failed").exists())
        self.assertEqual([l[:3] for l in (self.state / "retro.log").read_text().splitlines()], ["===", "SEC"])

    def plant(self, cache, marketplace, versions):
        for v in versions:
            (cache / marketplace / "software-factory" / v / "agents").mkdir(parents=True)
            (cache / marketplace / "software-factory" / v / "agents" / "retro.md").write_text("x")

    def test_newest_plugin_copy_wins(self):
        cache = self.base / "cache"
        self.plant(cache, "software-factory-marketplace", ("0.9.0", "0.10.0", "0.9.5"))
        self.assertEqual(self.daily.newest_plugin(cache).name, "0.10.0")

    def test_a_copy_from_another_marketplace_is_never_picked(self):  # #183
        cache = self.base / "cache"
        self.plant(cache, "lookalike-marketplace", ("99.0.0",))
        self.assertIsNone(self.daily.newest_plugin(cache))
        self.plant(cache, "software-factory-marketplace", ("0.9.0",))
        self.assertEqual(self.daily.newest_plugin(cache).name, "0.9.0")

    def test_a_project_that_resolves_to_home_a_parent_of_home_or_root_is_too_broad(self):  # #183
        home = self.base / "users" / "pat"
        link = self.base / "link-to-home"
        home.mkdir(parents=True)
        link.symlink_to(home)
        inside = home / "work" / "alpha"
        inside.mkdir(parents=True)
        with mock.patch.object(self.daily.Path, "home", return_value=home):
            for broad in (home, home.parent, link, "/", home / "work" / ".."):
                self.assertTrue(self.daily.too_broad(str(broad)), broad)
            self.assertFalse(self.daily.too_broad(str(inside)))

    def test_project_secret_files_are_denied_to_the_agent(self):  # #183
        denied = self.daily.disallowed_tools(Path("/h"), "/w/alpha")
        for glob in (".npmrc", ".netrc", ".pypirc", "id_*", "*.p12"):
            self.assertIn(f"Read(//w/alpha/**/{glob})", denied)


    def test_check_mode_logs_one_line_with_the_due_count_and_env_and_runs_nothing_else(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return done(0, json.dumps([{"project": "/w/alpha"}, {"project": "/w/beta"}]))

        with mock.patch.object(self.daily.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(self.daily.sys, "argv", ["retro-daily", "--check"]), \
                mock.patch.dict(os.environ, {"PATH": "/usr/bin", "HOME": "/h"}):
            self.assertEqual(self.daily.main(), 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("--due", calls[0])
        line = (self.state / "retro.log").read_text().splitlines()
        self.assertEqual(len(line), 1)
        self.assertRegex(line[0], r"^retro check \S+ due=2 PATH=set HOME=/h SOFTWARE_FACTORY_HOME=.*/state$")
        self.assertEqual(list(self.state.iterdir()), [self.state / "retro.log"])

    def test_check_mode_reports_an_unset_path_and_a_failed_scan_and_still_exits_0(self):
        env = {k: v for k, v in os.environ.items() if k != "PATH"}
        with mock.patch.object(self.daily.subprocess, "run", return_value=done(2)), \
                mock.patch.object(self.daily.sys, "argv", ["retro-daily", "--check"]), \
                mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.daily.main(), 0)
        self.assertRegex((self.state / "retro.log").read_text(), r"due=unknown \(scan-failed 2\) PATH=unset ")


class SharedStateDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="retro-state-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        (self.base / "sibling" / ".git").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.base / "home"),
                                           "SOFTWARE_FACTORY_HOME": str(self.base / "sibling" / "state")})
        env.start()
        self.addCleanup(env.stop)

    def test_launcher_and_schedule_refuse_a_state_folder_inside_a_git_work_tree(self):  # #204
        for name in ("retro-daily", "retro-schedule"):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load(name).state_dir(), self.base / "home" / ".software-factory", name)

    def test_launcher_copied_away_from_hooks_uses_the_plugin_root_it_finds(self):
        daily = load("retro-daily")
        elsewhere = self.base / "state" / "bin" / "retro-daily"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("")
        with mock.patch.object(daily, "__file__", str(elsewhere), create=True), \
                mock.patch.object(daily, "newest_plugin", return_value=ROOT) as found, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(daily.state_dir(), self.base / "home" / ".software-factory")
        found.assert_called_once()

    def test_launcher_without_any_plugin_falls_back_to_the_plain_state_folder(self):
        daily = load("retro-daily")
        elsewhere = self.base / "state" / "bin" / "retro-daily"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("")
        with mock.patch.object(daily, "__file__", str(elsewhere), create=True), \
                mock.patch.object(daily, "newest_plugin", return_value=None):
            self.assertEqual(daily.state_dir(), self.base / "sibling" / "state")


class RetroScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="retro-schedule-")
        self.home = Path(os.path.realpath(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        self.state = self.home / "state"
        self.schedule = load("retro-schedule")
        self.crontab = ["5 * * * * backup.sh", "0 9 * * * x retro-daily # software-factory-retro",
                        "0 9 * * * x retro-daily --check # software-factory-retro.check"]
        self.calls = []

    def fake_job_tools(self, platform):
        """Temp HOME, and no real launchctl or crontab. subprocess.run is patched only inside these tests."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.home), "SOFTWARE_FACTORY_HOME": str(self.state)}))
        stack.enter_context(mock.patch.object(self.schedule.subprocess, "run", side_effect=self.fake_run))
        stack.enter_context(mock.patch.object(self.schedule.sys, "platform", platform))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return stack

    def fake_run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["crontab", "-"]:
            self.crontab = kw["input"].splitlines()
        return subprocess.CompletedProcess(cmd, 0, "\n".join(self.crontab), "")

    def plant_old_job(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        for name in ("dev.software-factory.retro.plist", "dev.software-factory.retro.check.plist", "other.plist"):
            (agents / name).write_text("x")
        (self.state / "bin").mkdir(parents=True)
        (self.state / "bin" / "retro-daily").write_text("x")
        return agents

    def test_install_turns_the_session_start_retro_on_and_removes_the_old_launchd_job(self):
        agents = self.plant_old_job()
        with self.fake_job_tools("darwin"):
            self.assertEqual(self.schedule.main(["install"]), 0)
        self.assertTrue((self.state / "retro-auto").is_file())
        self.assertEqual([p.name for p in agents.iterdir()], ["other.plist"])
        self.assertFalse((self.state / "bin" / "retro-daily").exists())
        for label in ("dev.software-factory.retro", "dev.software-factory.retro.check"):
            self.assertIn(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], self.calls)
        self.assertFalse(any(c[:2] == ["launchctl", "bootstrap"] for c in self.calls))

    def test_uninstall_turns_it_off(self):
        with self.fake_job_tools("darwin"):
            self.schedule.main(["install"])
            self.schedule.main(["uninstall"])
        self.assertFalse((self.state / "retro-auto").exists())

    def test_old_cron_lines_go_and_other_lines_stay(self):
        with self.fake_job_tools("linux"):
            self.schedule.main(["install"])
        self.assertEqual(self.crontab, ["5 * * * * backup.sh"])
        self.calls.clear()
        with self.fake_job_tools("linux"):
            self.schedule.main(["uninstall"])
        self.assertEqual(self.calls, [["crontab", "-l"]])  # nothing to remove, so the crontab is not rewritten

    def test_a_machine_without_crontab_still_installs(self):
        with self.fake_job_tools("linux"), mock.patch.object(self.schedule.subprocess, "run", side_effect=OSError):
            self.assertEqual(self.schedule.main(["install"]), 0)
        self.assertTrue((self.state / "retro-auto").is_file())


if __name__ == "__main__":
    unittest.main()

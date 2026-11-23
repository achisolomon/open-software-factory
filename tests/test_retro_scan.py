"""Tests for scripts/retro-scan on fixture transcripts."""

import ast
import contextlib
import datetime
import errno
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

DAY = 86400
ROOT = Path(__file__).resolve().parents[1] / "plugin"
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
        self.stderr = r.stderr
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

    def factory(self, *parts):
        folder = self.f.base.joinpath(*parts)
        (folder / "docs" / "factory").mkdir(parents=True)
        (folder / "docs" / "factory" / "status.md").write_text("# Status\n")
        return str(folder)

    def edit(self, call_id, file_path, t, cwd):
        return {"type": "assistant", "timestamp": iso(t), "cwd": cwd, "message": {"content": [
            {"type": "tool_use", "id": call_id, "name": "Edit", "input": {"file_path": file_path}}]}}

    def test_a_parent_folder_session_counts_for_the_project_it_worked_in_most(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        other = self.factory("work", "group", "other")
        rows = [user("start", NOW - 90, work), self.edit("e1", repo + "/README.md", NOW - 80, work),
                self.edit("e2", other + "/a.py", NOW - 70, work), self.edit("e3", other + "/b.py", NOW - 60, work),
                bash("b1", f'git -C "{other}" status', NOW - 50, work),
                user("next", NOW - 40, other + "/.claude/worktrees/x")]
        self.f.session(work, rows)
        self.f.session(work, [user("hi", NOW - 30, work)] + [self.edit(f"e{k}", f"{repo}/f{k}.py", NOW - 20 + k, work)
                                                             for k in range(3)])
        self.f.session(work, [user("only talk", NOW - 30, work)])
        found = {p["project"]: p for p in self.f.run("--discover")}
        self.assertEqual(sorted(found), sorted([other, repo]))
        self.assertEqual((found[other]["sessions"], found[repo]["sessions"]), (1, 1))
        self.assertEqual(found[other]["worktrees"], [])
        measured = self.f.run("--project", other, "--since", iso(NOW - 3600), "--no-github")
        self.assertEqual(measured["sessions"], 1)

    def test_a_longer_folder_name_or_a_deep_or_hidden_project_is_not_matched(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        self.factory("work", "a", "b", "c", "deep")
        self.factory("work", ".hidden", "proj")
        self.f.session(work, [user("go", NOW - 60, work), self.edit("e1", repo + "-old/x.py", NOW - 50, work),
                              self.edit("e2", work + "/a/b/c/deep/x.py", NOW - 40, work),
                              self.edit("e3", work + "/.hidden/proj/x.py", NOW - 30, work)])
        self.assertEqual(self.f.run("--discover"), [])

    def test_a_session_in_the_home_folder_is_never_mapped_to_a_project_below_it(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        self.f.session(work, [user("go", NOW - 60, work), self.edit("e1", repo + "/x.py", NOW - 50, work)])
        self.assertEqual(self.f.run("--discover", HOME=work), [])

    def test_a_live_sibling_worktree_with_its_own_status_file_counts_for_its_main_checkout(self):
        repo = str(self.f.repo)
        sibling = self.factory("work", "worktrees", "fix-1")
        relative = self.factory("work", "worktrees", "fix-2")
        Path(sibling, ".git").write_text(f"gitdir: {repo}/.git/worktrees/fix-1\n")
        Path(relative, ".git").write_text("gitdir: ../../app/.git/worktrees/fix-2\n")  # git 2.48 relative paths
        for cwd in (sibling + "/src", sibling + "/docs", relative):
            self.f.session(cwd, [user("go", NOW - 60, cwd)])
        found = self.f.run("--discover")
        self.assertEqual([(p["project"], p["sessions"], p["worktrees"]) for p in found],
                         [(repo, 3, [sibling, relative])])

    def test_a_stray_mention_or_tool_output_does_not_pull_a_session_into_a_project(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        notes = work + "/notes"
        (self.f.base / "work" / "notes" / ".git").mkdir()  # a real repo, so work there votes elsewhere
        self.f.session(work, [user("go", NOW - 90, work), self.edit("e1", notes + "/a.md", NOW - 80, work),
                              self.edit("e2", notes + "/b.md", NOW - 70, work), bash("b1", f"ls {repo}", NOW - 60, work),
                              bash("b2", "ls", NOW - 50, notes), result("b2", f"see {repo}/x {repo}/y", NOW - 49, notes)])
        self.f.session(work, [user(f"look at {repo}/README.md", NOW - 40, work), bash("b3", "ls", NOW - 30, work),
                              result("b3", f"{repo}/README.md", NOW - 29, work, error=False)])
        self.assertEqual(self.f.run("--discover"), [])

    def test_an_even_split_between_two_projects_counts_for_neither(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        other = self.factory("work", "other")
        rows = [user("go", NOW - 60, work)]
        rows += [self.edit(f"e{k}", f"{(repo, other)[k % 2]}/f{k}", NOW - 50 + k, work) for k in range(6)]
        self.f.session(work, rows)
        self.assertEqual(self.f.run("--discover"), [])

    def test_work_in_a_deleted_folder_votes_for_no_one_and_one_or_two_calls_are_not_enough(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        gone = work + "/worktrees/removed-fix"  # a sibling worktree deleted since; its repo cannot be told
        left = self.f.base / "work" / "worktrees" / "left-behind" / "node_modules"  # removed worktree, no .git left
        left.mkdir(parents=True)
        rows = [user("go", NOW - 90, work)] + [bash(f"g{k}", "npm test", NOW - 80 + k, gone) for k in range(10)]
        rows += [bash(f"l{k}", f"ls {left}", NOW - 70 + k, str(left.parent)) for k in range(10)]
        self.f.session(work, rows + [self.edit(f"e{k}", f"{repo}/f{k}", NOW - 50 + k, work) for k in range(3)])
        self.f.session(work, rows + [self.edit(f"e{k}", f"{repo}/f{k}", NOW - 50 + k, work) for k in range(2)])
        self.assertEqual([(p["project"], p["sessions"]) for p in self.f.run("--discover")], [(repo, 1)])

    def test_a_path_ended_by_shell_punctuation_still_counts(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        self.f.session(work, [user("go", NOW - 60, work), bash("b1", f"cd {repo}; npm test", NOW - 50, work),
                              bash("b2", f"(cd {repo}) && ls", NOW - 40, work),
                              bash("b3", f"echo {repo}:{repo},", NOW - 30, work)])
        self.assertEqual([p["project"] for p in self.f.run("--discover")], [repo])

    def test_work_in_another_repo_with_a_space_in_its_name_counts_against_the_project(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        other = self.f.base / "work" / "my repo"
        (other / ".git").mkdir(parents=True)
        (other / "src").mkdir()
        rows = [user("go", NOW - 90, work)] + [self.edit(f"e{k}", f"{repo}/f{k}", NOW - 80 + k, work) for k in range(3)]
        rows += [self.edit(f"o{k}", f"{other}/src/x{k}.py", NOW - 60 + k, work) for k in range(5)]
        rows += [bash("b1", f"ls {other}/src && ls -la", NOW - 40, work)]
        self.f.session(work, rows)
        self.assertEqual(self.f.run("--discover"), [])

    def test_a_linked_worktree_beside_its_project_counts_for_that_project(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        beside = self.f.base / "work" / "app-feature"
        beside.mkdir()
        (beside / ".git").write_text(f"gitdir: {repo}/.git/worktrees/app-feature\n")
        self.f.session(work, [user("go", NOW - 60, work)] + [
            self.edit(f"e{k}", f"{beside}/f{k}.py", NOW - 50 + k, str(beside)) for k in range(3)])
        self.assertEqual([(p["project"], p["sessions"]) for p in self.f.run("--discover")], [(repo, 1)])

    def test_a_folder_macos_privacy_hides_is_reported_with_the_eperm_text(self):
        loader = importlib.machinery.SourceFileLoader("retro_scan_eperm", str(SCRIPT))
        rs = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(rs)
        hidden = str(self.f.base / "work")
        with mock.patch.object(rs.os, "scandir", side_effect=PermissionError(errno.EPERM, os.strerror(errno.EPERM))):
            self.assertEqual(rs.factory_below(hidden), ())
        self.assertEqual(rs.BLOCKED, {hidden})

    def test_a_scoped_due_run_reports_only_blocked_folders_in_its_scope(self):
        loader = importlib.machinery.SourceFileLoader("retro_scan_scope", str(SCRIPT))
        rs = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(rs)
        mine, other = str(self.f.base / "work"), str(self.f.base / "elsewhere")
        for under, expected in ((mine, False), (other, True)):
            rs.BLOCKED.clear()
            rs.BLOCKED.add(other)
            err = io.StringIO()
            with mock.patch.object(rs, "due", return_value=([], [])), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                rs.main(["--due", "--under", under])
            self.assertEqual("cannot read 1 folder(s)" in err.getvalue(), expected, under)

    def test_an_unreadable_folder_below_the_session_folder_does_not_stop_the_scan(self):
        work, repo = str(self.f.base / "work"), str(self.f.repo)
        locked = self.f.base / "work" / "locked" / "inner"
        locked.mkdir(parents=True)
        self.f.session(work, [user("go", NOW - 60, work)] + [self.edit(f"e{k}", f"{repo}/f{k}", NOW - 50 + k, work)
                                                             for k in range(3)])
        locked.parent.chmod(0)
        try:
            found = self.f.run("--discover")
        finally:
            locked.parent.chmod(0o755)
        self.assertEqual([p["project"] for p in found], [repo])

    def test_only_sessions_from_the_last_30_days_count_and_last_active_is_the_newest(self):
        repo, newest = str(self.f.repo), int(NOW) - 1000
        for age in (newest, NOW - 20 * DAY, NOW - 40 * DAY):
            path = self.f.session(repo, [user("go", NOW - 100, repo)])
            os.utime(path, (age, age))
        found = self.f.run("--discover")
        self.assertEqual(found[0]["sessions"], 2)
        self.assertEqual(found[0]["last_active"], datetime.datetime.fromtimestamp(
            newest, datetime.timezone.utc).isoformat(timespec="seconds"))


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

    def test_approvals_ask_rule_writes_error_share_and_message_filters(self):
        self.f.state.mkdir(parents=True)
        (self.f.state / "settings.json").write_text(json.dumps({"permissions": {"ask": ["Bash(git push:*)"]}}))
        r, t = self.repo, NOW - 1800
        rows = [
            user("no, stop that", t, r),
            user("no " + "x" * 700, t + 1, r),
            user("<command-message>no, stop</command-message>", t + 2, r),
            bash("a", "git push origin feature/x", t + 3, r),
            result("a", "The user doesn't want to proceed with this tool use", t + 4, r),
            bash("b", "ls", t + 5, r),
            result("b", "Permission denied by auto mode classifier", t + 6, r),
        ]
        dev = {"agentType": "software-factory:software-developer", "model": "sonnet"}
        sub_rows = [user("no, stop it", t, r), bash("s", "npm test", t + 7, r), result("s", "FAIL a.test.ts", t + 8, r)]
        self.f.session(r, rows, subagents=[(dev, sub_rows)])
        m = self.measure()
        self.assertEqual(m["approvals"], {"user-rejected": 1, "classifier-denied": 1})
        self.assertEqual(m["gh_writes"], {"ask-rule": 1})
        self.assertEqual(m["subagent_error_share"], 0.33)
        self.assertEqual(m["user_message_counts"], {"correction": 1})

    def test_ci_runs_that_started_before_the_window_are_not_counted(self):
        bin_dir = self.f.base / "bin"
        bin_dir.mkdir()
        runs = [{"createdAt": iso(NOW - 7200), "updatedAt": iso(NOW - 7000), "conclusion": "failure"},
                {"createdAt": iso(NOW - 900), "updatedAt": iso(NOW - 300), "conclusion": "success"}]
        (bin_dir / "gh").write_text(f"#!/bin/sh\nif [ \"$1\" = pr ]; then echo '[]'; else echo '{json.dumps(runs)}'; fi\n")
        (bin_dir / "gh").chmod(0o755)
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        m = self.f.run("--project", self.repo, "--since", iso(NOW - 3600), PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.assertEqual((m["github"]["ci_runs"], m["github"]["ci_failed"]), (1, 0))

    def test_an_invalid_since_is_a_usage_error(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", self.repo, "--since", "last week"],
                           capture_output=True, text=True, env=self.f.env())
        self.assertEqual(r.returncode, 2)
        self.assertIn("--since is not an ISO time: last week", r.stderr)

    def test_out_writes_the_json_to_the_file_and_prints_its_path(self):
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        out = self.f.state / "retros" / "2026-11-14" / "m.json"
        r = self.scan_to(out)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(out)))
        self.assertEqual(json.loads(out.read_text())["project"], self.repo)

    def scan_to(self, out):
        return subprocess.run([sys.executable, str(SCRIPT), "--project", self.repo, "--since", iso(NOW - 3600),
                               "--no-github", "--out", str(out)], capture_output=True, text=True, env=self.f.env())

    def test_due_out_writes_due_json_like_the_daily_launcher(self):
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        run_dir = self.f.state / "retros" / "2026-11-14"
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", self.repo, "--since", iso(NOW - 3600),
                            "--no-github", "--out", str(run_dir / "m.json"), "--due-out", str(run_dir)],
                           capture_output=True, text=True, env=self.f.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((run_dir / "due.json").read_text(), json.dumps([self.repo]) + "\n")

    def test_due_out_outside_the_retros_folder_is_refused(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", self.repo, "--no-github",
                            "--due-out", str(self.f.state / "overlay")], capture_output=True, text=True,
                           env=self.f.env())
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.f.state / "overlay" / "due.json").exists())

    def test_due_out_for_a_project_the_scan_did_not_discover_is_refused(self):  # #183
        other = self.f.base / "elsewhere"
        other.mkdir()
        run_dir = self.f.state / "retros" / "2026-11-14"
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(other), "--no-github",
                            "--due-out", str(run_dir)], capture_output=True, text=True, env=self.f.env())
        self.assertEqual(r.returncode, 2)
        self.assertIn("discovers", r.stdout)
        self.assertFalse((run_dir / "due.json").exists())

    def test_out_outside_the_retros_folder_is_refused(self):
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        for out in (self.f.base / "m.json", self.f.state / "overlay" / "entries.jsonl",
                    self.f.state / "retros" / ".." / "m.json"):
            r = self.scan_to(out)
            self.assertEqual(r.returncode, 2, out)
            self.assertIn("error", json.loads(r.stdout))
            self.assertFalse(out.exists(), out)

    def test_out_through_a_symlink_is_refused(self):
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        retros = self.f.state / "retros"
        retros.mkdir(parents=True)
        victim = self.f.base / "victim.json"
        victim.write_text("keep")
        (retros / "link.json").symlink_to(victim)
        (retros / "dirlink").symlink_to(self.f.base)
        for out in (retros / "link.json", retros / "dirlink" / "m.json"):
            self.assertEqual(self.scan_to(out).returncode, 2, out)
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse((self.f.base / "m.json").exists())

    def test_github_failure_is_reported_not_fatal(self):
        bin_dir = self.f.base / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
        (bin_dir / "gh").chmod(0o755)
        r = self.repo
        self.f.session(r, [user("go", NOW - 100, r)])
        m = self.f.run("--project", r, "--since", iso(NOW - 3600), PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.assertIn("not logged in", m["github"]["error"])


def load_module():
    loader = importlib.machinery.SourceFileLoader("retro_scan_under_test", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.repo = str(self.f.repo)
        self.scan = load_module()
        patcher = mock.patch.dict(os.environ, self.f.env())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.f.tmp.cleanup()

    def measure(self):
        return self.f.run("--project", self.repo, "--since", iso(NOW - 3600), "--no-github")

    def test_script_parses_as_python_3_9(self):
        ast.parse(SCRIPT.read_text(), feature_version=(3, 9))

    def test_non_string_cwd_is_skipped_and_the_scan_goes_on(self):
        r = self.repo
        self.f.session(r, [{"type": "user", "timestamp": iso(NOW - 60), "cwd": 5}])
        self.f.session(r, [user("fine", NOW - 30, r)])
        found = self.f.run("--discover")
        self.assertEqual([p["sessions"] for p in found], [1])

    def test_unreadable_file_is_skipped_and_counted(self):
        r = self.repo
        self.f.session(r, [user("fine", NOW - 30, r)])
        locked = self.f.session(r, [user("locked", NOW - 20, r)])
        locked.chmod(0o000)
        try:
            self.assertEqual([p["sessions"] for p in self.f.run("--discover")], [1])
            self.assertIn("1 unreadable", self.f.stderr)
            self.assertEqual(self.scan.session_cwd(locked), None)
            self.assertEqual(self.scan.SKIPPED, {str(locked)})
        finally:
            locked.chmod(0o644)

    def test_file_removed_between_glob_and_stat_is_skipped(self):
        ghost = self.f.projects / "-gone" / "ghost.jsonl"
        with mock.patch.object(Path, "glob", return_value=[ghost]):
            self.assertEqual(list(self.scan.main_sessions(0)), [])

    def test_bad_json_line_and_truncated_last_line_are_ignored(self):
        r = self.repo
        path = self.f.session(r, [user("no, stop", NOW - 30, r)])
        path.write_text("not json\n" + path.read_text() + '{"type": "us')
        self.assertEqual(self.measure()["user_message_counts"], {"correction": 1})

    def test_odd_field_types_do_not_crash_the_scan(self):
        r, t = self.repo, NOW - 60
        odd = [
            {"type": "user", "timestamp": iso(t), "cwd": r, "message": "just a string"},
            {"type": "user", "timestamp": iso(t), "cwd": r, "message": {"content": [{"type": "text", "text": 7}]}},
            {"type": "assistant", "timestamp": iso(t), "cwd": r, "message": {"content": [
                {"type": "tool_use", "id": ["x"], "name": 5, "input": "oops"},
                {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": 42}},
                {"type": "tool_result", "tool_use_id": ["x"], "content": None, "is_error": True}]}},
            {"type": "user", "timestamp": 12345, "cwd": r},
        ]
        self.f.session(r, odd)
        m = self.measure()
        self.assertEqual((m["sessions"], m["tool_calls"], m["errors_by_class"]), (1, 2, {"other": 1}))

    def test_git_push_text_inside_a_commit_message_is_not_a_write(self):
        r = self.repo
        self.f.session(r, [bash("a", 'git commit -m "docs: say when to git push"', NOW - 50, r),
                           bash("b", "git push origin feature/x", NOW - 40, r)])
        self.assertEqual(self.measure()["gh_writes"], {"sandboxed": 1})

    def test_subagent_log_older_than_the_window_is_not_parsed(self):
        r, t = self.repo, NOW - 600
        meta = {"agentType": "software-factory:software-developer", "model": "sonnet"}
        path = self.f.session(r, [user("go", t, r)], subagents=[(meta, [bash("s", "ls", t, r), bash("s2", "ls", t + 60, r)])])
        old = NOW - 10 * DAY
        for sub in (path.parent / path.stem / "subagents").glob("agent-0.jsonl"):
            os.utime(sub, (old, old))
        self.assertEqual(self.measure()["dispatches"], {})

    def test_rows_after_the_until_bound_are_left_out(self):
        r = self.repo
        self.f.session(r, [user("no, stop", NOW - 100, r), user("no, wrong", NOW - 10, r)])
        m = self.scan.measure(r, NOW - 3600, until=NOW - 50, with_github=False)
        self.assertEqual(m["user_message_counts"], {"correction": 1})

    def test_messages_of_subagent_logs_are_not_collected(self):
        path = self.f.session(self.repo, [user("no, stop", NOW - 100, self.repo)])
        self.assertEqual(self.scan.scan_file(path, 0, NOW + 1, sub=True)["messages"], [])
        self.assertEqual(self.scan.scan_file(path, 0, NOW + 1)["messages"], ["no, stop"])

    def test_p90_picks_the_value_at_ninety_percent_and_clamps_to_the_last(self):
        self.assertEqual((self.scan.p90(list(range(1, 11))), self.scan.p90([5])), (10, 5))
        runs = [{"model": "m", "minutes": n, "calls": 1, "errors": 0} for n in range(1, 11)]
        self.assertEqual(self.scan.dispatch_table({"a": runs})["a"]["p90_min"], 10.0)

    def test_due_scans_transcripts_once_for_all_projects(self):
        other = self.f.base / "work" / "other"
        (other / "docs" / "factory").mkdir(parents=True)
        (other / "docs" / "factory" / "status.md").write_text("# Status\n")
        for repo in (self.repo, str(other)):
            self.f.session(repo, [user("a", NOW - 100, repo)])
            self.f.session(repo, [user("b", NOW - 50, repo)])
        with mock.patch.object(self.scan, "remote_main", return_value=("abc", None)), \
                mock.patch.object(self.scan, "session_cwd", wraps=self.scan.session_cwd) as reads:
            due, skipped = self.scan.due()
        self.assertEqual((len(due), skipped, reads.call_count), (2, [], 4))

    def test_due_under_a_folder_checks_only_the_projects_it_covers(self):
        work = self.f.base / "work"
        other = work / "other"
        (other / "docs" / "factory").mkdir(parents=True)
        (other / "docs" / "factory" / "status.md").write_text("# Status\n")
        for repo in (self.repo, str(other)):
            self.f.session(repo, [user("a", NOW - 100, repo)])
            self.f.session(repo, [user("b", NOW - 50, repo)])
        asked = []
        with mock.patch.object(self.scan, "remote_main", side_effect=lambda p: asked.append(p) or ("abc", None)):
            inside = self.scan.due(self.repo + "/.claude/worktrees/fix")[0]
            beside = self.scan.due(str(work))[0]
            self.scan.factory_below.cache_clear()
            with mock.patch.object(self.scan.Path, "home", return_value=work):
                home = self.scan.due(str(work))[0]
        self.assertEqual([d["project"] for d in inside], [self.repo])
        self.assertEqual(sorted(d["project"] for d in beside), sorted([self.repo, str(other)]))
        self.assertEqual(home, [])  # a session in the home folder starts no retro
        self.assertEqual(asked.count(str(other)), 1)  # git ran only where the folder covers the project

    def test_remote_main_never_prompts_and_has_a_short_timeout(self):
        completed = subprocess.CompletedProcess([], 0, stdout="abc\trefs/heads/main\n", stderr="")
        with mock.patch.object(self.scan.subprocess, "run", return_value=completed) as run:
            self.assertEqual(self.scan.remote_main(self.repo), ("abc", None))
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertLessEqual(run.call_args.kwargs["timeout"], 30)

    def test_due_reports_a_project_whose_remote_cannot_be_read(self):
        r = self.repo
        self.f.session(r, [user("a", NOW - 100, r)])
        self.f.session(r, [user("b", NOW - 50, r)])
        self.assertEqual(self.f.run("--due"), [])
        self.assertIn(f"skipped {r}", self.f.stderr)

    def test_github_says_when_the_list_limit_truncates(self):
        bin_dir = self.f.base / "bin"
        bin_dir.mkdir()
        (bin_dir / "prs.json").write_text(json.dumps([{"number": n, "reviews": [], "comments": []} for n in range(50)]))
        (bin_dir / "gh").write_text(f"#!/bin/sh\nif [ \"$1\" = pr ]; then cat {bin_dir}/prs.json; else echo '[]'; fi\n")
        (bin_dir / "gh").chmod(0o755)
        self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        m = self.f.run("--project", self.repo, "--since", iso(NOW - 3600), PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.assertEqual((m["github"]["prs_truncated"], m["github"]["ci_runs_truncated"]), (True, False))


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

    def record_retro(self, until):
        self.f.state.mkdir(parents=True, exist_ok=True)
        (self.f.state / "retro-metrics.jsonl").write_text(
            json.dumps({"project": self.repo, "sha": "older-sha", "until": iso(until)}) + "\n")

    def session_aged(self, t):
        path = self.f.session(self.repo, [user("go", NOW - 100, self.repo)])
        os.utime(path, (t, t))

    def test_sessions_from_before_the_last_retro_do_not_count(self):
        self.record_retro(NOW - 3600)
        self.session_aged(NOW - 7200)
        self.session_aged(NOW - 50)
        self.assertEqual(self.f.run("--due"), [])

    def test_a_session_after_an_old_retro_is_still_found(self):
        self.record_retro(NOW - 45 * DAY)
        self.session_aged(NOW - 40 * DAY)
        self.session_aged(NOW - 50)
        due = self.f.run("--due")
        self.assertEqual([(d["project"], d["sessions"]) for d in due], [(self.repo, 2)])


if __name__ == "__main__":
    unittest.main()

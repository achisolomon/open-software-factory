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

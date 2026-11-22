"""Tests for scripts/scratch-clean."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "scratch-clean"


def run(*args, factory_tmp=None, tmpdir=None):
    env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_TMP", "TMPDIR")}
    if factory_tmp:
        env["FACTORY_TMP"] = str(factory_tmp)
    if tmpdir:
        env["TMPDIR"] = str(tmpdir)
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


class ScratchCleanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()

    def test_removes_folder_and_file_inside_factory_tmp(self):
        folder = self.scratch / "dir"
        (folder / "sub").mkdir(parents=True)
        (folder / "sub" / "a.txt").write_text("x")
        loose = self.scratch / "b.txt"
        loose.write_text("y")
        result = run(folder, loose, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(folder.exists() or loose.exists())

    def test_tmpdir_also_counts_as_scratch(self):
        target = self.scratch / "t"
        target.mkdir()
        self.assertEqual(run(target, tmpdir=self.scratch).returncode, 0)
        self.assertFalse(target.exists())

    def test_refuses_path_outside_and_deletes_nothing(self):
        inside = self.scratch / "keep"
        inside.mkdir()
        result = run(inside, self.outside, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(inside.exists() and self.outside.exists())

    def test_refuses_the_root_itself(self):
        result = run(self.scratch, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.scratch.exists())

    def test_refuses_symlink_that_escapes(self):
        (self.outside / "f.txt").write_text("x")
        link = self.scratch / "link"
        link.symlink_to(self.outside)
        result = run(link, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.outside / "f.txt").exists())

    def test_refuses_dotdot_escape(self):
        result = run(self.scratch / ".." / "outside", factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.outside.exists())

    def test_refuses_when_no_root_is_set(self):
        target = self.scratch / "t"
        target.mkdir()
        result = run(target)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(target.exists())

    def test_missing_path_is_skipped_with_exit_zero(self):
        result = run(self.scratch / "nope", factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stdout)

    def test_removes_registered_worktree_and_prunes(self):
        repo = self.outside / "repo"
        repo.mkdir()
        git("-C", str(repo), "init", "-q")
        git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
        wt = self.scratch / "mut-T-1"
        git("-C", str(repo), "worktree", "add", "-q", str(wt), "HEAD")
        (wt / "dirty.txt").write_text("changed")
        result = run(wt, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(wt.exists())
        listing = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout
        self.assertNotIn("mut-T-1", listing)

    def test_worktree_of_names_the_repository(self):
        repo = self.outside / "repo"
        repo.mkdir()
        git("-C", str(repo), "init", "-q")
        git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
        wt = self.scratch / "mut-T-2"
        git("-C", str(repo), "worktree", "add", "-q", str(wt), "HEAD")
        result = run("--worktree-of", repo, wt, factory_tmp=self.scratch)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(wt.exists())


if __name__ == "__main__":
    unittest.main()

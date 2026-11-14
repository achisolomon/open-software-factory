"""#204 follow-ups: retro-check draft-diff, and overlay.state_dir refusing a state folder in a git work tree."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import overlay  # noqa: E402

CHECK = ROOT / "scripts" / "retro-check"
GIT = ["git", "-c", "user.name=Pat", "-c", "user.email=pat@example.invalid"]


class TempBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="retro-204-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))


class DraftDiffTests(TempBase):
    def setUp(self):
        super().setUp()
        self.repo = self.base / "plugin"
        self.repo.mkdir()
        (self.repo / "a.md").write_text("# Tests\n\nRun the tests.\n")
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
            subprocess.run([*GIT, "-C", str(self.repo), *args], check=True, capture_output=True)
        self.draft = self.base / "draft.md"
        self.draft.write_text("# Title\n\nThe change: a.md under Tests\n\nRun the unit tests first.\n")

    def diff(self):
        r = subprocess.run([sys.executable, str(CHECK), "draft-diff", str(self.draft), str(self.repo)],
                           capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def test_only_the_drafts_added_lines_pass(self):
        (self.repo / "a.md").write_text("# Tests\n\nRun the tests.\n\nRun the unit tests first.\n")
        self.assertEqual(self.diff(), (0, {"extra": []}))

    def test_an_added_line_that_is_not_in_the_draft_fails(self):
        (self.repo / "a.md").write_text("# Tests\n\nRun the tests.\nRun the unit tests first.\nSkip the review.\n")
        code, out = self.diff()
        self.assertEqual((code, out["extra"]), (1, ["+Skip the review."]))

    def test_a_deleted_or_replaced_line_fails(self):
        (self.repo / "a.md").write_text("# Tests\n\nRun the unit tests first.\n")
        code, out = self.diff()
        self.assertEqual((code, out["extra"]), (1, ["-Run the tests."]))

    def test_an_untracked_file_fails(self):
        (self.repo / "b.md").write_text("Run the unit tests first.\n")
        self.assertEqual(self.diff(), (1, {"extra": ["untracked: b.md"]}))

    def test_no_change_at_all_fails(self):
        self.assertEqual(self.diff(), (1, {"extra": ["no added lines"]}))

    def git(self, *args):
        subprocess.run([*GIT, "-C", str(self.repo), *args], check=True, capture_output=True)

    def test_a_removed_front_matter_dashes_line_is_a_deletion_not_a_header(self):
        (self.repo / "a.md").write_text("---\ntools: Read\n---\nRun the tests.\n")
        self.git("commit", "-qam", "front matter")
        (self.repo / "a.md").write_text("---\ntools: Read\nRun the tests.\n\nRun the unit tests first.\n")
        code, out = self.diff()
        self.assertEqual((code, out["extra"]), (1, ["----"]))

    def test_a_pure_rename_fails(self):
        self.git("mv", "a.md", "b.md")
        code, out = self.diff()
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("changed kind of file: R") for e in out["extra"]), out)

    def test_a_copy_fails(self):
        (self.repo / "b.md").write_text("# Tests\n\nRun the tests.\n")
        self.git("add", "-N", "b.md")
        code, out = self.diff()
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("changed kind of file: A") for e in out["extra"]), out)

    def test_a_mode_change_fails(self):
        os.chmod(self.repo / "a.md", 0o755)
        (self.repo / "a.md").write_text("# Tests\n\nRun the tests.\n\nRun the unit tests first.\n")
        code, out = self.diff()
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("changed kind of file: M") for e in out["extra"]), out)

    def test_a_binary_change_fails(self):
        (self.repo / "a.md").write_bytes(b"\x00\x01\x02 a.md")
        code, out = self.diff()
        self.assertEqual(code, 1)
        self.assertIn("binary: a.md", out["extra"])

    def test_a_change_to_a_file_the_draft_does_not_name_fails(self):
        (self.repo / "c.md").write_text("Run the tests.\n")
        self.git("add", "c.md")
        self.git("commit", "-qm", "c")
        (self.repo / "c.md").write_text("Run the tests.\nRun the unit tests first.\n")
        code, out = self.diff()
        self.assertEqual((code, out["extra"]), (1, ["file not named in the draft: c.md"]))

    def test_a_missing_repo_exits_2(self):
        r = subprocess.run([sys.executable, str(CHECK), "draft-diff", str(self.draft), str(self.base / "nope")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class StateDirTests(TempBase):
    def state_for(self, chosen):
        home = self.base / "home"
        home.mkdir(exist_ok=True)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(home), "SOFTWARE_FACTORY_HOME": str(chosen)}), \
                contextlib.redirect_stderr(err):
            return overlay.state_dir(), err.getvalue(), home / ".software-factory"

    def test_a_plain_folder_is_used(self):
        chosen = self.base / "state"
        got, err, _ = self.state_for(chosen)
        self.assertEqual((got, err), (chosen, ""))

    def test_a_folder_inside_a_git_work_tree_falls_back_with_a_notice(self):
        (self.base / "sibling" / ".git").mkdir(parents=True)
        got, err, default = self.state_for(self.base / "sibling" / "state")
        self.assertEqual(got, default)
        self.assertIn("ignoring SOFTWARE_FACTORY_HOME", err)

    def test_a_work_tree_root_with_a_git_file_falls_back(self):
        (self.base / "wt").mkdir()
        (self.base / "wt" / ".git").write_text("gitdir: /elsewhere\n")
        self.assertEqual(self.state_for(self.base / "wt")[0], self.state_for(self.base / "wt")[2])

    def test_home_and_root_fall_back(self):
        home = self.base / "home"
        for chosen in (home, "/"):
            with self.subTest(chosen=str(chosen)):
                home.mkdir(exist_ok=True)
                got, err, default = self.state_for(chosen)
                self.assertEqual(got, default)
                self.assertIn("using", err)


if __name__ == "__main__":
    unittest.main()

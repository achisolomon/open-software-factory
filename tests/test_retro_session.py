"""Tests for hooks/retro_session.py (SessionStart). Nothing here starts a real retro."""

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
sys.path.insert(0, str(ROOT / "hooks"))
import retro_session  # noqa: E402


class RetroSessionHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="retro-session-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.state = self.base / "state"
        self.state.mkdir()
        (self.state / "retro-auto").write_text("on\n")
        env = mock.patch.dict(os.environ, {"SOFTWARE_FACTORY_HOME": str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RETRO_HEADLESS", None)

    def start(self, cwd):
        """Run the hook for a session in cwd. Returns the commands it started."""
        with mock.patch.object(retro_session.subprocess, "Popen") as popen, \
                mock.patch.object(retro_session.sys, "stdin", io.StringIO(json.dumps({"cwd": cwd}))):
            self.assertEqual(retro_session.main(), 0)
        return [c.args[0] for c in popen.call_args_list]

    def test_the_first_session_of_the_day_in_a_folder_starts_its_retro_in_the_background(self):
        folder = str(self.base / "work" / "app")
        started = self.start(folder)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][1:], [str(ROOT / "scripts" / "retro-daily"), "--under", folder])
        self.assertEqual(self.start(folder), [])  # the second session that day starts nothing
        self.assertEqual(len(self.start(str(self.base / "work" / "other"))), 1)  # another folder has its own day

    def test_nothing_starts_when_the_daily_retro_is_off_or_inside_a_headless_retro(self):
        (self.state / "retro-auto").unlink()
        self.assertEqual(self.start(str(self.base)), [])
        (self.state / "retro-auto").write_text("on\n")
        with mock.patch.dict(os.environ, {"RETRO_HEADLESS": "1"}):
            self.assertEqual(self.start(str(self.base)), [])

    def test_stamps_from_earlier_days_are_removed(self):
        stamps = self.state / "retro-started"
        stamps.mkdir()
        (stamps / "2020-01-01-abc").write_text("")
        self.start(str(self.base))
        self.assertEqual(len(list(stamps.iterdir())), 1)
        self.assertFalse((stamps / "2020-01-01-abc").exists())

    def test_a_broken_input_never_blocks_the_session(self):
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "retro_session.py")], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, SOFTWARE_FACTORY_HOME=str(self.state)))
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()

"""hooks/retro_approve_guard.py: a subagent may not run the user-only retro-check commands."""

import json
from pathlib import Path
import unittest

from test_hooks import DEV, run

HOOKS_JSON = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "hooks.json"


def decide(command, agent_id="a1"):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "agent_type": DEV}
    if agent_id:
        payload["agent_id"] = agent_id
    result = run("retro_approve_guard.py", payload)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout.strip() else None


class RetroApproveGuardTests(unittest.TestCase):
    def test_a_subagent_is_denied_every_user_only_command(self):
        for name in ("approve", "drop", "retire", "keep", "repair"):
            with self.subTest(name=name):
                self.assertEqual(decide(f"python3 /p/scripts/retro-check {name} lesson-1"), "deny")

    def test_the_main_session_is_not_blocked(self):
        self.assertIsNone(decide("python3 /p/scripts/retro-check approve lesson-1", agent_id=None))

    def test_a_subagent_may_run_the_other_retro_check_commands(self):
        for name in ("check e.json", "scrub r.md --strict", "lessons", "draft-diff d.md /r"):
            with self.subTest(name=name):
                self.assertIsNone(decide(f"python3 /p/scripts/retro-check {name}"))

    def test_the_guard_is_wired_as_a_bash_hook(self):
        bash = [m for m in json.loads(HOOKS_JSON.read_text())["hooks"]["PreToolUse"] if m["matcher"] == "Bash"][0]
        self.assertTrue(any("retro_approve_guard.py" in h["command"] for h in bash["hooks"]))


if __name__ == "__main__":
    unittest.main()

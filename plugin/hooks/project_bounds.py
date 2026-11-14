"""PreToolUse hook (Write|Edit|NotebookEdit): a factory subagent may only write inside the project folder.

Scratch files belong in $FACTORY_TMP (<project>/.software-factory-tmp/<session id>), which scratch.py deletes when
the session ends. Writing next to the project, in its parent folder or in the user's home leaves files nobody
cleans up (contract rule 9.12).
"""

import json
import os
from pathlib import Path
import re
import sys

from factory_scope import project_root

try:
    import overlay
except Exception as err:  # a broken overlay module must not switch the bounds off: only the retro exception is lost
    overlay = None
    print(f"Software Factory: project bounds hook cannot load overlay.py ({err}); the retro folders stay denied.",
          file=sys.stderr)

RETRO_AGENT = "retro"
FACTORY_AGENTS = {path.stem for path in (Path(__file__).resolve().parents[1] / "agents").glob("*.md")}


# Claude Code's own per-session scratchpad (/private/tmp/claude-<uid>/<project slug>/<session id>/scratchpad).
# The harness tells subagents to use it, so blocking it only wasted turns (96+ in one week).
HARNESS_SCRATCHPAD = re.compile(r"^/private/tmp/claude-\d+/[^/]+/[0-9a-f-]{36}/scratchpad(/|$)")


def retro_state_folder(real):
    """True for <state>/retros/<day>/<slug>/work/** and <state>/upstream/** only (#198, #200); due.json, scans and
    overlay/ stay launcher-owned. real is already resolved, and so is the state folder, so `..` and symlinks cannot
    escape."""
    if overlay is None:
        return False
    state = Path(os.path.realpath(overlay.state_dir()))
    if state / "upstream" in real.parents:
        return True
    parts = real.relative_to(state).parts if state in real.parents else ()
    return len(parts) >= 5 and parts[0] == overlay.RUNS_DIR and parts[3] == overlay.WORK_DIR


def outside(path, root, cwd, agent=""):
    """agent is the factory agent's short name; only the Retro agent may use the retro state folders (the main
    session never reaches this check)."""
    real = Path(os.path.realpath(os.path.join(cwd, os.path.expanduser(path))))
    if HARNESS_SCRATCHPAD.match(str(real)) or (agent == RETRO_AGENT and retro_state_folder(real)):
        return False
    return real != root and root not in real.parents


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or not payload.get("agent_id"):
            return 0  # the main session (the user or the Team Lead) is not limited
        prefix, _, name = str(payload.get("agent_type") or "").rpartition(":")
        if prefix not in ("", "software-factory") or name not in FACTORY_AGENTS:
            return 0
        tool_input = payload.get("tool_input") or {}
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, str) or not path:
            return 0
        root = project_root(payload)
        if not outside(path, root, str(payload.get("cwd") or root), name):
            return 0
    except Exception:
        return 0  # never break the session over a checker fault
    reason = (f"Software Factory: `{path}` is outside the project folder {root}. Factory agents write only inside "
              f"the project. Put scratch files in $FACTORY_TMP ({root}/.software-factory-tmp/<session id>), which is "
              f"deleted when the session ends (contract rule 9.12).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

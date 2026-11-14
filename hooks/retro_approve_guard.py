"""PreToolUse hook (Bash): only the main session, where the user sees the prompt, may run the retro-check commands that
change the overlay on the user's say-so (approve, drop, retire, keep, repair). A subagent call (the hook input names an
agent) is denied, so an injected instruction cannot reach them through a delegated agent (#204 item 6).
"""

import json
import re
import sys

# Hygiene guard for the plain spelling, not a security boundary: the real control is the user's yes in the main session.
USER_ONLY =re.compile(r"\bretro-check['\"]?\s+(?:approve|drop|retire|keep|repair)\b")


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or not payload.get("agent_id"):
            return 0  # the main session: the user sees the prompt
        if not USER_ONLY.search(str((payload.get("tool_input") or {}).get("command") or "")):
            return 0
    except Exception:
        return 0  # never break the session over a checker fault
    reason = ("Software Factory: retro-check approve, drop, retire, keep and repair change the overlay on the user's "
              "yes. A subagent may not run them. Return the question to the main session.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

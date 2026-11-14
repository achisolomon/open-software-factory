"""SessionStart hook (startup, resume, clear): load the user's Retro Engine overlay into the session.

Refreshes the overlay after a plugin update (keep, retire or mark stale each entry), prints the active entries,
lists the entries that went live since the last session (with a one-step revert command), and tells the Team Lead about
stale entries, corrupt overlay lines, changes that wait for the user's yes and a failed scheduled retro.
Prints nothing when there is nothing to say. Never blocks the session.

Last-seen marker: <state>/overlay/last-session holds the ISO time of the previous run. An active entry whose
went_live_at is later than it is listed once. With no marker (first run) nothing is listed and the marker is written.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import overlay

MARKER = "last-session"


def marker_path():
    return overlay.state_dir() / "overlay" / MARKER


def parse_time(value):
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_marker():
    try:
        return parse_time(marker_path().read_text(encoding="utf-8").strip())
    except OSError:
        return None


def write_marker():
    """Atomic, so a crash never leaves a half-written timestamp."""
    path = marker_path()
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{MARKER}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def went_live_notice(rows, since):
    if since is None:
        return ""
    fresh = [e for e in rows if e.get("status") == "active" and e.get("went_live_by") in ("auto", "approved")
             and (parse_time(e.get("went_live_at")) or since) > since]
    if not fresh:
        return ""
    check = overlay.PLUGIN_ROOT / "scripts" / "retro-check"
    lines = ["Retro: these overlay entries went live since your last session. If you act as the Team Lead, show "
             "them to the user once, as plain text, with the revert command."]
    for e in fresh:
        one_line = " / ".join(part.strip() for part in str(e.get("text", "")).splitlines() if part.strip())
        lines.append(f"- [{e.get('role')}] {one_line} (went live: {e['went_live_by']}, overlay {e.get('id')}). "
                     f"Revert: {check} retire {e.get('id')}")
    return "\n".join(lines)


def corrupt_notice(path, rows, bad):
    """One line with the count and the backup name. Rewriting makes the backup, so the notice shows once."""
    if not bad:
        return ""
    overlay.write_jsonl(path, rows)
    backups = sorted(path.parent.glob(f"{path.name}.corrupt-*"))
    return (f"Overlay: {bad} corrupt line(s) in {path.name}, backup: {backups[-1].name if backups else 'none'}. "
            f"The good entries were kept.")


def main():
    try:
        sys.stdin.read()
        if not overlay.state_dir().is_dir():
            return 0
        path = overlay.overlay_file("entries")
        rows, bad = overlay.read_jsonl_counted(path)
        rows, changed = overlay.refresh(rows)
        if changed and not bad:
            overlay.write_jsonl(path, rows)
        parts = [corrupt_notice(path, rows, bad), overlay.render(rows), went_live_notice(rows, read_marker())]
        stale = sum(1 for e in rows if e.get("status") == "stale")
        if stale:
            parts.append(f"{stale} overlay entries are stale after a plugin update and are not applied. The next "
                         f"retro asks the user to keep, drop or rewrite them.")
        waiting = len(overlay.read_jsonl(overlay.overlay_file("pending")))
        drafts = len(list((overlay.state_dir() / "upstream").glob("*.md")))
        if waiting or drafts:
            parts.append(f"Retro: {waiting} change(s) wait for the user's yes and {drafts} upstream draft(s) are "
                         f"ready. If you act as the Team Lead, tell the user once and offer /software-factory:retro review.")
        failed = overlay.state_dir() / "retro-failed"
        if failed.is_file():
            parts.append(f"Retro failed: {failed.read_text(errors='ignore').strip()[:200]}. If you act as the Team "
                         f"Lead, tell the user once and offer /software-factory:retro to run it now.")
        context = "\n\n".join(p for p in parts if p)
    except Exception:  # never block the session start over the overlay
        return 0
    try:
        write_marker()
    except OSError:  # an unwritable marker only means the list shows again next time
        pass
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

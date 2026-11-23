"""SessionStart hook (startup): start the day's retro for the folder this session opened, in the background.

Runs only when the user turned the daily retro on (<state>/retro-auto, made by scripts/retro-schedule install), at most
once a day per folder, and never inside a headless retro run. The retro inherits this session's folder access, so macOS
needs no Full Disk Access grant, and retro-daily --under checks and runs only the projects this folder covers. Prints
nothing and never blocks the session.
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys


def stamp_name(folder, day):
    return f"{day}-{hashlib.sha256(folder.encode()).hexdigest()[:16]}"


def claim_today(stamps, folder, day):
    """True for the first session of the day in this folder. Older stamps are removed."""
    stamps.mkdir(parents=True, exist_ok=True)
    for old in stamps.iterdir():
        if not old.name.startswith(day):
            old.unlink(missing_ok=True)
    try:
        os.close(os.open(stamps / stamp_name(folder, day), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return False
    return True


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if os.environ.get("RETRO_HEADLESS"):  # the retro's own headless session must not start another retro
            return 0
        import overlay
        state = overlay.state_dir()
        if not (state / overlay.AUTO_FILE).is_file():
            return 0
        folder = os.path.realpath(data.get("cwd") or os.getcwd())
        if not claim_today(state / "retro-started", folder, datetime.date.today().isoformat()):
            return 0
        subprocess.Popen([sys.executable, str(overlay.PLUGIN_ROOT / "scripts" / "retro-daily"), "--under", folder],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=str(state), start_new_session=True)  # retro-daily writes its own log
    except Exception:  # never block the session start over the retro
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

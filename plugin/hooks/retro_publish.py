"""Retro Engine input checks for scripts/retro-check: the run folder layout, which files the agent may name and which
it may not. Run folders are <state>/retros/<day>/<slug>/ with the launcher's due.json and scan files, and a work/ folder
for the agent. Stdlib only.
"""

from datetime import datetime
import json
from pathlib import Path

import overlay


class CheckError(Exception):
    """Bad input or a refused action. retro-check prints it as {"error": ...} and exits 2."""


def load_json(path, kind):
    """Parsed JSON of the kind (list or dict) from a file, or CheckError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise CheckError(f"cannot read {path}: {err}") from err
    if not isinstance(data, kind):
        raise CheckError(f"{path} must hold a JSON {kind.__name__}")
    return data


def read_text_file(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as err:
        raise CheckError(f"cannot read {path}: {err}") from err


def retro_date(window):
    """The ISO date of window.until, or CheckError. It names the published file, so it must be a plain date."""
    until = window.get("until") if isinstance(window, dict) else None
    try:
        return datetime.fromisoformat(str(until).replace("Z", "+00:00")).date().isoformat()
    except ValueError as err:
        raise CheckError(f"window.until must be an ISO date, got {until!r}") from err


def checked_source(report):
    """The report path, when it is a real file inside <state>/retros/. A symlink is refused."""
    src = Path(report)
    if src.is_symlink():
        raise CheckError(f"{report} is a symlink")
    allowed = (overlay.state_dir() / overlay.RUNS_DIR).resolve()
    real = src.resolve()
    if not src.is_file() or real.parent.name != overlay.WORK_DIR or real.parent.parent.parent.parent != allowed:
        raise CheckError(f"the report must be a file in {allowed}/<day>/<slug>/{overlay.WORK_DIR}/")
    return src


def checked_metrics(metrics_path, run_dir=None):
    """The metrics, when the file is a scan file directly in a project's run folder <state>/retros/<day>/<slug>/ (the
    given one, if any), which the agent cannot write."""
    path = Path(metrics_path)
    folder = path.resolve().parent
    if (path.is_symlink() or folder.parent.parent != (overlay.state_dir() / overlay.RUNS_DIR).resolve()
            or run_dir not in (None, folder)):
        raise CheckError(f"--metrics must be a scan file directly in {run_dir or 'a run folder <state>/retros/<day>/<slug>'}")
    return load_json(path, dict)


def require_scan_of(metrics, project):
    """CheckError unless the scan describes this project, so one project's scan cannot stand in for another's."""
    if str(Path(str(metrics.get("project"))).resolve()) != project:
        raise CheckError(f"--metrics is not the scan of {project}")


def checked_project(run_dir, project):
    """CheckError unless project is in the run folder's due.json (a JSON list of project paths)."""
    due = load_json(Path(run_dir) / overlay.DUE_FILE, list)
    if project not in {str(Path(p).resolve()) for p in due if isinstance(p, str)}:
        raise CheckError(f"{project} is not in {overlay.DUE_FILE} for this run")


def checked_retros_dir(project):
    """<project>/docs/factory/retros, created if needed. The project needs docs/factory, and no part may be a symlink."""
    factory = Path(project) / "docs" / "factory"
    if not factory.is_dir():
        raise CheckError(f"{project} has no docs/factory folder")
    retros = factory / "retros"
    if any(p.is_symlink() for p in (factory.parent, factory, retros)):
        raise CheckError("docs, docs/factory and retros may not be symlinks")
    retros.mkdir(exist_ok=True)
    return retros


def raw_scan_texts(run_dir):
    """Every raw user message and PR comment sample held by the scan files (*.json) in the run folder."""
    texts = []
    for path in sorted(Path(run_dir).glob("*.json")):
        try:
            scan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(scan, dict):
            continue
        messages = scan.get("user_messages")
        for group in messages.values() if isinstance(messages, dict) else []:
            texts += [t for t in group if isinstance(t, str)] if isinstance(group, list) else []
        github = scan.get("github")  # retro-scan writes the PR comment samples here (#199); the top level is kept too
        for holder in (scan, github if isinstance(github, dict) else {}):
            samples = holder.get("review_samples")
            texts += [t for t in samples if isinstance(t, str)] if isinstance(samples, list) else []
    return texts

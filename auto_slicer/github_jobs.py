"""Slicing jobs for GitHub models, run one at a time from a queue.

A job slices one model with either a matrix selection or a repo rule.
Results land in <github archive>/<artifact name>/<model stem>/<prefix>_<timestamp>/,
where the prefix names the selection or rule (see matrix.py), so the
archive itself records which cells and rules already have a gcode.
"""

import asyncio
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .github_catalog import GithubError
from .matrix import (
    all_selections, cell_prefix, compose_overrides, folder_name,
    full_selection, rule_prefix, selection_error,
)
from .slicer import slice_file

JOB_TTL = 3600  # seconds a finished job stays listed
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
FOLDER_PATTERN = re.compile(r"^(.+)_(\d{8}_\d{6})$")


@dataclass
class SlicePlan:
    source: Path  # the model in the artifact cache
    overrides: dict[str, str]
    model_dir: Path  # <github archive>/<artifact name>/<model stem>
    prefix: str  # archive folder name without its timestamp


@dataclass
class JobQueue:
    jobs: dict[str, dict] = field(default_factory=dict)
    pending: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: asyncio.Task | None = None
    # Async callables awaited with a copy of each job on every state change
    listeners: list = field(default_factory=list)


# --- Requests ---


def parse_choice(params: dict, project_rules: dict | None) -> dict:
    """Validate a slice request into {"selection": {...}} or {"rule": name}."""
    if ("selection" in params) == ("rule" in params):
        raise GithubError(400, "Give either selection or rule")
    if "rule" in params:
        return {"rule": _valid_rule(params["rule"], project_rules)}
    return {"selection": _valid_selection(params["selection"], project_rules)}


def _valid_rule(rule, project_rules: dict | None) -> str:
    """A rule name the project defines."""
    if not isinstance(rule, str) or rule not in (project_rules or {}).get("rules", {}):
        raise GithubError(400, f"Unknown rule: {rule}")
    return rule


def _valid_selection(selection, project_rules: dict | None) -> dict[str, str]:
    """A complete matrix selection, refused when the project turned its matrix off."""
    if project_rules is not None and not project_rules["matrix"]:
        raise GithubError(400, "This project slices with its rules only")
    if not isinstance(selection, dict):
        raise GithubError(400, "selection must be an object")
    error = selection_error(selection)
    if error:
        raise GithubError(400, error)
    return full_selection(selection)


def choice_overrides(choice: dict, project_rules: dict | None) -> dict[str, str]:
    """The settings overrides of a selection or rule."""
    if "rule" in choice:
        return dict(project_rules["rules"][choice["rule"]]["settings"])
    return compose_overrides(choice["selection"])


def choice_prefix(choice: dict) -> str:
    """The archive folder prefix of a selection or rule."""
    if "rule" in choice:
        return rule_prefix(choice["rule"])
    return cell_prefix(choice["selection"])


def model_dir(archive_dir: Path, artifact_name: str, model: str) -> Path:
    """Archive directory of one model of one artifact."""
    return archive_dir / artifact_name / Path(model).stem


def printer_path(gcodes_prefix: str, relative: str) -> str:
    """A result path as the printer's gcodes root sees it."""
    return f"{gcodes_prefix}/{relative}" if gcodes_prefix else relative


# --- Jobs ---


def new_job(job_id: str, artifact_id: int, model: str, choice: dict, now: float) -> dict:
    """A queued job. Exactly one of selection and rule is set."""
    return {
        "id": job_id, "artifact_id": artifact_id, "model": model,
        "selection": None, "rule": None, **choice,
        "state": "queued", "message": "Queued", "path": None, "printer_path": None,
        "created_at": now, "finished_at": None,
    }


def finished(job: dict, success: bool, message: str, relative: str | None, gcodes_prefix: str, now: float) -> dict:
    """A job after its slice: done with the gcode's paths, or error."""
    if not success or relative is None:
        return {**job, "state": "error", "message": message, "finished_at": now}
    path = printer_path(gcodes_prefix, relative)
    return {**job, "state": "done", "message": message, "path": relative, "printer_path": path, "finished_at": now}


def prune_jobs(jobs: dict[str, dict], now: float) -> dict[str, dict]:
    """Drop jobs that finished more than JOB_TTL ago."""
    return {
        job_id: job for job_id, job in jobs.items()
        if job["finished_at"] is None or now - job["finished_at"] < JOB_TTL
    }


def get_job(queue: JobQueue, job_id: str) -> dict:
    """One job by id."""
    queue.jobs = prune_jobs(queue.jobs, time.time())
    if job_id not in queue.jobs:
        raise GithubError(404, f"Unknown job: {job_id}")
    return queue.jobs[job_id]


def list_jobs(queue: JobQueue) -> list[dict]:
    """All jobs still kept, oldest first."""
    queue.jobs = prune_jobs(queue.jobs, time.time())
    return list(queue.jobs.values())


async def submit(queue: JobQueue, config: Config, artifact_id: int, model: str, choice: dict, plan: SlicePlan) -> dict:
    """Queue a slice and return its job."""
    job = new_job(secrets.token_hex(6), artifact_id, model, choice, time.time())
    queue.jobs = prune_jobs(queue.jobs, time.time())
    await _update(queue, job)
    queue.pending.put_nowait((job["id"], plan))
    if queue.worker is None or queue.worker.done():
        queue.worker = asyncio.create_task(_work(queue, config))
    return job


async def _work(queue: JobQueue, config: Config) -> None:
    """Run queued jobs one after another, forever."""
    while True:
        job_id, plan = await queue.pending.get()
        await _run(queue, config, job_id, plan)
        queue.pending.task_done()


async def _run(queue: JobQueue, config: Config, job_id: str, plan: SlicePlan) -> None:
    """Slice one job in a thread and record its outcome."""
    await _update(queue, {**queue.jobs[job_id], "state": "slicing", "message": "Slicing"})
    try:
        success, message, relative = await asyncio.to_thread(run_slice, config, plan, time.strftime(TIMESTAMP_FORMAT))
    except Exception as error:
        success, message, relative = False, f"System error: {error}", None
    job = finished(queue.jobs[job_id], success, message, relative, config.moonraker_gcodes_prefix, time.time())
    await _update(queue, job)


async def _update(queue: JobQueue, job: dict) -> None:
    """Store a job and tell every listener."""
    queue.jobs[job["id"]] = job
    for listener in queue.listeners:
        try:
            await listener(dict(job))
        except Exception as error:
            print(f"[Jobs] Listener failed: {error}", flush=True)


def run_slice(config: Config, plan: SlicePlan, timestamp: str) -> tuple[bool, str, str | None]:
    """Slice a copy of the cached model (slice_file moves its input) into the archive.

    Returns (success, message, gcode path relative to the github archive).
    slice_file also moves the copy into the model directory, beside the result folders.
    """
    folder = plan.model_dir / folder_name(plan.prefix, timestamp)
    with tempfile.TemporaryDirectory(prefix="slicer_github_") as tmp:
        model = Path(tmp) / plan.source.name
        shutil.copy2(plan.source, model)
        success, message, _, _ = slice_file(config, model, plan.overrides, archive_folder=folder)
    gcode = find_gcode(folder) if success else None
    relative = gcode.relative_to(config.github_archive_dir).as_posix() if gcode else None
    return success, message, relative


# --- Results already in the archive ---


def find_gcode(folder: Path) -> Path | None:
    """The gcode of a result folder, if any."""
    return next(iter(sorted(folder.glob("*.gcode"))), None)


def split_folder_name(name: str) -> tuple[str, str] | None:
    """Split <prefix>_<YYYYmmdd_HHMMSS> into (prefix, timestamp)."""
    match = FOLDER_PATTERN.match(name)
    return (match.group(1), match.group(2)) if match else None


def latest_folders(names) -> dict[str, str]:
    """The newest folder name per prefix (timestamps sort chronologically)."""
    latest = {}
    for name in sorted(names):
        parts = split_folder_name(name)
        if parts:
            latest[parts[0]] = name
    return latest


def result_choices(project_rules: dict | None) -> dict[str, dict]:
    """Folder prefix to the choice that produces it, for every matrix cell and rule."""
    choices = {cell_prefix(selection): {"selection": selection} for selection in all_selections()}
    rules = (project_rules or {}).get("rules", {})
    choices.update({rule_prefix(name): {"rule": name} for name in rules})
    return choices


def gcode_folders(directory: Path) -> dict[str, Path]:
    """Result folders of a model directory that hold a gcode: folder name to gcode."""
    if not directory.is_dir():
        return {}
    found = {folder.name: find_gcode(folder) for folder in directory.iterdir() if folder.is_dir()}
    return {name: gcode for name, gcode in found.items() if gcode}


def _result_entry(choice: dict, relative: str, gcodes_prefix: str) -> dict:
    """One sliced result: its choice, shaped like a job's, and its gcode paths."""
    path = printer_path(gcodes_prefix, relative)
    return {"selection": None, "rule": None, **choice, "path": relative, "printer_path": path}


def sliced(directory: Path, archive_dir: Path, project_rules: dict | None, gcodes_prefix: str) -> list[dict]:
    """The newest result of each cell and rule already sliced for one model."""
    folders = gcode_folders(directory)
    choices = result_choices(project_rules)
    return [
        _result_entry(choices[prefix], folders[name].relative_to(archive_dir).as_posix(), gcodes_prefix)
        for prefix, name in latest_folders(folders).items()
        if prefix in choices
    ]

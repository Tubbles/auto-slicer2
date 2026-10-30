"""Catalog of STL artifacts from GitHub Actions runs.

Each configured repo's latest successful run on its default branch
contributes one project per non-expired artifact. Artifact zips are
downloaded and extracted into the cache directory once, keyed by
artifact id: <cache>/<artifact id>/{artifact.zip, models/, thumbnails/}.
Repo rules come from .autoslice.json at the run's commit.
"""

import asyncio
import base64
import json
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import aiohttp

from .file_utils import find_models_in_zip
from .thumbnails import render_stl_thumbnail

API_ROOT = "https://api.github.com"
JSON_MEDIA = "application/vnd.github+json"
RAW_MEDIA = "application/vnd.github.raw+json"
API_VERSION = "2022-11-28"
RULES_FILE = ".autoslice.json"
PROJECTS_TTL = 60  # seconds a projects listing is reused
THUMBNAIL_SIZE = 300
RULES_KEYS = {"matrix", "rules"}
RULE_KEYS = {"description", "settings"}


class GithubError(Exception):
    """A request the GitHub slicing feature cannot serve, with the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Artifact:
    project: dict  # the catalog entry handed to clients
    head_sha: str  # full commit of the run, for the rules file


@dataclass
class CatalogState:
    session: aiohttp.ClientSession | None
    token: str
    repos: list[str]
    cache_dir: Path
    projects: list[dict] | None = None
    fetched_at: float = 0.0
    artifacts: dict[int, Artifact] = field(default_factory=dict)
    rules: dict[tuple[str, str], dict | None] = field(default_factory=dict)
    locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    render_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# --- Pure parsing ---


def github_headers(token: str, accept: str = JSON_MEDIA) -> dict[str, str]:
    """Request headers for the GitHub REST API."""
    return {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": API_VERSION}


def latest_run(runs_json: dict) -> dict | None:
    """The first run of a runs listing (the API sorts newest first), or None."""
    runs = runs_json.get("workflow_runs", [])
    return runs[0] if runs else None


def parse_artifacts(artifacts_json: dict, repo: str, head_sha: str) -> list[Artifact]:
    """One Artifact per non-expired artifact of a run."""
    return [
        Artifact(_project(repo, artifact, head_sha), head_sha)
        for artifact in artifacts_json.get("artifacts", [])
        if not artifact.get("expired")
    ]


def _project(repo: str, artifact: dict, head_sha: str) -> dict:
    """The catalog entry of one artifact."""
    return {
        "repo": repo,
        "artifact_id": artifact["id"],
        "name": artifact["name"],
        "commit": head_sha[:7],
        "created_at": artifact["created_at"],
        "size": artifact["size_in_bytes"],
    }


def is_fresh(fetched_at: float, now: float) -> bool:
    """Whether a projects listing fetched at fetched_at may still be reused."""
    return now - fetched_at < PROJECTS_TTL


def parse_rules(text: str) -> dict:
    """Parse and validate .autoslice.json into {"matrix": bool, "rules": {...}}. ValueError names the problem."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON: {error}") from error
    _check_top_level(data)
    rules = data.get("rules", {})
    return {"matrix": data.get("matrix", True), "rules": {name: _parse_rule(name, rule) for name, rule in rules.items()}}


def _check_top_level(data) -> None:
    """Validate the top-level object of .autoslice.json."""
    if not isinstance(data, dict):
        raise ValueError("the top level must be an object")
    unknown = set(data) - RULES_KEYS
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(sorted(unknown))}")
    if not isinstance(data.get("matrix", True), bool):
        raise ValueError("'matrix' must be true or false")
    if not isinstance(data.get("rules", {}), dict):
        raise ValueError("'rules' must be an object")


def _parse_rule(name: str, rule) -> dict:
    """Validate one rule, the shape of a preset: {"description": str, "settings": {key: str}}."""
    if not isinstance(rule, dict) or set(rule) - RULE_KEYS:
        raise ValueError(f"rule '{name}' must be an object with only description and settings")
    description = rule.get("description", "")
    settings = rule.get("settings")
    if not isinstance(description, str):
        raise ValueError(f"rule '{name}': description must be a string")
    if not isinstance(settings, dict) or not all(isinstance(v, str) for v in settings.values()):
        raise ValueError(f"rule '{name}': settings must be an object of string values")
    return {"description": description, "settings": dict(settings)}


def model_entries(models_dir: Path, paths: list[Path]) -> list[dict]:
    """The models listing: name relative to the extracted artifact, and size in bytes."""
    return [
        {"name": path.relative_to(models_dir).as_posix(), "size": path.stat().st_size}
        for path in sorted(paths)
    ]


# --- GitHub I/O ---


async def _request(state: CatalogState, path: str, accept: str = JSON_MEDIA) -> tuple[int, bytes]:
    """GET an API path and return (status, body). Redirects are followed (artifact zips)."""
    try:
        async with state.session.get(API_ROOT + path, headers=github_headers(state.token, accept)) as response:
            return response.status, await response.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise GithubError(502, f"GitHub request failed: {str(error) or type(error).__name__}") from error


def _failure(status: int, path: str, body: bytes) -> GithubError:
    """A GithubError for an unexpected GitHub answer, with GitHub's message when it sent one."""
    try:
        message = json.loads(body).get("message", "")
    except (ValueError, AttributeError):
        message = ""
    detail = f": {message}" if message else ""
    return GithubError(502, f"GitHub answered {status} for {path}{detail}")


async def _get_json(state: CatalogState, path: str) -> dict:
    """GET an API path that must answer 200 with JSON."""
    status, body = await _request(state, path)
    if status != 200:
        raise _failure(status, path, body)
    return json.loads(body)


async def _repo_artifacts(state: CatalogState, repo: str) -> list[Artifact]:
    """The artifacts of a repo's latest successful run on its default branch."""
    branch = (await _get_json(state, f"/repos/{repo}"))["default_branch"]
    runs = await _get_json(state, f"/repos/{repo}/actions/runs?branch={quote(branch)}&status=success&per_page=1")
    run = latest_run(runs)
    if run is None:
        return []
    listing = await _get_json(state, f"/repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100")
    return parse_artifacts(listing, repo, run["head_sha"])


async def projects(state: CatalogState) -> list[dict]:
    """All projects of all configured repos, reused for PROJECTS_TTL seconds."""
    if state.projects is not None and is_fresh(state.fetched_at, time.time()):
        return state.projects
    found = [artifact for repo in state.repos for artifact in await _repo_artifacts(state, repo)]
    state.artifacts.update({artifact.project["artifact_id"]: artifact for artifact in found})
    state.projects = sorted((artifact.project for artifact in found), key=lambda p: (p["repo"], p["name"]))
    state.fetched_at = time.time()
    return state.projects


async def find_artifact(state: CatalogState, artifact_id: int) -> Artifact:
    """An artifact seen in a listing. Lists the projects once more when it is unknown."""
    if artifact_id not in state.artifacts:
        await projects(state)
    if artifact_id not in state.artifacts:
        raise GithubError(404, f"Unknown artifact: {artifact_id}")
    return state.artifacts[artifact_id]


async def fetch_rules(state: CatalogState, repo: str, head_sha: str) -> dict | None:
    """A repo's parsed rules file at a commit, or None when it has none or it is invalid (logged)."""
    path = f"/repos/{repo}/contents/{RULES_FILE}?ref={head_sha}"
    status, body = await _request(state, path, accept=RAW_MEDIA)
    if status == 404:
        return None
    if status != 200:
        raise _failure(status, path, body)
    try:
        return parse_rules(body.decode("utf-8"))
    except ValueError as error:
        print(f"[GitHub] {repo}@{head_sha[:7]}: {RULES_FILE} ignored: {error}", flush=True)
        return None


async def project_rules(state: CatalogState, artifact_id: int) -> dict | None:
    """The rules of an artifact's commit, fetched once per commit."""
    artifact = await find_artifact(state, artifact_id)
    key = (artifact.project["repo"], artifact.head_sha)
    if key not in state.rules:
        state.rules[key] = await fetch_rules(state, *key)
    return state.rules[key]


# --- Artifact cache ---


def artifact_dir(state: CatalogState, artifact_id: int) -> Path:
    """Cache directory of one artifact."""
    return state.cache_dir / str(artifact_id)


def store_artifact(data: bytes, directory: Path) -> None:
    """Save an artifact zip and extract it. models/ only appears once extraction is complete."""
    directory.mkdir(parents=True, exist_ok=True)
    zip_path = directory / "artifact.zip"
    zip_path.write_bytes(data)
    staging = Path(tempfile.mkdtemp(prefix="extract_", dir=directory))
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(staging)
    staging.rename(directory / "models")


async def ensure_models(state: CatalogState, artifact_id: int) -> Path:
    """Download and extract an artifact once and return its models directory."""
    artifact = await find_artifact(state, artifact_id)
    models_dir = artifact_dir(state, artifact_id) / "models"
    async with state.locks.setdefault(artifact_id, asyncio.Lock()):
        if not models_dir.is_dir():
            data = await _download(state, artifact)
            await asyncio.to_thread(store_artifact, data, artifact_dir(state, artifact_id))
    return models_dir


async def _download(state: CatalogState, artifact: Artifact) -> bytes:
    """The zip bytes of an artifact. GitHub redirects to blob storage and aiohttp drops the token there."""
    path = f"/repos/{artifact.project['repo']}/actions/artifacts/{artifact.project['artifact_id']}/zip"
    print(f"[GitHub] Downloading {artifact.project['name']}", flush=True)
    status, body = await _request(state, path)
    if status != 200:
        raise _failure(status, path, body)
    return body


async def models(state: CatalogState, artifact_id: int) -> list[dict]:
    """The model files of an artifact."""
    models_dir = await ensure_models(state, artifact_id)
    return model_entries(models_dir, find_models_in_zip(models_dir))


async def model_path(state: CatalogState, artifact_id: int, name: str) -> Path:
    """The cached file of one model, 404 for a name the artifact does not hold."""
    models_dir = await ensure_models(state, artifact_id)
    for path in find_models_in_zip(models_dir):
        if path.relative_to(models_dir).as_posix() == name:
            return path
    raise GithubError(404, f"Unknown model: {name}")


async def thumbnail(state: CatalogState, artifact_id: int, name: str) -> str:
    """Base64 PNG of a model, rendered once with OpenSCAD (one render at a time)."""
    stl_path = await model_path(state, artifact_id, name)
    png_path = artifact_dir(state, artifact_id) / "thumbnails" / f"{name}.png"
    async with state.render_lock:
        if not png_path.exists():
            await asyncio.to_thread(_render, stl_path, png_path)
    return base64.b64encode(png_path.read_bytes()).decode("ascii")


def _render(stl_path: Path, png_path: Path) -> None:
    """Render a thumbnail into place, or raise when OpenSCAD fails."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    partial = png_path.with_suffix(".partial.png")
    # OpenSCAD resolves a relative import() against its input file, /dev/null
    if not render_stl_thumbnail(stl_path.resolve(), partial, THUMBNAIL_SIZE, THUMBNAIL_SIZE):
        partial.unlink(missing_ok=True)
        raise GithubError(500, f"Thumbnail rendering failed for {stl_path.name}")
    shutil.move(str(partial), png_path)

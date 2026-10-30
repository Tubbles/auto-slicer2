"""The GitHub slicing methods, shared by the HTTP API and the Moonraker agent.

Each method takes the context and a params dict (JSON-RPC params, or the
HTTP path and body fields) and returns JSON-ready data, never None
(Moonraker reads a null result from an agent as an error). Failures raise
GithubError with the HTTP status to answer with.
"""

import asyncio
from dataclasses import dataclass, field

import aiohttp

from . import github_catalog as catalog
from .config import Config
from .github_catalog import CatalogState, GithubError
from .github_jobs import (
    JobQueue, SlicePlan, choice_overrides, choice_prefix, get_job,
    list_jobs, model_dir, parse_choice, sliced, submit,
)
from .matrix import options

# sock_read bounds a stalled transfer without capping a long artifact download
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)


@dataclass
class GithubContext:
    config: Config
    catalog: CatalogState
    jobs: JobQueue = field(default_factory=JobQueue)


def create_context(config: Config) -> GithubContext:
    """Build the context. Call it inside the running event loop, as it opens an HTTP session."""
    session = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
    state = CatalogState(session, config.github_token, config.github_repos, config.github_cache_dir)
    return GithubContext(config, state)


async def close_context(context: GithubContext) -> None:
    """Stop the job worker and close the HTTP session."""
    if context.jobs.worker is not None:
        context.jobs.worker.cancel()
        await asyncio.gather(context.jobs.worker, return_exceptions=True)
    await context.catalog.session.close()


def artifact_id_param(params: dict) -> int:
    """The artifact_id parameter as an integer."""
    try:
        return int(params["artifact_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise GithubError(400, "artifact_id must be an integer") from error


def text_param(params: dict, key: str) -> str:
    """A required non-empty string parameter."""
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise GithubError(400, f"{key} must be a non-empty string")
    return value


async def _projects(context: GithubContext, params: dict) -> list[dict]:
    return await catalog.projects(context.catalog)


async def _models(context: GithubContext, params: dict) -> list[dict]:
    return await catalog.models(context.catalog, artifact_id_param(params))


async def _thumbnail(context: GithubContext, params: dict) -> dict:
    png = await catalog.thumbnail(context.catalog, artifact_id_param(params), text_param(params, "model"))
    return {"png": png}


async def _options(context: GithubContext, params: dict) -> dict:
    return options(await catalog.project_rules(context.catalog, artifact_id_param(params)))


async def _sliced(context: GithubContext, params: dict) -> list[dict]:
    artifact_id = artifact_id_param(params)
    artifact = await catalog.find_artifact(context.catalog, artifact_id)
    rules = await catalog.project_rules(context.catalog, artifact_id)
    archive = context.config.github_archive_dir
    directory = model_dir(archive, artifact.project["name"], text_param(params, "model"))
    return sliced(directory, archive, rules, context.config.moonraker_gcodes_prefix)


async def _slice(context: GithubContext, params: dict) -> dict:
    artifact_id, model = artifact_id_param(params), text_param(params, "model")
    rules = await catalog.project_rules(context.catalog, artifact_id)
    choice = parse_choice(params, rules)
    source = await catalog.model_path(context.catalog, artifact_id, model)
    artifact = await catalog.find_artifact(context.catalog, artifact_id)
    directory = model_dir(context.config.github_archive_dir, artifact.project["name"], model)
    plan = SlicePlan(source, choice_overrides(choice, rules), directory, choice_prefix(choice))
    return await submit(context.jobs, context.config, artifact_id, model, choice, plan)


async def _jobs(context: GithubContext, params: dict) -> dict | list[dict]:
    """One job when params name an id, otherwise every job kept."""
    if "id" in params:
        return get_job(context.jobs, text_param(params, "id"))
    return list_jobs(context.jobs)


METHODS = {
    "projects": _projects,
    "models": _models,
    "thumbnail": _thumbnail,
    "options": _options,
    "sliced": _sliced,
    "slice": _slice,
    "jobs": _jobs,
}


async def call(context: GithubContext, method: str, params: dict):
    """Run one method by name."""
    if method not in METHODS:
        raise GithubError(404, f"Unknown method: {method}")
    return await METHODS[method](context, params)

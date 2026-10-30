"""Tests for the /api/github endpoints."""

import base64
import time
from unittest.mock import patch

import pytest

from auto_slicer import github_jobs
from auto_slicer.web_api import TOKEN_TTL, create_web_app, generate_token

from .github_helpers import ARTIFACT_ID, ARTIFACT_NAME, MODEL, fake_slice_file, make_config, make_context

RULES = {"matrix": True, "rules": {"Thin lid": {"description": "Lid", "settings": {"layer_height": "0.1"}}}}


def _app(context, config):
    """A web app with one valid token, and the headers that carry it."""
    app = create_web_app(config, {}, github=context)
    token = generate_token()
    app["tokens"][token] = (42, time.time() + TOKEN_TTL, time.time())
    return app, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def github_app(tmp_path):
    context = make_context(tmp_path, rules=RULES)
    app, headers = _app(context, context.config)
    return app, headers, context


class TestFeatureOff:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/github/projects", f"/api/github/projects/{ARTIFACT_ID}/options", "/api/github/jobs/x"])
    async def test_not_found(self, tmp_path, aiohttp_client, path):
        app, headers = _app(None, make_config(tmp_path))
        client = await aiohttp_client(app)
        resp = await client.get(path, headers=headers)
        assert resp.status == 404
        assert "not configured" in (await resp.json())["error"]


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_requires_auth(self, github_app, aiohttp_client):
        app, _, _ = github_app
        client = await aiohttp_client(app)
        assert (await client.get("/api/github/projects")).status == 401

    @pytest.mark.asyncio
    async def test_projects(self, github_app, aiohttp_client):
        app, headers, context = github_app
        client = await aiohttp_client(app)
        resp = await client.get("/api/github/projects", headers=headers)
        assert resp.status == 200
        assert await resp.json() == context.catalog.projects

    @pytest.mark.asyncio
    async def test_models(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        resp = await client.get(f"/api/github/projects/{ARTIFACT_ID}/models", headers=headers)
        assert await resp.json() == [{"name": MODEL, "size": 27}]

    @pytest.mark.asyncio
    async def test_bad_artifact_id(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        resp = await client.get("/api/github/projects/abc/models", headers=headers)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_thumbnail_from_cache(self, github_app, aiohttp_client):
        app, headers, context = github_app
        png = context.config.github_cache_dir / str(ARTIFACT_ID) / "thumbnails" / f"{MODEL}.png"
        png.parent.mkdir(parents=True)
        png.write_bytes(b"PNG")
        client = await aiohttp_client(app)
        resp = await client.get(f"/api/github/projects/{ARTIFACT_ID}/models/{MODEL}/thumbnail", headers=headers)
        assert await resp.json() == {"png": base64.b64encode(b"PNG").decode()}

    @pytest.mark.asyncio
    async def test_options(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        body = await (await client.get(f"/api/github/projects/{ARTIFACT_ID}/options", headers=headers)).json()
        assert body["rules"] == RULES["rules"]
        assert body["matrix"] is True
        assert len(body["dimensions"]) == 4

    @pytest.mark.asyncio
    async def test_sliced_empty(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        resp = await client.get(f"/api/github/projects/{ARTIFACT_ID}/models/{MODEL}/sliced", headers=headers)
        assert await resp.json() == []

    @pytest.mark.asyncio
    async def test_slice_and_poll(self, github_app, aiohttp_client):
        app, headers, context = github_app
        client = await aiohttp_client(app)
        with patch.object(github_jobs, "slice_file", fake_slice_file([])):
            resp = await client.post("/api/github/slice", headers=headers,
                                     json={"artifact_id": ARTIFACT_ID, "model": MODEL, "rule": "Thin lid"})
            job = await resp.json()
            assert job["state"] == "queued"
            await context.jobs.pending.join()
        context.jobs.worker.cancel()
        done = await (await client.get(f"/api/github/jobs/{job['id']}", headers=headers)).json()
        assert done["state"] == "done"
        assert done["path"].startswith(f"{ARTIFACT_NAME}/kbdkid5-plate-left/Thin-lid_")
        sliced = await (await client.get(f"/api/github/projects/{ARTIFACT_ID}/models/{MODEL}/sliced", headers=headers)).json()
        assert sliced == [{"selection": None, "rule": "Thin lid", "path": done["path"], "printer_path": done["printer_path"]}]

    @pytest.mark.asyncio
    async def test_slice_bad_request(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        resp = await client.post("/api/github/slice", headers=headers, json={"artifact_id": ARTIFACT_ID, "model": MODEL})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_slice_invalid_json(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        resp = await client.post("/api/github/slice", headers=headers, data="nope")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_job(self, github_app, aiohttp_client):
        app, headers, _ = github_app
        client = await aiohttp_client(app)
        assert (await client.get("/api/github/jobs/nope", headers=headers)).status == 404

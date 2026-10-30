"""Tests for the GitHub artifact catalog: parsing real API responses, rules, and the artifact cache."""

import base64
import io
import json
import zipfile
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web

from auto_slicer import github_catalog
from auto_slicer.github_catalog import (
    CatalogState, GithubError, _failure, _request, find_artifact, fetch_rules, github_headers, is_fresh,
    latest_run, model_entries, models, parse_artifacts, parse_rules, project_rules,
    projects, store_artifact, thumbnail,
)
from auto_slicer.file_utils import find_models_in_zip

from .github_helpers import ARTIFACT_ID, HEAD_SHA, MODEL, REPO, load_fixture, make_context


def _zip_bytes(names: list[str]) -> bytes:
    """A zip holding one small file per name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"solid x\nendsolid x\n")
    return buffer.getvalue()


def _fake_get_json(responses: dict):
    """A _get_json stand-in answering paths from a dict and recording them."""
    seen = []

    async def get_json(state, path):
        seen.append(path)
        return responses[path]
    return get_json, seen


RUN_ID = 41014945273
API_RESPONSES = {
    f"/repos/{REPO}": load_fixture("repo.json"),
    f"/repos/{REPO}/actions/runs?branch=main&status=success&per_page=1": load_fixture("runs.json"),
    f"/repos/{REPO}/actions/runs/{RUN_ID}/artifacts?per_page=100": load_fixture("artifacts.json"),
}


class TestParsing:
    def test_headers(self):
        headers = github_headers("abc")
        assert headers["Authorization"] == "Bearer abc"
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"

    def test_latest_run(self):
        run = latest_run(load_fixture("runs.json"))
        assert run["id"] == RUN_ID
        assert run["head_sha"] == HEAD_SHA

    def test_latest_run_none(self):
        assert latest_run({"total_count": 0, "workflow_runs": []}) is None

    def test_parse_artifacts(self):
        artifacts = parse_artifacts(load_fixture("artifacts.json"), REPO, HEAD_SHA)
        assert len(artifacts) == 7
        kbdkid5 = next(a for a in artifacts if a.project["artifact_id"] == ARTIFACT_ID)
        assert kbdkid5.project == {
            "repo": REPO, "artifact_id": ARTIFACT_ID, "name": "kbdkid5-7300786",
            "commit": "7300786", "created_at": "2026-10-30T06:40:13Z", "size": 1617991,
        }
        assert kbdkid5.head_sha == HEAD_SHA

    def test_parse_artifacts_skips_expired(self):
        listing = load_fixture("artifacts.json")
        listing["artifacts"][0]["expired"] = True
        assert len(parse_artifacts(listing, REPO, HEAD_SHA)) == 6

    def test_is_fresh(self):
        assert is_fresh(100.0, 159.0)
        assert not is_fresh(100.0, 160.0)

    def test_model_entries(self, tmp_path):
        (tmp_path / "b.stl").write_bytes(b"12345")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.stl").write_bytes(b"1")
        entries = model_entries(tmp_path, find_models_in_zip(tmp_path))
        assert entries == [{"name": "b.stl", "size": 5}, {"name": "sub/a.stl", "size": 1}]


class TestParseRules:
    def test_full(self):
        text = json.dumps({"matrix": False, "rules": {"lid": {"description": "Thin lid", "settings": {"layer_height": "0.1"}}}})
        assert parse_rules(text) == {
            "matrix": False,
            "rules": {"lid": {"description": "Thin lid", "settings": {"layer_height": "0.1"}}},
        }

    def test_defaults(self):
        assert parse_rules("{}") == {"matrix": True, "rules": {}}

    def test_description_optional(self):
        rules = parse_rules('{"rules": {"x": {"settings": {}}}}')
        assert rules["rules"]["x"] == {"description": "", "settings": {}}

    @pytest.mark.parametrize("text, fragment", [
        ("not json", "not valid JSON"),
        ("[]", "top level"),
        ('{"rule": {}}', "unknown keys"),
        ('{"matrix": "yes"}', "matrix"),
        ('{"rules": []}', "rules"),
        ('{"rules": {"x": "fast"}}', "rule 'x'"),
        ('{"rules": {"x": {"settings": {}, "extra": 1}}}', "rule 'x'"),
        ('{"rules": {"x": {"description": 3, "settings": {}}}}', "description"),
        ('{"rules": {"x": {"description": "d"}}}', "settings"),
        ('{"rules": {"x": {"settings": {"layer_height": 0.1}}}}', "string values"),
    ])
    def test_bad_files(self, text, fragment):
        with pytest.raises(ValueError, match=fragment):
            parse_rules(text)


class TestProjects:
    @pytest.mark.asyncio
    async def test_lists_and_caches(self, tmp_path):
        state = CatalogState(None, "t", [REPO], tmp_path)
        get_json, seen = _fake_get_json(API_RESPONSES)
        with patch.object(github_catalog, "_get_json", get_json):
            first = await projects(state)
            second = await projects(state)
        assert len(first) == 7
        assert second is first
        assert len(seen) == 3
        assert [p["name"] for p in first] == sorted(p["name"] for p in first)
        assert ARTIFACT_ID in state.artifacts

    @pytest.mark.asyncio
    async def test_refetches_when_stale(self, tmp_path):
        state = CatalogState(None, "t", [REPO], tmp_path)
        get_json, seen = _fake_get_json(API_RESPONSES)
        with patch.object(github_catalog, "_get_json", get_json):
            await projects(state)
            state.fetched_at -= 61
            await projects(state)
        assert len(seen) == 6

    @pytest.mark.asyncio
    async def test_no_successful_run(self, tmp_path):
        state = CatalogState(None, "t", [REPO], tmp_path)
        responses = dict(API_RESPONSES)
        responses[f"/repos/{REPO}/actions/runs?branch=main&status=success&per_page=1"] = {"workflow_runs": []}
        get_json, _ = _fake_get_json(responses)
        with patch.object(github_catalog, "_get_json", get_json):
            assert await projects(state) == []

    @pytest.mark.asyncio
    async def test_find_unknown_artifact(self, tmp_path):
        context = make_context(tmp_path)
        with pytest.raises(GithubError) as raised:
            await find_artifact(context.catalog, 1)
        assert raised.value.status == 404


class TestRules:
    @pytest.mark.asyncio
    async def test_missing_file(self, tmp_path):
        async def request(state, path, accept=None):
            return 404, b'{"message": "Not Found"}'
        with patch.object(github_catalog, "_request", request):
            assert await fetch_rules(CatalogState(None, "t", [REPO], tmp_path), REPO, HEAD_SHA) is None

    @pytest.mark.asyncio
    async def test_valid_file(self, tmp_path):
        seen = []

        async def request(state, path, accept=None):
            seen.append((path, accept))
            return 200, b'{"rules": {"lid": {"settings": {"layer_height": "0.1"}}}}'
        with patch.object(github_catalog, "_request", request):
            rules = await fetch_rules(CatalogState(None, "t", [REPO], tmp_path), REPO, HEAD_SHA)
        assert rules["rules"]["lid"]["settings"] == {"layer_height": "0.1"}
        assert seen == [(f"/repos/{REPO}/contents/.autoslice.json?ref={HEAD_SHA}", "application/vnd.github.raw+json")]

    @pytest.mark.asyncio
    async def test_bad_file_disables_rules(self, tmp_path, capsys):
        async def request(state, path, accept=None):
            return 200, b'{"rules": ['
        with patch.object(github_catalog, "_request", request):
            assert await fetch_rules(CatalogState(None, "t", [REPO], tmp_path), REPO, HEAD_SHA) is None
        assert ".autoslice.json ignored" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_github_failure_raises(self, tmp_path):
        async def request(state, path, accept=None):
            return 403, b'{"message": "API rate limit exceeded"}'
        with patch.object(github_catalog, "_request", request):
            with pytest.raises(GithubError, match="rate limit") as raised:
                await fetch_rules(CatalogState(None, "t", [REPO], tmp_path), REPO, HEAD_SHA)
        assert raised.value.status == 502

    @pytest.mark.asyncio
    async def test_project_rules_fetched_once(self, tmp_path):
        context = make_context(tmp_path)
        context.catalog.rules.clear()
        calls = []

        async def fetch(state, repo, head_sha):
            calls.append((repo, head_sha))
            return {"matrix": True, "rules": {}}
        with patch.object(github_catalog, "fetch_rules", fetch):
            await project_rules(context.catalog, ARTIFACT_ID)
            await project_rules(context.catalog, ARTIFACT_ID)
        assert calls == [(REPO, HEAD_SHA)]


class TestArtifactCache:
    def test_store_artifact(self, tmp_path):
        store_artifact(_zip_bytes(["a.stl", "b.stl"]), tmp_path / "1")
        assert (tmp_path / "1" / "artifact.zip").exists()
        assert sorted(p.name for p in (tmp_path / "1" / "models").iterdir()) == ["a.stl", "b.stl"]

    @pytest.mark.asyncio
    async def test_models_downloads_once(self, tmp_path):
        context = make_context(tmp_path)
        other = 12958753687  # camera-arm-7300786
        downloads = []

        async def download(state, artifact):
            downloads.append(artifact.project["artifact_id"])
            return _zip_bytes(["camera-arm.stl", "notes.txt"])
        with patch.object(github_catalog, "_download", download):
            first = await models(context.catalog, other)
            second = await models(context.catalog, other)
        assert first == second == [{"name": "camera-arm.stl", "size": 19}]
        assert downloads == [other]

    @pytest.mark.asyncio
    async def test_unknown_model(self, tmp_path):
        context = make_context(tmp_path)
        with pytest.raises(GithubError) as raised:
            await thumbnail(context.catalog, ARTIFACT_ID, "../../etc/passwd")
        assert raised.value.status == 404


class TestThumbnail:
    @pytest.mark.asyncio
    async def test_renders_once(self, tmp_path):
        context = make_context(tmp_path)
        renders = []

        def render(stl_path, output_path, width, height, rotation=(0.0, 0.0, 0.0)):
            renders.append((stl_path, width, height))
            output_path.write_bytes(b"PNG")
            return True
        with patch.object(github_catalog, "render_stl_thumbnail", render):
            first = await thumbnail(context.catalog, ARTIFACT_ID, MODEL)
            second = await thumbnail(context.catalog, ARTIFACT_ID, MODEL)
        assert first == second == base64.b64encode(b"PNG").decode()
        assert len(renders) == 1
        assert renders[0][0].is_absolute()
        assert renders[0][1:] == (300, 300)

    @pytest.mark.asyncio
    async def test_render_failure(self, tmp_path):
        context = make_context(tmp_path)
        with patch.object(github_catalog, "render_stl_thumbnail", lambda *args: False):
            with pytest.raises(GithubError) as raised:
                await thumbnail(context.catalog, ARTIFACT_ID, MODEL)
        assert raised.value.status == 500
        assert not list((context.config.github_cache_dir / str(ARTIFACT_ID) / "thumbnails").iterdir())


class TestRequests:
    def test_failure_with_message(self):
        error = _failure(403, "/x", b'{"message": "API rate limit exceeded"}')
        assert (error.status, str(error)) == (502, "GitHub answered 403 for /x: API rate limit exceeded")

    def test_failure_without_message(self):
        assert str(_failure(500, "/x", b"<html>")) == "GitHub answered 500 for /x"

    @pytest.mark.asyncio
    async def test_network_error(self, tmp_path):
        class RefusingSession:
            def get(self, url, headers=None):
                raise aiohttp.ClientConnectionError()
        with pytest.raises(GithubError, match="ClientConnectionError") as raised:
            await _request(CatalogState(RefusingSession(), "t", [REPO], tmp_path), "/x")
        assert raised.value.status == 502

    @pytest.mark.asyncio
    async def test_redirect_followed_without_token(self, tmp_path, aiohttp_server, monkeypatch):
        """The zip endpoint redirects to blob storage on another host, which must not see the token."""
        seen = {}

        async def blob(request):
            seen["blob_auth"] = request.headers.get("Authorization")
            return web.Response(body=b"zipbytes")
        blob_app = web.Application()
        blob_app.router.add_get("/blob", blob)
        blob_server = await aiohttp_server(blob_app)

        async def api(request):
            seen["api_auth"] = request.headers.get("Authorization")
            raise web.HTTPFound(str(blob_server.make_url("/blob")))
        api_app = web.Application()
        api_app.router.add_get("/repos/{owner}/{repo}/actions/artifacts/{id}/zip", api)
        api_server = await aiohttp_server(api_app)
        monkeypatch.setattr(github_catalog, "API_ROOT", str(api_server.make_url("")).rstrip("/"))
        async with aiohttp.ClientSession() as session:
            state = CatalogState(session, "secret-token", [REPO], tmp_path)
            assert await _request(state, f"/repos/{REPO}/actions/artifacts/1/zip") == (200, b"zipbytes")
        assert seen == {"api_auth": "Bearer secret-token", "blob_auth": None}

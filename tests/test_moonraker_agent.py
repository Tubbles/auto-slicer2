"""Tests for the Moonraker agent's JSON-RPC framing, against a fake websocket and a fake Moonraker."""

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from auto_slicer import moonraker_agent
from auto_slicer.moonraker_agent import (
    AgentLink, IDENTIFY_ID, INITIAL_BACKOFF, MAX_BACKOFF, _session, answer, error_response,
    identify, identify_request, job_event, method_name, reply_error, result_response,
    retry_delay, send_job_event, serve, websocket_url,
)

from auto_slicer.handlers import _start_github, post_shutdown

from .github_helpers import ARTIFACT_ID, MODEL, make_config, make_context


def _text(message: dict) -> SimpleNamespace:
    """A websocket text frame as aiohttp yields it."""
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(message))


class FakeWebSocket:
    """Yields the given frames, records what is sent."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)


class TestFraming:
    def test_websocket_url(self):
        assert websocket_url("http://fluiddpi:7125/") == "http://fluiddpi:7125/websocket"

    def test_identify_request(self):
        request = identify_request("")
        assert request["method"] == "server.connection.identify"
        assert request["id"] == IDENTIFY_ID
        assert request["params"]["type"] == "agent"
        assert request["params"]["client_name"] == "autoslice"
        assert {"version", "url"} <= set(request["params"])
        assert "api_key" not in request["params"]

    def test_identify_with_api_key(self):
        assert identify_request("k")["params"]["api_key"] == "k"

    def test_job_event_is_a_notification(self):
        event = job_event({"id": "j"})
        assert event == {"jsonrpc": "2.0", "method": "connection.send_event",
                         "params": {"event": "autoslice.job", "data": {"id": "j"}}}

    def test_responses(self):
        assert result_response(7, [1]) == {"jsonrpc": "2.0", "result": [1], "id": 7}
        assert error_response(7, 404, "x") == {"jsonrpc": "2.0", "error": {"code": 404, "message": "x"}, "id": 7}

    def test_reply_error(self):
        assert reply_error({"jsonrpc": "2.0", "result": {"connection_id": 1}, "id": 1}, 1) == ""
        assert reply_error({"jsonrpc": "2.0", "error": {"code": 400, "message": "taken"}, "id": 1}, 1) == "taken"
        assert reply_error({"jsonrpc": "2.0", "result": "ok", "id": 2}, 1) is None
        assert reply_error({"jsonrpc": "2.0", "method": "notify_status_update", "params": []}, 1) is None

    def test_method_name(self):
        assert method_name("autoslice.projects") == "projects"
        assert method_name("other.projects") is None
        assert method_name(None) is None

    def test_retry_delay(self):
        delays, delay = [], None
        for _ in range(6):
            delay = retry_delay(delay, connected=False)
            delays.append(delay)
        assert delays == [INITIAL_BACKOFF, 10, 20, 40, MAX_BACKOFF, MAX_BACKOFF]
        assert retry_delay(MAX_BACKOFF, connected=True) == INITIAL_BACKOFF


class TestAnswer:
    @pytest.mark.asyncio
    async def test_result(self, tmp_path):
        context = make_context(tmp_path)
        request = {"jsonrpc": "2.0", "method": "autoslice.models", "params": {"artifact_id": ARTIFACT_ID}, "id": 9}
        response = await answer(context, request)
        assert response == {"jsonrpc": "2.0", "result": [{"name": MODEL, "size": 27}], "id": 9}

    @pytest.mark.asyncio
    async def test_service_error(self, tmp_path):
        request = {"jsonrpc": "2.0", "method": "autoslice.models", "params": {"artifact_id": "x"}, "id": 9}
        response = await answer(make_context(tmp_path), request)
        assert response["error"]["code"] == 400
        assert response["id"] == 9

    @pytest.mark.asyncio
    async def test_no_params(self, tmp_path):
        response = await answer(make_context(tmp_path), {"jsonrpc": "2.0", "method": "autoslice.jobs", "id": 3})
        assert response == {"jsonrpc": "2.0", "result": [], "id": 3}

    @pytest.mark.asyncio
    async def test_unknown_method(self, tmp_path):
        response = await answer(make_context(tmp_path), {"jsonrpc": "2.0", "method": "autoslice.nope", "id": 4})
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_positional_params(self, tmp_path):
        request = {"jsonrpc": "2.0", "method": "autoslice.models", "params": [ARTIFACT_ID], "id": 5}
        assert (await answer(make_context(tmp_path), request))["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_notifications_and_responses_ignored(self, tmp_path):
        context = make_context(tmp_path)
        assert await answer(context, {"jsonrpc": "2.0", "method": "notify_status_update", "params": [{}, 1.0]}) is None
        assert await answer(context, {"jsonrpc": "2.0", "result": "ok", "id": 1}) is None

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, tmp_path, monkeypatch):
        context = make_context(tmp_path)

        async def broken(state):
            raise OSError("disk full")
        monkeypatch.setattr("auto_slicer.github_catalog.projects", broken)
        response = await answer(context, {"jsonrpc": "2.0", "method": "autoslice.projects", "id": 6})
        assert response["error"] == {"code": 500, "message": "OSError: disk full"}


class TestFakeWebSocket:
    @pytest.mark.asyncio
    async def test_identify_success(self):
        ws = FakeWebSocket([
            _text({"jsonrpc": "2.0", "method": "notify_proc_stat_update", "params": [{}]}),
            _text({"jsonrpc": "2.0", "result": {"connection_id": 5}, "id": IDENTIFY_ID}),
        ])
        await identify(ws, "key")
        assert ws.sent == [identify_request("key")]

    @pytest.mark.asyncio
    async def test_identify_refused(self):
        ws = FakeWebSocket([_text({"jsonrpc": "2.0", "error": {"code": 400, "message": "Agent 'autoslice' already registered"}, "id": IDENTIFY_ID})])
        with pytest.raises(ConnectionError, match="already registered"):
            await identify(ws, "")

    @pytest.mark.asyncio
    async def test_identify_closed(self):
        with pytest.raises(ConnectionError, match="closed"):
            await identify(FakeWebSocket([]), "")

    @pytest.mark.asyncio
    async def test_serve_answers_requests_only(self, tmp_path):
        ws = FakeWebSocket([
            _text({"jsonrpc": "2.0", "method": "notify_status_update", "params": [{}, 1.0]}),
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="not json"),
            _text({"jsonrpc": "2.0", "method": "autoslice.options", "params": {"artifact_id": ARTIFACT_ID}, "id": 11}),
        ])
        await serve(make_context(tmp_path), ws)
        await asyncio.sleep(0.05)
        assert len(ws.sent) == 1
        assert ws.sent[0]["id"] == 11
        assert ws.sent[0]["result"]["matrix"] is True

    @pytest.mark.asyncio
    async def test_job_event_only_while_linked(self):
        link = AgentLink()
        await send_job_event(link, {"id": "j"})
        link.ws = FakeWebSocket([])
        await send_job_event(link, {"id": "j"})
        assert link.ws.sent == [job_event({"id": "j"})]


class TestFakeMoonraker:
    @pytest.mark.asyncio
    async def test_session(self, tmp_path, aiohttp_server):
        """Identify, ignore a notification, answer a relayed extension request, then see the close."""
        received = []

        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            identify_message = json.loads((await ws.receive()).data)
            received.append(identify_message)
            await ws.send_json({"jsonrpc": "2.0", "result": {"connection_id": 1}, "id": identify_message["id"]})
            await ws.send_json({"jsonrpc": "2.0", "method": "notify_status_update", "params": [{}, 1.0]})
            await ws.send_json({"jsonrpc": "2.0", "method": "autoslice.models",
                                "params": {"artifact_id": ARTIFACT_ID}, "id": 140234})
            received.append(json.loads((await ws.receive()).data))
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/websocket", websocket)
        server = await aiohttp_server(app)
        context = make_context(tmp_path)
        context.config.moonraker_url = str(server.make_url("")).rstrip("/")
        context.config.moonraker_api_key = "secret"
        link = AgentLink()
        async with aiohttp.ClientSession() as session:
            assert await _session(context, session, link) is True
        assert link.ws is None
        assert received[0]["params"]["api_key"] == "secret"
        assert received[1] == {"jsonrpc": "2.0", "result": [{"name": MODEL, "size": 27}], "id": 140234}

    @pytest.mark.asyncio
    async def test_refused_identify_raises(self, tmp_path, aiohttp_server):
        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            message = json.loads((await ws.receive()).data)
            await ws.send_json({"jsonrpc": "2.0", "error": {"code": 401, "message": "Unauthorized"}, "id": message["id"]})
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/websocket", websocket)
        server = await aiohttp_server(app)
        context = make_context(tmp_path)
        context.config.moonraker_url = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ConnectionError, match="Unauthorized"):
                await _session(context, session, AgentLink())


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_backoff_and_logging(self, tmp_path, monkeypatch, capsys):
        outcomes = [OSError("refused"), OSError("refused"), True, OSError("refused")]
        delays = []

        async def fake_session(context, session, link):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def fake_sleep(delay):
            delays.append(delay)
            if not outcomes:
                raise asyncio.CancelledError

        monkeypatch.setattr(moonraker_agent, "_session", fake_session)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        context = make_context(tmp_path)
        with pytest.raises(asyncio.CancelledError):
            await moonraker_agent.run_agent(context)
        assert delays == [INITIAL_BACKOFF, 10, INITIAL_BACKOFF, 10]
        assert capsys.readouterr().out.count("Cannot connect") == 2
        assert len(context.jobs.listeners) == 1


class TestStartup:
    @pytest.mark.asyncio
    async def test_off_leaves_the_bot_alone(self, tmp_path):
        app = SimpleNamespace(bot_data={})
        config = make_config(tmp_path)
        config.github_token = ""
        assert _start_github(app, config) is None
        assert app.bot_data == {}

    @pytest.mark.asyncio
    async def test_no_agent_without_moonraker_url(self, tmp_path):
        app = SimpleNamespace(bot_data={})
        github = _start_github(app, make_config(tmp_path))
        assert app.bot_data == {"_github": github}
        await post_shutdown(app)
        assert github.catalog.session.closed

    @pytest.mark.asyncio
    async def test_agent_started_and_stopped(self, tmp_path):
        app = SimpleNamespace(bot_data={})
        config = make_config(tmp_path)
        config.moonraker_url = "http://127.0.0.1:9"
        github = _start_github(app, config)
        task = app.bot_data["_agent_task"]
        await asyncio.sleep(0)
        assert not task.done()
        await post_shutdown(app)
        assert task.cancelled()
        assert github.catalog.session.closed

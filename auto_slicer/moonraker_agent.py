"""Moonraker agent: serves the GitHub slicing methods to KlipperScreen.

The bot connects to Moonraker's websocket and identifies as an agent
named "autoslice". A client calls a method through Moonraker's
server.extensions.request ({"agent": "autoslice", "method":
"autoslice.<name>", "arguments": {...}}). Moonraker relays it here as a
JSON-RPC request with the arguments as params and returns our result or
error to the client. Every job state change goes out as the agent event
"autoslice.job" (clients see notify_agent_event). No method is registered
with connection.register_remote_method: that call only exposes a method
to Klipper's gcode macros, which cannot receive a result.
"""

import asyncio
import json
from dataclasses import dataclass
from functools import partial

import aiohttp

from .github_catalog import GithubError
from .github_service import GithubContext, METHODS, call

AGENT_NAME = "autoslice"
AGENT_VERSION = "1.0"
AGENT_URL = "https://github.com/Tubbles/auto-slicer2"
METHOD_PREFIX = "autoslice."
JOB_EVENT = "autoslice.job"
IDENTIFY_ID = 1
IDENTIFY_TIMEOUT = 30
HEARTBEAT = 30
INITIAL_BACKOFF = 5
MAX_BACKOFF = 60


@dataclass
class AgentLink:
    ws: aiohttp.ClientWebSocketResponse | None = None  # set while identified


# --- Pure framing ---


def websocket_url(url: str) -> str:
    """Moonraker's websocket endpoint for a base URL like http://fluiddpi:7125."""
    return url.rstrip("/") + "/websocket"


def identify_request(api_key: str) -> dict:
    """server.connection.identify as an agent."""
    params = {"client_name": AGENT_NAME, "version": AGENT_VERSION, "type": "agent", "url": AGENT_URL}
    if api_key:
        params["api_key"] = api_key
    return {"jsonrpc": "2.0", "method": "server.connection.identify", "params": params, "id": IDENTIFY_ID}


def job_event(job: dict) -> dict:
    """connection.send_event for a job change, without an id, so Moonraker sends no reply."""
    params = {"event": JOB_EVENT, "data": job}
    return {"jsonrpc": "2.0", "method": "connection.send_event", "params": params}


def result_response(request_id, result) -> dict:
    """A JSON-RPC result."""
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def error_response(request_id, code: int, message: str) -> dict:
    """A JSON-RPC error."""
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}


def reply_error(message: dict, request_id: int) -> str | None:
    """For the reply to our request request_id: its error message, or "" on success. None for other messages."""
    if "method" in message or message.get("id") != request_id:
        return None
    error = message.get("error")
    if error is None:
        return ""
    return str(error.get("message") if isinstance(error, dict) else error) or "unknown error"


def retry_delay(previous: float | None, connected: bool) -> float:
    """Seconds before the next connection attempt: 5 after a session or at first, doubling to 60."""
    if connected or previous is None:
        return INITIAL_BACKOFF
    return min(previous * 2, MAX_BACKOFF)


def method_name(rpc_method) -> str | None:
    """Our method name for "autoslice.<name>", or None."""
    if not isinstance(rpc_method, str) or not rpc_method.startswith(METHOD_PREFIX):
        return None
    return rpc_method[len(METHOD_PREFIX):]


# --- Requests ---


async def answer(context: GithubContext, message: dict) -> dict | None:
    """The response to one message from Moonraker, None for anything that is not a request."""
    if "method" not in message or "id" not in message:
        return None
    request_id, name = message["id"], method_name(message["method"])
    if name not in METHODS:
        return error_response(request_id, -32601, f"Method not found: {message['method']}")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return error_response(request_id, -32602, "Invalid params: arguments must be an object")
    return await _call(context, request_id, name, params)


async def _call(context: GithubContext, request_id, name: str, params: dict) -> dict:
    """Run a method and wrap its outcome. Error codes are HTTP statuses, as Moonraker's own are."""
    try:
        return result_response(request_id, await call(context, name, params))
    except GithubError as error:
        return error_response(request_id, error.status, str(error))
    except Exception as error:
        print(f"[Agent] {name} failed: {error}", flush=True)
        return error_response(request_id, 500, f"{type(error).__name__}: {error}")


async def _send(ws, message: dict) -> None:
    """Send one JSON-RPC message. A closed socket only gets logged."""
    try:
        await ws.send_str(json.dumps(message))
    except (ConnectionError, RuntimeError) as error:
        print(f"[Agent] Send failed: {error}", flush=True)


async def _handle(context: GithubContext, ws, text: str) -> None:
    """Answer one websocket text message if it is a request."""
    try:
        message = json.loads(text)
    except ValueError:
        return
    response = await answer(context, message) if isinstance(message, dict) else None
    if response is not None:
        await _send(ws, response)


async def send_job_event(link: AgentLink, job: dict) -> None:
    """Job listener: forward a job change while identified."""
    if link.ws is not None and not link.ws.closed:
        await _send(link.ws, job_event(job))


# --- Connection ---


async def identify(ws, api_key: str) -> None:
    """Identify as the agent. Raises ConnectionError when Moonraker refuses or closes."""
    await ws.send_str(json.dumps(identify_request(api_key)))
    async for message in ws:
        if message.type != aiohttp.WSMsgType.TEXT:
            break
        error = reply_error(json.loads(message.data), IDENTIFY_ID)
        if error is not None:
            if error:
                raise ConnectionError(f"identify refused: {error}")
            return
    raise ConnectionError("closed before identify finished")


async def serve(context: GithubContext, ws) -> None:
    """Answer requests until the connection closes. Each runs in its own task so a slow one blocks nothing."""
    tasks = set()
    async for message in ws:
        if message.type == aiohttp.WSMsgType.ERROR:
            break
        if message.type == aiohttp.WSMsgType.TEXT:
            task = asyncio.create_task(_handle(context, ws, message.data))
            tasks.add(task)
            task.add_done_callback(tasks.discard)


async def _session(context: GithubContext, session: aiohttp.ClientSession, link: AgentLink) -> bool:
    """One connection, served until it closes. Returns whether it got identified."""
    url = context.config.moonraker_url
    async with session.ws_connect(websocket_url(url), heartbeat=HEARTBEAT) as ws:
        await asyncio.wait_for(identify(ws, context.config.moonraker_api_key), IDENTIFY_TIMEOUT)
        print(f"[Agent] Connected to {url} as {AGENT_NAME}", flush=True)
        link.ws = ws
        try:
            await serve(context, ws)
        finally:
            link.ws = None
    print(f"[Agent] Disconnected from {url}", flush=True)
    return True


async def run_agent(context: GithubContext) -> None:
    """Keep the agent connected forever, retrying with backoff. Logs only the first failure in a row."""
    link = AgentLink()
    context.jobs.listeners.append(partial(send_job_event, link))
    delay, failing = None, False
    # Its own session: the GitHub session's sock_read limit is meant for HTTP
    # transfers, not for a websocket that may sit idle between requests
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                connected = await _session(context, session, link)
            except Exception as error:  # the agent must outlive any one failed connection
                connected = False
                if not failing:
                    _log_failure(context.config.moonraker_url, error)
            failing = not connected
            delay = retry_delay(delay, connected)
            await asyncio.sleep(delay)


def _log_failure(url: str, error: Exception) -> None:
    """Log a failed connection attempt."""
    reason = str(error) or type(error).__name__
    print(f"[Agent] Cannot connect to {url}: {reason} (retrying every {INITIAL_BACKOFF} to {MAX_BACKOFF}s)", flush=True)

"""HTTP API for the Telegram Mini App.

Provides endpoints for the Mini App to read the settings registry,
read/write per-user overrides, and apply presets. Uses aiohttp.

Authentication is via Telegram initData HMAC validation.
"""

import json
import time

from aiohttp import web

from .config import Config
from .presets import load_presets
from .settings_registry import SettingDefinition
from .settings_validate import validate
from .web_auth import validate_init_data


def _setting_to_dict(defn: SettingDefinition) -> dict:
    """Serialize a SettingDefinition to a JSON-friendly dict."""
    d = {
        "key": defn.key,
        "label": defn.label,
        "description": defn.description,
        "type": defn.setting_type,
        "default_value": defn.default_value,
        "category": defn.category,
    }
    if defn.unit:
        d["unit"] = defn.unit
    if defn.minimum_value is not None:
        d["minimum_value"] = defn.minimum_value
    if defn.maximum_value is not None:
        d["maximum_value"] = defn.maximum_value
    if defn.minimum_value_warning is not None:
        d["minimum_value_warning"] = defn.minimum_value_warning
    if defn.maximum_value_warning is not None:
        d["maximum_value_warning"] = defn.maximum_value_warning
    if defn.options:
        d["options"] = defn.options
    return d


def _build_registry_response(config: Config) -> dict:
    """Build the full registry response: settings grouped by category, presets, defaults."""
    all_settings = config.registry.all_settings()

    # Group settings by category
    categories: dict[str, list[dict]] = {}
    settings_list = []
    for key, defn in all_settings.items():
        sd = _setting_to_dict(defn)
        settings_list.append(sd)
        cat = defn.category or "Other"
        categories.setdefault(cat, []).append(sd)

    presets = load_presets()
    preset_data = {
        name: {"description": p["description"], "settings": p["settings"]}
        for name, p in presets.items()
    }

    return {
        "settings": settings_list,
        "categories": categories,
        "presets": preset_data,
        "defaults": config.defaults,
    }


def _validate_overrides(config: Config, overrides: dict[str, str]) -> dict:
    """Validate a batch of key:value pairs against the registry.

    Returns {"applied": {key: value}, "errors": {key: msg}, "warnings": {key: msg}}.
    """
    applied = {}
    errors = {}
    warnings = {}

    for key, raw_value in overrides.items():
        defn = config.registry.get(key)
        if not defn:
            errors[key] = f"Unknown setting: '{key}'"
            continue
        result = validate(defn, str(raw_value))
        if not result.ok:
            errors[key] = result.error
        else:
            applied[key] = result.coerced_value
            if result.warning:
                warnings[key] = result.warning

    return {"applied": applied, "errors": errors, "warnings": warnings}


def _extract_user_id(request: web.Request) -> tuple[int | None, str]:
    """Extract and validate user_id from the Authorization header."""
    config: Config = request.app["config"]
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        return None, "missing or invalid Authorization header"
    init_data = auth[4:]
    return validate_init_data(init_data, config.telegram_token)


async def handle_registry(request: web.Request) -> web.Response:
    """GET /api/registry — return the full settings registry."""
    config: Config = request.app["config"]

    # Use cached response if available
    cached = request.app.get("_registry_cache")
    if cached is None:
        cached = json.dumps(_build_registry_response(config))
        request.app["_registry_cache"] = cached

    return web.Response(text=cached, content_type="application/json")


async def handle_get_settings(request: web.Request) -> web.Response:
    """GET /api/settings — return current user overrides."""
    user_id, error = _extract_user_id(request)
    if user_id is None:
        return web.json_response({"error": error}, status=401)

    user_settings: dict = request.app["user_settings"]
    overrides = user_settings.get(user_id, {})
    return web.json_response({"overrides": overrides})


async def handle_post_settings(request: web.Request) -> web.Response:
    """POST /api/settings — validate and apply user overrides.

    Body: {"overrides": {"key": "value", ...}, "remove": ["key", ...]}
    """
    user_id, error = _extract_user_id(request)
    if user_id is None:
        return web.json_response({"error": error}, status=401)

    config: Config = request.app["config"]
    user_settings: dict = request.app["user_settings"]

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON body"}, status=400)

    # Validate and apply overrides
    new_overrides = body.get("overrides", {})
    remove_keys = body.get("remove", [])

    result = _validate_overrides(config, new_overrides)

    # Apply valid settings
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id].update(result["applied"])

    # Remove requested keys
    for key in remove_keys:
        user_settings[user_id].pop(key, None)
    if not user_settings[user_id]:
        user_settings.pop(user_id, None)

    return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/health — simple health check, no auth required."""
    return web.json_response({"status": "ok", "time": int(time.time())})


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.Response:
    """Add CORS headers for the Mini App frontend."""
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        response = await handler(request)

    allowed_origin = request.app.get("cors_origin", "*")
    response.headers["Access-Control-Allow-Origin"] = allowed_origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return response


@web.middleware
async def logging_middleware(request: web.Request, handler) -> web.Response:
    """Log all incoming requests."""
    start = time.time()
    try:
        response = await handler(request)
        elapsed = (time.time() - start) * 1000
        print(f"[API] {request.method} {request.path} → {response.status} ({elapsed:.0f}ms)")
        return response
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        print(f"[API] {request.method} {request.path} → ERROR: {e} ({elapsed:.0f}ms)")
        raise


def create_web_app(config: Config, user_settings: dict, cors_origin: str = "*") -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application(middlewares=[logging_middleware, cors_middleware])
    app["config"] = config
    app["user_settings"] = user_settings
    app["cors_origin"] = cors_origin

    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/registry", handle_registry)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_post_settings)

    return app

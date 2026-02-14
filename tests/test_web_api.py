"""Tests for web API pure helpers."""

import time
from pathlib import Path

import pytest
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from auto_slicer.settings_registry import SettingDefinition, SettingsRegistry
from auto_slicer.config import Config
from auto_slicer.web_api import (
    _setting_to_dict, _build_registry_response, _validate_overrides,
    create_web_app, generate_token, validate_token, cleanup_expired,
    TOKEN_TTL, TOKEN_MAX_TTL,
)


def _make_defn(**kwargs) -> SettingDefinition:
    """Create a SettingDefinition with sensible defaults."""
    defaults = {
        "key": "test_key",
        "label": "Test Label",
        "description": "A test setting",
        "setting_type": "float",
        "default_value": 1.0,
        "unit": "mm",
        "minimum_value": 0.0,
        "maximum_value": 10.0,
        "minimum_value_warning": 0.1,
        "maximum_value_warning": 9.0,
        "options": {},
        "category": "Test Category",
    }
    defaults.update(kwargs)
    return SettingDefinition(**defaults)


def _make_registry(settings: dict[str, SettingDefinition]) -> SettingsRegistry:
    """Create a minimal SettingsRegistry."""
    label_map = {d.label.lower(): k for k, d in settings.items()}
    norm_map = {k.lower().replace(" ", "_"): k for k in settings}
    return SettingsRegistry(settings, label_map, norm_map)


def _make_config(
    settings: dict[str, SettingDefinition] | None = None,
    defaults: dict[str, str] | None = None,
) -> Config:
    """Create a Config with a mock registry."""
    if settings is None:
        settings = {"test_key": _make_defn()}
    registry = _make_registry(settings)
    return Config(
        archive_dir=Path("."),
        cura_bin=Path("."),
        def_dir=Path("."),
        printer_def="",
        defaults=defaults if defaults is not None else {"test_key": "1.0"},
        telegram_token="test:token",
        allowed_users={42, 999},
        notify_chat_id=None,
        registry=registry,
    )


def _add_token(app, user_id: int = 42) -> str:
    """Generate a token, store it in the app, and return it."""
    token = generate_token()
    app["tokens"][token] = (user_id, time.time() + TOKEN_TTL, time.time())
    return token


def _bearer(token: str) -> dict:
    """Return an Authorization header dict for a Bearer token."""
    return {"Authorization": f"Bearer {token}"}


class TestSettingToDict:
    def test_basic_fields(self):
        defn = _make_defn()
        d = _setting_to_dict(defn)
        assert d["key"] == "test_key"
        assert d["label"] == "Test Label"
        assert d["description"] == "A test setting"
        assert d["type"] == "float"
        assert d["default_value"] == 1.0
        assert d["category"] == "Test Category"

    def test_includes_unit(self):
        d = _setting_to_dict(_make_defn(unit="mm"))
        assert d["unit"] == "mm"

    def test_omits_empty_unit(self):
        d = _setting_to_dict(_make_defn(unit=""))
        assert "unit" not in d

    def test_includes_bounds(self):
        d = _setting_to_dict(_make_defn(minimum_value=0.5, maximum_value=5.0))
        assert d["minimum_value"] == 0.5
        assert d["maximum_value"] == 5.0

    def test_omits_none_bounds(self):
        d = _setting_to_dict(_make_defn(
            minimum_value=None, maximum_value=None,
            minimum_value_warning=None, maximum_value_warning=None,
        ))
        assert "minimum_value" not in d
        assert "maximum_value" not in d
        assert "minimum_value_warning" not in d
        assert "maximum_value_warning" not in d

    def test_includes_options(self):
        d = _setting_to_dict(_make_defn(
            setting_type="enum",
            options={"a": "Alpha", "b": "Beta"},
        ))
        assert d["options"] == {"a": "Alpha", "b": "Beta"}

    def test_omits_empty_options(self):
        d = _setting_to_dict(_make_defn(options={}))
        assert "options" not in d

    def test_includes_value_expression(self):
        d = _setting_to_dict(_make_defn(value_expression="layer_height * 2"))
        assert d["value_expression"] == "layer_height * 2"

    def test_omits_none_value_expression(self):
        d = _setting_to_dict(_make_defn(value_expression=None))
        assert "value_expression" not in d


class TestBuildRegistryResponse:
    def test_structure(self):
        config = _make_config()
        resp = _build_registry_response(config)
        assert "settings" in resp
        assert "categories" in resp
        assert "presets" in resp
        assert "defaults" in resp

    def test_settings_list(self):
        config = _make_config()
        resp = _build_registry_response(config)
        assert len(resp["settings"]) == 1
        assert resp["settings"][0]["key"] == "test_key"

    def test_categories_grouping(self):
        config = _make_config()
        resp = _build_registry_response(config)
        assert "Test Category" in resp["categories"]
        assert len(resp["categories"]["Test Category"]) == 1

    def test_presets_present(self):
        config = _make_config()
        resp = _build_registry_response(config)
        assert "draft" in resp["presets"]
        assert "description" in resp["presets"]["draft"]
        assert "settings" in resp["presets"]["draft"]

    def test_defaults_from_config(self):
        config = _make_config()
        resp = _build_registry_response(config)
        assert resp["defaults"]["test_key"] == "1.0"

    def test_reverse_deps_present(self):
        settings = {
            "a": _make_defn(key="a"),
            "b": _make_defn(key="b", value_expression="a * 2"),
        }
        config = _make_config(settings)
        resp = _build_registry_response(config)
        assert "reverse_deps" in resp
        assert "a" in resp["reverse_deps"]
        assert "b" in resp["reverse_deps"]["a"]

    def test_multiple_categories(self):
        settings = {
            "a": _make_defn(key="a", category="Cat A"),
            "b": _make_defn(key="b", category="Cat B"),
        }
        config = _make_config(settings)
        resp = _build_registry_response(config)
        assert "Cat A" in resp["categories"]
        assert "Cat B" in resp["categories"]

    def test_empty_category_becomes_other(self):
        settings = {"x": _make_defn(key="x", category="")}
        config = _make_config(settings)
        resp = _build_registry_response(config)
        assert "Other" in resp["categories"]

    def test_pinned_expression_stripped(self):
        """Config default pins expression — frontend should not see value_expression."""
        settings = {
            "pinned": _make_defn(key="pinned", value_expression="layer_height * 2"),
        }
        config = _make_config(settings, defaults={"pinned": "2"})
        resp = _build_registry_response(config)
        sd = resp["settings"][0]
        assert sd["key"] == "pinned"
        assert "value_expression" not in sd

    def test_unpinned_expression_kept(self):
        """No config default — frontend should see value_expression."""
        settings = {
            "free": _make_defn(key="free", value_expression="layer_height * 2"),
        }
        config = _make_config(settings, defaults={})
        resp = _build_registry_response(config)
        sd = resp["settings"][0]
        assert sd["key"] == "free"
        assert sd["value_expression"] == "layer_height * 2"


class TestValidateOverrides:
    def test_valid_float(self):
        config = _make_config()
        result = _validate_overrides(config, {"test_key": "2.0"})
        assert result["applied"] == {"test_key": "2.0"}
        assert result["errors"] == {}

    def test_invalid_value(self):
        config = _make_config()
        result = _validate_overrides(config, {"test_key": "abc"})
        assert result["applied"] == {}
        assert "test_key" in result["errors"]

    def test_unknown_key(self):
        config = _make_config()
        result = _validate_overrides(config, {"nonexistent": "1"})
        assert "nonexistent" in result["errors"]
        assert result["applied"] == {}

    def test_warning(self):
        config = _make_config()
        # 0.05 is below minimum_value_warning of 0.1 but above minimum_value of 0.0
        result = _validate_overrides(config, {"test_key": "0.05"})
        assert "test_key" in result["applied"]
        assert "test_key" in result["warnings"]

    def test_mixed_valid_and_invalid(self):
        settings = {
            "good": _make_defn(key="good"),
            "also_good": _make_defn(key="also_good"),
        }
        config = _make_config(settings)
        result = _validate_overrides(config, {
            "good": "2.0",
            "also_good": "not_a_number",
            "missing": "1",
        })
        assert "good" in result["applied"]
        assert "also_good" in result["errors"]
        assert "missing" in result["errors"]

    def test_bool_setting(self):
        settings = {
            "my_bool": _make_defn(
                key="my_bool", setting_type="bool", default_value=False,
                minimum_value=None, maximum_value=None,
                minimum_value_warning=None, maximum_value_warning=None,
            ),
        }
        config = _make_config(settings)
        result = _validate_overrides(config, {"my_bool": "true"})
        assert result["applied"] == {"my_bool": "true"}

    def test_enum_setting(self):
        settings = {
            "my_enum": _make_defn(
                key="my_enum", setting_type="enum", default_value="a",
                options={"a": "Alpha", "b": "Beta"},
                minimum_value=None, maximum_value=None,
                minimum_value_warning=None, maximum_value_warning=None,
            ),
        }
        config = _make_config(settings)
        result = _validate_overrides(config, {"my_enum": "b"})
        assert result["applied"] == {"my_enum": "b"}

    def test_empty_overrides(self):
        config = _make_config()
        result = _validate_overrides(config, {})
        assert result["applied"] == {}
        assert result["errors"] == {}
        assert result["warnings"] == {}


# --- Token helper tests ---


class TestGenerateToken:
    def test_returns_string(self):
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_unique(self):
        tokens = {generate_token() for _ in range(100)}
        assert len(tokens) == 100


class TestValidateToken:
    def test_valid_token(self):
        now = time.time()
        tokens = {"abc": (42, now + 600, now)}
        assert validate_token(tokens, "abc") == 42

    def test_refreshes_ttl(self):
        now = time.time()
        old_expiry = now + 10
        tokens = {"abc": (42, old_expiry, now)}
        validate_token(tokens, "abc")
        _, new_expiry, created = tokens["abc"]
        assert new_expiry > old_expiry
        assert created == now  # creation time unchanged

    def test_expired_token(self):
        now = time.time()
        tokens = {"abc": (42, now - 1, now - 600)}
        assert validate_token(tokens, "abc") is None
        assert "abc" not in tokens

    def test_max_ttl_exceeded(self):
        now = time.time()
        created = now - TOKEN_MAX_TTL - 1
        tokens = {"abc": (42, now + 600, created)}
        assert validate_token(tokens, "abc") is None
        assert "abc" not in tokens

    def test_unknown_token(self):
        tokens = {}
        assert validate_token(tokens, "nope") is None

    def test_cleanup_expired(self):
        now = time.time()
        tokens = {
            "good": (1, now + 600, now),
            "bad": (2, now - 1, now - 600),
            "also_bad": (3, now - 100, now - 200),
        }
        cleanup_expired(tokens)
        assert "good" in tokens
        assert "bad" not in tokens
        assert "also_bad" not in tokens

    def test_cleanup_max_ttl(self):
        now = time.time()
        tokens = {
            "fresh": (1, now + 600, now),
            "ancient": (2, now + 600, now - TOKEN_MAX_TTL - 1),
        }
        cleanup_expired(tokens)
        assert "fresh" in tokens
        assert "ancient" not in tokens


# --- Integration test fixtures ---


@pytest.fixture
def app_with_user():
    """Create a test app with one user's overrides pre-populated."""
    config = _make_config()
    user_settings = {42: {"test_key": "5.0"}}
    saved = []
    save_fn = lambda: saved.append(dict(user_settings))
    starred = {"layer_height", "speed_print"}
    starred_saved = []
    save_starred_fn = lambda: starred_saved.append(set(starred))
    tokens = {}
    app = create_web_app(
        config, user_settings, save_fn=save_fn,
        starred_keys=starred, save_starred_fn=save_starred_fn,
        tokens=tokens,
    )
    return app, user_settings, saved, starred, starred_saved


# --- Auth middleware tests ---


class TestAuthMiddleware:
    @pytest.fixture(autouse=True)
    def _setup(self, app_with_user):
        self.app, _, _, _, _ = app_with_user

    @pytest.mark.asyncio
    async def test_health_no_auth(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.get("/api/health")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_options_no_auth(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.options("/api/settings")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_missing_token_401(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.get("/api/settings")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_invalid_token_401(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.get("/api/settings", headers=_bearer("bogus"))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_expired_token_401(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = "expired-tok"
        self.app["tokens"][token] = (42, time.time() - 1, time.time() - 600)
        resp = await client.get("/api/settings", headers=_bearer(token))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_token_passes(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app, 42)
        resp = await client.get("/api/settings", headers=_bearer(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_old_tma_scheme_rejected(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.get("/api/settings", headers={"Authorization": "tma fakedata"})
        assert resp.status == 401


# --- DELETE /api/settings integration tests ---


class TestDeleteSettings:
    @pytest.fixture(autouse=True)
    def _setup(self, app_with_user, aiohttp_client):
        self.app, self.user_settings, self.saved, _, _ = app_with_user
        self._aiohttp_client = aiohttp_client

    async def _client(self):
        return await self._aiohttp_client(self.app)

    @pytest.mark.asyncio
    async def test_clears_overrides(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app, 42)
        resp = await client.delete("/api/settings", headers=_bearer(token))
        assert resp.status == 200
        data = await resp.json()
        assert data == {"overrides": {}}
        assert 42 not in self.user_settings

    @pytest.mark.asyncio
    async def test_calls_save_fn(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app, 42)
        await client.delete("/api/settings", headers=_bearer(token))
        assert len(self.saved) == 1

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.delete("/api/settings")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_nonexistent_user_ok(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app, 999)
        resp = await client.delete("/api/settings", headers=_bearer(token))
        assert resp.status == 200
        data = await resp.json()
        assert data == {"overrides": {}}


class TestGetStarred:
    @pytest.fixture(autouse=True)
    def _setup(self, app_with_user):
        self.app, _, _, self.starred, _ = app_with_user

    @pytest.mark.asyncio
    async def test_returns_keys(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.get("/api/starred", headers=_bearer(token))
        assert resp.status == 200
        data = await resp.json()
        assert set(data["keys"]) == {"layer_height", "speed_print"}

    @pytest.mark.asyncio
    async def test_returns_sorted(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.get("/api/starred", headers=_bearer(token))
        data = await resp.json()
        assert data["keys"] == sorted(data["keys"])

    @pytest.mark.asyncio
    async def test_requires_auth(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.get("/api/starred")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_starred(self, aiohttp_client):
        self.starred.clear()
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.get("/api/starred", headers=_bearer(token))
        data = await resp.json()
        assert data["keys"] == []


class TestPostStarred:
    @pytest.fixture(autouse=True)
    def _setup(self, app_with_user):
        self.app, _, _, self.starred, self.starred_saved = app_with_user

    @pytest.mark.asyncio
    async def test_add_keys(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post(
            "/api/starred",
            json={"add": ["infill_sparse_density"]},
            headers=_bearer(token),
        )
        assert resp.status == 200
        data = await resp.json()
        assert "infill_sparse_density" in data["keys"]
        assert "layer_height" in data["keys"]

    @pytest.mark.asyncio
    async def test_remove_keys(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post(
            "/api/starred",
            json={"remove": ["layer_height"]},
            headers=_bearer(token),
        )
        assert resp.status == 200
        data = await resp.json()
        assert "layer_height" not in data["keys"]
        assert "speed_print" in data["keys"]

    @pytest.mark.asyncio
    async def test_requires_auth(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.post("/api/starred", json={"add": ["foo"]})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_calls_save_fn(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        await client.post(
            "/api/starred",
            json={"add": ["new_key"]},
            headers=_bearer(token),
        )
        assert len(self.starred_saved) == 1

    @pytest.mark.asyncio
    async def test_invalid_json(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post(
            "/api/starred",
            data="not json",
            headers={**_bearer(token), "Content-Type": "application/json"},
        )
        assert resp.status == 400


# --- POST /api/evaluate tests ---

@pytest.fixture
def eval_app():
    """App with settings that have value expressions."""
    settings = {
        "base": _make_defn(key="base", default_value=10.0, value_expression=None),
        "computed": _make_defn(key="computed", default_value=0.0, value_expression="base * 2"),
    }
    config = _make_config(settings)
    tokens = {}
    app = create_web_app(config, {}, tokens=tokens)
    return app


class TestEvaluateEndpoint:
    @pytest.fixture(autouse=True)
    def _setup(self, eval_app):
        self.app = eval_app

    @pytest.mark.asyncio
    async def test_returns_computed_values(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post("/api/evaluate", json={"overrides": {}}, headers=_bearer(token))
        assert resp.status == 200
        data = await resp.json()
        assert "computed" in data
        assert data["computed"]["computed"] == 20.0

    @pytest.mark.asyncio
    async def test_respects_overrides(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post("/api/evaluate", json={"overrides": {"base": "5"}}, headers=_bearer(token))
        assert resp.status == 200
        data = await resp.json()
        assert data["computed"]["computed"] == 10.0

    @pytest.mark.asyncio
    async def test_pinned_excluded_from_computed(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post("/api/evaluate", json={"overrides": {"computed": "99"}}, headers=_bearer(token))
        data = await resp.json()
        assert "computed" not in data["computed"]

    @pytest.mark.asyncio
    async def test_requires_auth(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        resp = await client.post("/api/evaluate", json={"overrides": {}})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_invalid_json(self, aiohttp_client):
        client = await aiohttp_client(self.app)
        token = _add_token(self.app)
        resp = await client.post(
            "/api/evaluate",
            data="not json",
            headers={**_bearer(token), "Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_errors_reported(self, aiohttp_client):
        # Create app with a bad expression
        settings = {
            "bad": _make_defn(key="bad", value_expression="1 / 0"),
        }
        config = _make_config(settings)
        app = create_web_app(config, {})
        token = _add_token(app)
        client = await aiohttp_client(app)
        resp = await client.post("/api/evaluate", json={"overrides": {}}, headers=_bearer(token))
        data = await resp.json()
        assert "bad" in data["errors"]

    @pytest.mark.asyncio
    async def test_config_defaults_pin_expressions(self, aiohttp_client):
        """Config defaults should prevent expression evaluation, same as user overrides."""
        settings = {
            "base": _make_defn(key="base", default_value=10.0),
            "computed": _make_defn(key="computed", default_value=0.0, value_expression="base * 2"),
        }
        config = _make_config(settings, defaults={"computed": "5"})
        app = create_web_app(config, {}, tokens={})
        token = _add_token(app)
        client = await aiohttp_client(app)
        resp = await client.post("/api/evaluate", json={"overrides": {}}, headers=_bearer(token))
        data = await resp.json()
        # computed should NOT appear — it's pinned by config default
        assert "computed" not in data["computed"]

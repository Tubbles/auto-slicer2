"""Tests for web API pure helpers."""

from pathlib import Path

import pytest

from auto_slicer.settings_registry import SettingDefinition, SettingsRegistry
from auto_slicer.config import Config
from auto_slicer.web_api import _setting_to_dict, _build_registry_response, _validate_overrides


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


def _make_config(settings: dict[str, SettingDefinition] | None = None) -> Config:
    """Create a Config with a mock registry."""
    if settings is None:
        settings = {"test_key": _make_defn()}
    registry = _make_registry(settings)
    return Config(
        archive_dir=Path("."),
        cura_bin=Path("."),
        def_dir=Path("."),
        printer_def="",
        defaults={"test_key": "1.0"},
        telegram_token="test:token",
        admin_users=set(),
        chat_users=set(),
        notify_chat_id=None,
        registry=registry,
    )


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

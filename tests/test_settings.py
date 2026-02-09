"""Tests for settings registry, matcher, and validator."""

import configparser
import pytest

from auto_slicer.config import Config
from auto_slicer.settings_registry import (
    SettingsRegistry, SettingDefinition,
    _flatten_settings, _apply_overrides, _build_indexes,
)
from auto_slicer.settings_match import resolve_setting, _match_exact_key, _match_substring
from auto_slicer.settings_validate import validate, ValidationResult
from auto_slicer.handlers import _parse_settings_args, _build_value_picker, _MAX_CALLBACK_DATA
from auto_slicer.presets import load_presets, BUILTIN_PRESETS


@pytest.fixture(scope="module")
def config():
    c = configparser.ConfigParser()
    c.read("config.ini")
    return Config(c)


@pytest.fixture(scope="module")
def registry(config):
    return config.registry




# --- SettingsRegistry tests ---

class TestSettingsRegistry:
    def test_loads_many_settings(self, registry):
        assert len(registry.all_settings()) > 500

    def test_known_float_setting(self, registry):
        defn = registry.get("layer_height")
        assert defn is not None
        assert defn.label == "Layer Height"
        assert defn.setting_type == "float"
        assert defn.unit == "mm"
        assert defn.minimum_value == pytest.approx(0.001)

    def test_known_bool_setting(self, registry):
        defn = registry.get("support_enable")
        assert defn is not None
        assert defn.label == "Generate Support"
        assert defn.setting_type == "bool"
        assert defn.default_value is False

    def test_known_enum_setting(self, registry):
        defn = registry.get("adhesion_type")
        assert defn is not None
        assert defn.setting_type == "enum"
        assert "skirt" in defn.options
        assert "brim" in defn.options

    def test_known_int_setting(self, registry):
        defn = registry.get("wall_line_count")
        assert defn is not None
        assert defn.setting_type == "int"

    def test_label_to_key_index(self, registry):
        label_map = registry.label_to_key()
        assert label_map["layer height"] == "layer_height"
        assert label_map["build plate adhesion type"] == "adhesion_type"

    def test_nonexistent_setting(self, registry):
        assert registry.get("nonexistent_setting_xyz") is None

    def test_inherits_chain_applies_overrides(self, registry):
        # The ender3 chain should override machine dimensions from fdmprinter defaults
        defn = registry.get("machine_width")
        assert defn is not None
        assert defn.default_value == 235  # Ender 3 specific

    def test_has_category(self, registry):
        defn = registry.get("layer_height")
        assert defn is not None
        assert defn.category != ""

    def test_expression_bounds_stored_as_none(self, registry):
        # layer_height has maximum_value_warning as expression
        defn = registry.get("layer_height")
        assert defn is not None
        # The expression "0.8 * min(extruderValues(...))" should be None
        assert defn.maximum_value_warning is None


# --- Pure registry function tests ---

class TestRegistryFunctions:
    def test_flatten_settings_simple(self):
        node = {
            "my_float": {
                "type": "float",
                "label": "My Float",
                "description": "A float",
                "default_value": 1.0,
                "unit": "mm",
            },
        }
        result = _flatten_settings(node, category="Test")
        assert "my_float" in result
        assert result["my_float"].label == "My Float"
        assert result["my_float"].category == "Test"

    def test_flatten_settings_with_children(self):
        node = {
            "parent": {
                "type": "category",
                "label": "Parent Cat",
                "children": {
                    "child_bool": {
                        "type": "bool",
                        "label": "Child",
                        "description": "",
                        "default_value": False,
                    },
                },
            },
        }
        result = _flatten_settings(node, category="")
        assert "child_bool" in result
        assert result["child_bool"].category == "Parent Cat"

    def test_flatten_settings_skips_unsupported_types(self):
        node = {
            "poly": {"type": "polygon", "label": "P", "description": ""},
        }
        result = _flatten_settings(node, category="")
        assert len(result) == 0

    def test_apply_overrides(self):
        settings = {
            "test_key": SettingDefinition(
                key="test_key", label="Test", description="",
                setting_type="float", default_value=1.0,
            ),
        }
        _apply_overrides(settings, {"test_key": {"default_value": 2.0}})
        assert settings["test_key"].default_value == 2.0

    def test_apply_overrides_ignores_unknown_keys(self):
        settings = {}
        _apply_overrides(settings, {"unknown": {"default_value": 5}})
        assert len(settings) == 0

    def test_build_indexes(self):
        settings = {
            "layer_height": SettingDefinition(
                key="layer_height", label="Layer Height", description="",
                setting_type="float", default_value=0.2,
            ),
        }
        label_map, norm_map = _build_indexes(settings)
        assert label_map["layer height"] == "layer_height"
        assert norm_map["layer_height"] == "layer_height"


# --- SettingsMatcher tests ---

class TestSettingsMatcher:
    def test_exact_key_match(self, registry):
        key, candidates = resolve_setting(registry,"layer_height")
        assert key == "layer_height"
        assert len(candidates) == 1

    def test_spaces_to_underscores(self, registry):
        key, candidates = resolve_setting(registry,"layer height")
        assert key == "layer_height"

    def test_exact_label_match(self, registry):
        key, candidates = resolve_setting(registry,"Layer Height")
        assert key == "layer_height"

    def test_exact_label_case_insensitive(self, registry):
        key, candidates = resolve_setting(registry,"layer height")
        assert key == "layer_height"

    def test_typo_fuzzy_match(self, registry):
        key, candidates = resolve_setting(registry,"layer_hieght")
        assert len(candidates) > 0
        # Should find layer_height among candidates
        candidate_keys = [c.key for c in candidates]
        assert "layer_height" in candidate_keys

    def test_substring_ambiguous(self, registry):
        key, candidates = resolve_setting(registry,"layer")
        # "layer" is a substring of many settings — should be ambiguous
        assert key is None
        assert len(candidates) > 1

    def test_no_match(self, registry):
        key, candidates = resolve_setting(registry,"xyznotarealkey")
        assert key is None
        assert len(candidates) == 0

    def test_exact_enum_key(self, registry):
        key, candidates = resolve_setting(registry,"adhesion_type")
        assert key == "adhesion_type"
        assert len(candidates) == 1

    def test_label_match_for_support(self, registry):
        key, candidates = resolve_setting(registry,"Generate Support")
        assert key == "support_enable"


# --- Matcher pure function tests ---

class TestMatcherFunctions:
    def test_match_exact_key_found(self):
        settings = {
            "my_key": SettingDefinition(
                key="my_key", label="My Key", description="",
                setting_type="float", default_value=1.0,
            ),
        }
        key, candidates = _match_exact_key(settings, "my_key")
        assert key == "my_key"
        assert len(candidates) == 1

    def test_match_exact_key_not_found(self):
        key, candidates = _match_exact_key({}, "nope")
        assert key is None
        assert candidates == []

    def test_match_substring_single(self):
        settings = {
            "infill_density": SettingDefinition(
                key="infill_density", label="Infill Density", description="",
                setting_type="float", default_value=20,
            ),
        }
        key, candidates = _match_substring(settings, "infill")
        assert key == "infill_density"

    def test_match_substring_ambiguous(self):
        settings = {
            "top_layers": SettingDefinition(
                key="top_layers", label="Top Layers", description="",
                setting_type="int", default_value=4,
            ),
            "bottom_layers": SettingDefinition(
                key="bottom_layers", label="Bottom Layers", description="",
                setting_type="int", default_value=4,
            ),
        }
        key, candidates = _match_substring(settings, "layers")
        assert key is None
        assert len(candidates) == 2


# --- SettingsValidator tests ---

class TestSettingsValidator:
    def test_float_valid(self, registry):
        defn = registry.get("layer_height")
        result = validate(defn, "0.2")
        assert result.ok
        assert result.coerced_value == "0.2"
        assert result.error == ""

    def test_float_invalid_text(self, registry):
        defn = registry.get("layer_height")
        result = validate(defn, "abc")
        assert not result.ok
        assert "number" in result.error.lower()

    def test_float_below_hard_minimum(self, registry):
        defn = registry.get("layer_height")
        result = validate(defn, "0.0001")
        assert not result.ok
        assert "minimum" in result.error.lower()

    def test_float_warning_range(self, registry):
        defn = registry.get("layer_height")
        # 0.02 is below minimum_value_warning of 0.04 but above minimum_value of 0.001
        result = validate(defn, "0.02")
        assert result.ok
        assert result.warning != ""
        assert "recommended" in result.warning.lower()

    def test_int_valid(self, registry):
        defn = registry.get("wall_line_count")
        result = validate(defn, "3")
        assert result.ok
        assert result.coerced_value == "3"

    def test_int_invalid_float(self, registry):
        defn = registry.get("wall_line_count")
        result = validate(defn, "2.5")
        assert not result.ok
        assert "integer" in result.error.lower()

    def test_int_accepts_whole_float(self, registry):
        defn = registry.get("wall_line_count")
        result = validate(defn, "3.0")
        assert result.ok
        assert result.coerced_value == "3"

    def test_bool_true_variants(self, registry):
        defn = registry.get("support_enable")
        for val in ["true", "True", "yes", "1", "on"]:
            result = validate(defn, val)
            assert result.ok, f"Failed for {val}"
            assert result.coerced_value == "true"

    def test_bool_false_variants(self, registry):
        defn = registry.get("support_enable")
        for val in ["false", "False", "no", "0", "off"]:
            result = validate(defn, val)
            assert result.ok, f"Failed for {val}"
            assert result.coerced_value == "false"

    def test_bool_invalid(self, registry):
        defn = registry.get("support_enable")
        result = validate(defn, "maybe")
        assert not result.ok
        assert "true/false" in result.error.lower()

    def test_enum_valid_key(self, registry):
        defn = registry.get("adhesion_type")
        result = validate(defn, "skirt")
        assert result.ok
        assert result.coerced_value == "skirt"

    def test_enum_case_insensitive(self, registry):
        defn = registry.get("adhesion_type")
        result = validate(defn, "Skirt")
        assert result.ok
        assert result.coerced_value == "skirt"

    def test_enum_by_label(self, registry):
        defn = registry.get("adhesion_type")
        # Option labels are "Skirt", "Brim", etc.
        result = validate(defn, "Brim")
        assert result.ok
        assert result.coerced_value == "brim"

    def test_enum_invalid(self, registry):
        defn = registry.get("adhesion_type")
        result = validate(defn, "glue")
        assert not result.ok
        assert "invalid option" in result.error.lower()

    def test_str_accepts_anything(self):
        defn = SettingDefinition(
            key="test_str", label="Test", description="",
            setting_type="str", default_value=""
        )
        result = validate(defn, "anything goes here")
        assert result.ok


# --- Argument parser tests ---

class TestParseSettingsArgs:
    def test_simple_key_value(self):
        pairs = _parse_settings_args("/settings layer_height=0.2")
        assert pairs == [("layer_height", "0.2")]

    def test_multiple_pairs(self):
        pairs = _parse_settings_args("/settings layer_height=0.2 infill_sparse_density=20")
        assert pairs == [("layer_height", "0.2"), ("infill_sparse_density", "20")]

    def test_double_quoted_key(self):
        pairs = _parse_settings_args('/settings "layer height"=0.2')
        assert pairs == [("layer height", "0.2")]

    def test_single_quoted_key(self):
        pairs = _parse_settings_args("/settings 'layer height'=0.2")
        assert pairs == [("layer height", "0.2")]

    def test_empty_args(self):
        pairs = _parse_settings_args("/settings")
        assert pairs == []

    def test_no_equals(self):
        pairs = _parse_settings_args("/settings justtext")
        assert pairs == []

    def test_bot_mention(self):
        pairs = _parse_settings_args("/settings@mybot layer_height=0.2")
        assert pairs == [("layer_height", "0.2")]


# --- Preset tests ---

class TestPresets:
    def test_builtin_presets_exist(self):
        presets = load_presets()
        assert "draft" in presets
        assert "standard" in presets
        assert "fine" in presets
        assert "strong" in presets

    def test_get_preset(self):
        presets = load_presets()
        draft = presets.get("draft")
        assert draft is not None
        assert "settings" in draft
        assert "description" in draft
        assert "layer_height" in draft["settings"]

    def test_get_case_insensitive(self):
        presets = load_presets()
        # load_presets stores keys as lowercase
        assert presets.get("draft") is not None

    def test_get_nonexistent(self):
        presets = load_presets()
        assert presets.get("nonexistent") is None

    def test_preset_settings_are_valid_keys(self, registry):
        presets = load_presets()
        for name, preset in presets.items():
            for key in preset["settings"]:
                assert registry.get(key) is not None, (
                    f"Preset '{name}' has unknown key '{key}'"
                )

    def test_draft_has_higher_layer_height_than_fine(self):
        presets = load_presets()
        draft_lh = float(presets["draft"]["settings"]["layer_height"])
        fine_lh = float(presets["fine"]["settings"]["layer_height"])
        assert draft_lh > fine_lh


# --- Bounds override tests ---

class TestBoundsOverrides:
    def test_retraction_amount_hard_max(self, config):
        """Config sets retraction_amount.maximum_value = 4 for all-metal heat break."""
        defn = config.registry.get("retraction_amount")
        assert defn is not None
        assert defn.maximum_value == 4.0

        # 3mm should pass
        result = validate(defn, "3")
        assert result.ok

        # 5mm should be rejected
        result = validate(defn, "5")
        assert not result.ok
        assert "maximum" in result.error.lower()

        # 4mm should be exactly at the limit
        result = validate(defn, "4")
        assert result.ok


# --- Inline keyboard tests ---

class TestInlineKeyboard:
    def test_bool_value_picker(self, registry):
        defn = registry.get("support_enable")
        keyboard = _build_value_picker(defn)
        assert keyboard is not None
        # Should have one row with True and False buttons
        assert len(keyboard.inline_keyboard) == 1
        labels = [btn.text for btn in keyboard.inline_keyboard[0]]
        assert "True" in labels
        assert "False" in labels

    def test_enum_value_picker(self, registry):
        defn = registry.get("adhesion_type")
        keyboard = _build_value_picker(defn)
        assert keyboard is not None
        # Should have buttons for each option (skirt, brim, raft, none)
        all_buttons = [btn for row in keyboard.inline_keyboard for btn in row]
        assert len(all_buttons) >= 3  # at least skirt, brim, raft

    def test_float_returns_none(self, registry):
        defn = registry.get("layer_height")
        assert _build_value_picker(defn) is None

    def test_int_returns_none(self, registry):
        defn = registry.get("wall_line_count")
        assert _build_value_picker(defn) is None

    def test_callback_data_fits_common_settings(self, registry):
        """Verify val: callback_data for common settings fits in 64 bytes."""
        common_keys = [
            "support_enable", "adhesion_type", "layer_height",
            "infill_sparse_density", "wall_line_count",
            "material_print_temperature", "speed_print",
        ]
        for key in common_keys:
            cb = f"val:{key}:some_value"
            assert len(cb.encode()) <= _MAX_CALLBACK_DATA, (
                f"callback_data too long for {key}: {len(cb.encode())} bytes"
            )

    def test_preset_callback_data_fits(self):
        presets = load_presets()
        for name in presets:
            cb = f"preset:{name}"
            assert len(cb.encode()) <= _MAX_CALLBACK_DATA, (
                f"preset callback_data too long for {name}: {len(cb.encode())} bytes"
            )

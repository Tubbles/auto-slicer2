"""Tests for settings registry, matcher, and validator."""

import configparser
import pytest

from auto_slicer.config import Config
from auto_slicer.settings_registry import SettingsRegistry, SettingDefinition
from auto_slicer.settings_match import SettingsMatcher
from auto_slicer.settings_validate import SettingsValidator, ValidationResult


@pytest.fixture(scope="module")
def config():
    c = configparser.ConfigParser()
    c.read("config.ini")
    return Config(c)


@pytest.fixture(scope="module")
def registry(config):
    return config.registry


@pytest.fixture(scope="module")
def matcher(registry):
    return SettingsMatcher(registry)


@pytest.fixture(scope="module")
def validator():
    return SettingsValidator()


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


# --- SettingsMatcher tests ---

class TestSettingsMatcher:
    def test_exact_key_match(self, matcher):
        key, candidates = matcher.resolve("layer_height")
        assert key == "layer_height"
        assert len(candidates) == 1

    def test_spaces_to_underscores(self, matcher):
        key, candidates = matcher.resolve("layer height")
        assert key == "layer_height"

    def test_exact_label_match(self, matcher):
        key, candidates = matcher.resolve("Layer Height")
        assert key == "layer_height"

    def test_exact_label_case_insensitive(self, matcher):
        key, candidates = matcher.resolve("layer height")
        assert key == "layer_height"

    def test_typo_fuzzy_match(self, matcher):
        key, candidates = matcher.resolve("layer_hieght")
        assert len(candidates) > 0
        # Should find layer_height among candidates
        candidate_keys = [c.key for c in candidates]
        assert "layer_height" in candidate_keys

    def test_substring_ambiguous(self, matcher):
        key, candidates = matcher.resolve("layer")
        # "layer" is a substring of many settings — should be ambiguous
        assert key is None
        assert len(candidates) > 1

    def test_no_match(self, matcher):
        key, candidates = matcher.resolve("xyznotarealkey")
        assert key is None
        assert len(candidates) == 0

    def test_exact_enum_key(self, matcher):
        key, candidates = matcher.resolve("adhesion_type")
        assert key == "adhesion_type"
        assert len(candidates) == 1

    def test_label_match_for_support(self, matcher):
        key, candidates = matcher.resolve("Generate Support")
        assert key == "support_enable"


# --- SettingsValidator tests ---

class TestSettingsValidator:
    def test_float_valid(self, registry, validator):
        defn = registry.get("layer_height")
        result = validator.validate(defn, "0.2")
        assert result.ok
        assert result.coerced_value == "0.2"
        assert result.error == ""

    def test_float_invalid_text(self, registry, validator):
        defn = registry.get("layer_height")
        result = validator.validate(defn, "abc")
        assert not result.ok
        assert "number" in result.error.lower()

    def test_float_below_hard_minimum(self, registry, validator):
        defn = registry.get("layer_height")
        result = validator.validate(defn, "0.0001")
        assert not result.ok
        assert "minimum" in result.error.lower()

    def test_float_warning_range(self, registry, validator):
        defn = registry.get("layer_height")
        # 0.02 is below minimum_value_warning of 0.04 but above minimum_value of 0.001
        result = validator.validate(defn, "0.02")
        assert result.ok
        assert result.warning != ""
        assert "recommended" in result.warning.lower()

    def test_int_valid(self, registry, validator):
        defn = registry.get("wall_line_count")
        result = validator.validate(defn, "3")
        assert result.ok
        assert result.coerced_value == "3"

    def test_int_invalid_float(self, registry, validator):
        defn = registry.get("wall_line_count")
        result = validator.validate(defn, "2.5")
        assert not result.ok
        assert "integer" in result.error.lower()

    def test_int_accepts_whole_float(self, registry, validator):
        defn = registry.get("wall_line_count")
        result = validator.validate(defn, "3.0")
        assert result.ok
        assert result.coerced_value == "3"

    def test_bool_true_variants(self, registry, validator):
        defn = registry.get("support_enable")
        for val in ["true", "True", "yes", "1", "on"]:
            result = validator.validate(defn, val)
            assert result.ok, f"Failed for {val}"
            assert result.coerced_value == "true"

    def test_bool_false_variants(self, registry, validator):
        defn = registry.get("support_enable")
        for val in ["false", "False", "no", "0", "off"]:
            result = validator.validate(defn, val)
            assert result.ok, f"Failed for {val}"
            assert result.coerced_value == "false"

    def test_bool_invalid(self, registry, validator):
        defn = registry.get("support_enable")
        result = validator.validate(defn, "maybe")
        assert not result.ok
        assert "true/false" in result.error.lower()

    def test_enum_valid_key(self, registry, validator):
        defn = registry.get("adhesion_type")
        result = validator.validate(defn, "skirt")
        assert result.ok
        assert result.coerced_value == "skirt"

    def test_enum_case_insensitive(self, registry, validator):
        defn = registry.get("adhesion_type")
        result = validator.validate(defn, "Skirt")
        assert result.ok
        assert result.coerced_value == "skirt"

    def test_enum_by_label(self, registry, validator):
        defn = registry.get("adhesion_type")
        # Option labels are "Skirt", "Brim", etc.
        result = validator.validate(defn, "Brim")
        assert result.ok
        assert result.coerced_value == "brim"

    def test_enum_invalid(self, registry, validator):
        defn = registry.get("adhesion_type")
        result = validator.validate(defn, "glue")
        assert not result.ok
        assert "invalid option" in result.error.lower()

    def test_str_accepts_anything(self, validator):
        defn = SettingDefinition(
            key="test_str", label="Test", description="",
            setting_type="str", default_value=""
        )
        result = validator.validate(defn, "anything goes here")
        assert result.ok

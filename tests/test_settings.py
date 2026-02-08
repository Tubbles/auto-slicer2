"""Tests for settings registry, matcher, and validator."""

import configparser
import pytest

from auto_slicer.config import Config
from auto_slicer.settings_registry import SettingsRegistry, SettingDefinition
from auto_slicer.settings_match import SettingsMatcher


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

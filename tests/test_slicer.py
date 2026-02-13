"""Tests for slicer pure functions."""

from pathlib import Path

from auto_slicer.settings_registry import SettingDefinition, SettingsRegistry, _build_indexes
from auto_slicer.slicer import (
    build_cura_command, expand_gcode_tokens, find_unknown_gcode_tokens,
    merge_settings, resolve_settings,
)


def _make_setting(key, setting_type="float", default_value=0.0, expr=None):
    return SettingDefinition(
        key=key, label=key, description="",
        setting_type=setting_type, default_value=default_value,
        value_expression=expr,
    )


def _make_registry(settings_list):
    settings = {s.key: s for s in settings_list}
    label_map, norm_map = _build_indexes(settings)
    return SettingsRegistry(settings, label_map, norm_map)


class TestMergeSettings:
    def test_overrides_win(self):
        defaults = {"layer_height": "0.2", "speed_print": "60"}
        overrides = {"layer_height": "0.1"}
        result = merge_settings(defaults, overrides)
        assert result["layer_height"] == "0.1"
        assert result["speed_print"] == "60"

    def test_empty_overrides(self):
        defaults = {"a": "1"}
        assert merge_settings(defaults, {}) == {"a": "1"}

    def test_does_not_mutate_defaults(self):
        defaults = {"a": "1"}
        merge_settings(defaults, {"a": "2"})
        assert defaults["a"] == "1"


class TestBuildCuraCommand:
    def test_basic_structure(self):
        cmd = build_cura_command(
            cura_bin=Path("/usr/bin/CuraEngine"),
            def_dir=Path("/defs"),
            printer_def="printer.def.json",
            stl_path=Path("/tmp/model.stl"),
            gcode_path=Path("/tmp/model.gcode"),
            settings={},
        )
        assert cmd[0] == "/usr/bin/CuraEngine"
        assert "slice" in cmd
        assert "-l" in cmd
        assert "/tmp/model.stl" in cmd
        assert "-o" in cmd
        assert "/tmp/model.gcode" in cmd

    def test_settings_as_flags(self):
        cmd = build_cura_command(
            cura_bin=Path("/bin/cura"),
            def_dir=Path("/defs"),
            printer_def="p.def.json",
            stl_path=Path("/tmp/m.stl"),
            gcode_path=Path("/tmp/m.gcode"),
            settings={"layer_height": "0.2", "speed_print": "60"},
        )
        assert "-s" in cmd
        assert "layer_height=0.2" in cmd
        assert "speed_print=60" in cmd

    def test_extruders_dir(self):
        cmd = build_cura_command(
            cura_bin=Path("/bin/cura"),
            def_dir=Path("/resources/definitions"),
            printer_def="p.def.json",
            stl_path=Path("/tmp/m.stl"),
            gcode_path=Path("/tmp/m.gcode"),
            settings={},
        )
        # Should include both definitions and extruders dirs via -d
        d_indices = [i for i, x in enumerate(cmd) if x == "-d"]
        assert len(d_indices) == 2
        assert cmd[d_indices[1] + 1] == "/resources/extruders"


class TestResolveSettings:
    def test_computed_values_included(self):
        reg = _make_registry([
            _make_setting("layer_height", default_value=0.2),
            _make_setting("computed", expr="layer_height * 2"),
        ])
        result = resolve_settings(reg, {}, {})
        assert result["computed"] == "0.4"

    def test_overrides_win_over_computed(self):
        reg = _make_registry([
            _make_setting("layer_height", default_value=0.2),
            _make_setting("computed", expr="layer_height * 2"),
        ])
        result = resolve_settings(reg, {}, {"computed": "99"})
        assert result["computed"] == "99"

    def test_override_propagates_to_dependents(self):
        reg = _make_registry([
            _make_setting("a", default_value=10.0),
            _make_setting("b", expr="a + 5"),
        ])
        result = resolve_settings(reg, {}, {"a": "20"})
        assert result["a"] == "20"
        assert result["b"] == "25.0"

    def test_config_defaults_included(self):
        reg = _make_registry([
            _make_setting("a", default_value=1.0),
            _make_setting("b", expr="a * 3"),
        ])
        result = resolve_settings(reg, {"a": "10"}, {})
        assert result["a"] == "10"
        assert result["b"] == "30.0"

    def test_chained_expressions(self):
        reg = _make_registry([
            _make_setting("x", default_value=2.0),
            _make_setting("y", expr="x + 1"),
            _make_setting("z", expr="y * 2"),
        ])
        result = resolve_settings(reg, {}, {})
        assert result["y"] == "3.0"
        assert result["z"] == "6.0"

    def test_skips_values_matching_default(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
            _make_setting("b", default_value=10.0, expr="a * 2"),
        ])
        # b computes to 10.0 which matches its default — should be omitted
        result = resolve_settings(reg, {}, {})
        assert "b" not in result

    def test_keeps_user_override_even_if_matches_default(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
        ])
        # User explicitly sets a=5.0 (same as default) — should be kept
        result = resolve_settings(reg, {}, {"a": "5.0"})
        assert result["a"] == "5.0"

    def test_config_default_matching_definition_is_dropped(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
        ])
        # config default matches definition — not forced, so should be dropped
        result = resolve_settings(reg, {"a": "5.0"}, {})
        assert "a" not in result

    def test_forced_key_kept_even_if_matches_definition(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
        ])
        # forced key matches definition — should still be sent
        result = resolve_settings(reg, {"a": "5.0"}, {}, forced_keys={"a"})
        assert result["a"] == "5.0"

    def test_gcode_tokens_expanded(self):
        reg = _make_registry([
            _make_setting("material_print_temperature", default_value=0.0),
            _make_setting("material_bed_temperature", default_value=0.0),
            _make_setting("machine_start_gcode", setting_type="str",
                          default_value=""),
        ])
        gcode = "M140 S{material_bed_temperature}\nM104 S{material_print_temperature}"
        result = resolve_settings(
            reg, {"machine_start_gcode": gcode,
                  "material_print_temperature": "220",
                  "material_bed_temperature": "60"}, {},
        )
        assert "M140 S60" in result["machine_start_gcode"]
        assert "M104 S220" in result["machine_start_gcode"]

    def test_gcode_unknown_tokens_preserved(self):
        reg = _make_registry([
            _make_setting("machine_start_gcode", setting_type="str",
                          default_value=""),
        ])
        gcode = "M104 S{unknown_setting}"
        result = resolve_settings(reg, {"machine_start_gcode": gcode}, {})
        assert "{unknown_setting}" in result["machine_start_gcode"]

    def test_gcode_definition_default_pulled_and_expanded(self):
        """Gcode settings not in config/overrides are pulled from the registry
        so their {tokens} get expanded (e.g. machine_end_gcode with {machine_depth})."""
        reg = _make_registry([
            _make_setting("machine_depth", default_value=0.0),
            _make_setting("machine_end_gcode", setting_type="str",
                          default_value="G1 Y{machine_depth} ;Present"),
        ])
        result = resolve_settings(reg, {"machine_depth": "235"}, {})
        assert "machine_end_gcode" in result
        assert "{machine_depth}" not in result["machine_end_gcode"]
        assert "G1 Y235 ;Present" in result["machine_end_gcode"]

    def test_gcode_override_wins_over_definition_default(self):
        """When user provides gcode override, it takes priority over definition default."""
        reg = _make_registry([
            _make_setting("machine_end_gcode", setting_type="str",
                          default_value="M104 S0 ;default end"),
        ])
        result = resolve_settings(reg, {}, {"machine_end_gcode": "G28 ;custom end"})
        assert result["machine_end_gcode"] == "G28 ;custom end"


class TestExpandGcodeTokens:
    def test_replaces_known(self):
        result = expand_gcode_tokens(
            "M104 S{temp}", {"temp": "200"})
        assert result == "M104 S200"

    def test_preserves_unknown(self):
        result = expand_gcode_tokens(
            "M104 S{missing}", {"temp": "200"})
        assert result == "M104 S{missing}"

    def test_multiple_tokens(self):
        result = expand_gcode_tokens(
            "M140 S{bed}\nM104 S{nozzle}",
            {"bed": "60", "nozzle": "200"})
        assert result == "M140 S60\nM104 S200"

    def test_no_tokens(self):
        result = expand_gcode_tokens("G28 ;Home", {"a": "1"})
        assert result == "G28 ;Home"

    def test_empty_string(self):
        assert expand_gcode_tokens("", {"a": "1"}) == ""


class TestFindUnknownGcodeTokens:
    def test_no_gcode_settings(self):
        assert find_unknown_gcode_tokens({"layer_height": "0.2"}) == {}

    def test_all_tokens_resolved(self):
        settings = {"machine_start_gcode": "M104 S200\nG28"}
        assert find_unknown_gcode_tokens(settings) == {}

    def test_unknown_tokens_found(self):
        settings = {"machine_start_gcode": "M104 S{missing_temp}"}
        result = find_unknown_gcode_tokens(settings)
        assert result == {"machine_start_gcode": ["missing_temp"]}

    def test_multiple_unknown(self):
        settings = {"machine_start_gcode": "M140 S{bed}\nM104 S{nozzle}"}
        result = find_unknown_gcode_tokens(settings)
        assert result == {"machine_start_gcode": ["bed", "nozzle"]}

    def test_end_gcode(self):
        settings = {"machine_end_gcode": "M104 S{foo}"}
        result = find_unknown_gcode_tokens(settings)
        assert result == {"machine_end_gcode": ["foo"]}

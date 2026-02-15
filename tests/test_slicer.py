"""Tests for slicer pure functions."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from auto_slicer.config import Config
from auto_slicer.handlers import _find_models_in_zip
from auto_slicer.settings_registry import SettingDefinition, SettingsRegistry, _build_indexes
from auto_slicer.slicer import (
    SCALE_KEYS, _resolve_scale, _try_number,
    build_cura_command, expand_gcode_tokens, extract_stats,
    find_unknown_gcode_tokens, format_duration,
    format_metadata_comments, format_settings_summary, inject_metadata,
    matching_presets, merge_settings, parse_gcode_header,
    patch_gcode_header, resolve_settings, slice_file,
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

    def test_gcode_token_from_registry_default(self):
        """Tokens like {machine_depth} resolve from registry defaults even when
        the setting isn't in config defaults or overrides."""
        reg = _make_registry([
            _make_setting("machine_depth", default_value=235.0),
            _make_setting("machine_end_gcode", setting_type="str",
                          default_value="G1 Y{machine_depth} ;Present"),
        ])
        result = resolve_settings(reg, {}, {})
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

    def test_expression_arithmetic(self):
        result = expand_gcode_tokens(
            "G1 Y{depth - 20}", {"depth": "235"})
        assert result == "G1 Y215"

    def test_expression_with_float(self):
        result = expand_gcode_tokens(
            "G1 Z{height * 2}", {"height": "0.3"})
        assert result == "G1 Z0.6"

    def test_expression_preserves_on_error(self):
        result = expand_gcode_tokens(
            "G1 Y{totally_unknown - 20}", {})
        assert result == "G1 Y{totally_unknown - 20}"

    def test_expression_math_function(self):
        result = expand_gcode_tokens(
            "G1 X{max(a, b)}", {"a": "10", "b": "20"})
        assert result == "G1 X20"


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

    def test_valid_expression_not_flagged(self):
        settings = {"machine_end_gcode": "G1 Y{depth - 20}", "depth": "235"}
        assert find_unknown_gcode_tokens(settings) == {}

    def test_invalid_expression_flagged(self):
        settings = {"machine_end_gcode": "G1 Y{unknown_var - 20}"}
        result = find_unknown_gcode_tokens(settings)
        assert result == {"machine_end_gcode": ["unknown_var - 20"]}


class TestTryNumber:
    def test_int(self):
        assert _try_number("235") == 235
        assert isinstance(_try_number("235"), int)

    def test_float(self):
        assert _try_number("0.3") == 0.3
        assert isinstance(_try_number("0.3"), float)

    def test_whole_float_becomes_int(self):
        assert _try_number("235.0") == 235
        assert isinstance(_try_number("235.0"), int)

    def test_string_passthrough(self):
        assert _try_number("hello") == "hello"


class TestSliceFileArchiveFolder:
    def _make_config(self, archive_dir):
        config = MagicMock(spec=Config)
        config.archive_dir = Path(archive_dir)
        config.registry = _make_registry([_make_setting("layer_height", default_value=0.2)])
        config.defaults = {}
        config.forced_keys = set()
        config.cura_bin = Path("/usr/bin/CuraEngine")
        config.def_dir = Path("/defs")
        config.printer_def = "printer.def.json"
        return config

    @patch("auto_slicer.slicer.generate_thumbnails", return_value=None)
    @patch("auto_slicer.slicer.subprocess.run")
    def test_archive_folder_used_when_provided(self, mock_run, mock_thumbs):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            stl = tmpdir / "model.stl"
            stl.write_text("solid test")
            archive_folder = tmpdir / "zip_name" / "20260213_120000"

            config = self._make_config(tmpdir / "default_archive")

            success, msg, result_path, _ = slice_file(config, stl, {}, archive_folder=archive_folder)

            assert success
            assert result_path == archive_folder
            assert archive_folder.exists()
            # STL goes one level up (parent of archive_folder)
            assert (archive_folder.parent / "model.stl").exists()
            assert not (archive_folder / "model.stl").exists()

    @patch("auto_slicer.slicer.generate_thumbnails", return_value=None)
    @patch("auto_slicer.slicer.subprocess.run")
    def test_default_folder_when_archive_folder_not_provided(self, mock_run, mock_thumbs):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            stl = tmpdir / "model.stl"
            stl.write_text("solid test")
            archive_dir = tmpdir / "archive"

            config = self._make_config(archive_dir)

            success, msg, result_path, _ = slice_file(config, stl, {})

            assert success
            # job_folder is archive/model/timestamp/
            assert result_path.parent.name == "model"
            assert result_path.parent.parent == archive_dir
            # STL goes one level up into archive/model/
            model_dir = result_path.parent
            assert (model_dir / "model.stl").exists()


class TestFindModelsInZip:
    def test_finds_stls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.stl").touch()
            (root / "sub").mkdir()
            (root / "sub" / "b.STL").touch()
            result = _find_models_in_zip(root)
            names = {p.name for p in result}
            assert names == {"a.stl", "b.STL"}

    def test_finds_3mf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model.3mf").touch()
            (root / "sub").mkdir()
            (root / "sub" / "other.3MF").touch()
            result = _find_models_in_zip(root)
            names = {p.name for p in result}
            assert names == {"model.3mf", "other.3MF"}

    def test_finds_mixed_stl_and_3mf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.stl").touch()
            (root / "b.3mf").touch()
            result = _find_models_in_zip(root)
            names = {p.name for p in result}
            assert names == {"a.stl", "b.3mf"}

    def test_skips_macosx(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "good.stl").touch()
            macos = root / "__MACOSX"
            macos.mkdir()
            (macos / "bad.stl").touch()
            result = _find_models_in_zip(root)
            assert len(result) == 1
            assert result[0].name == "good.stl"

    def test_skips_dot_underscore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "good.stl").touch()
            (root / "._hidden.stl").touch()
            result = _find_models_in_zip(root)
            assert len(result) == 1
            assert result[0].name == "good.stl"

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert _find_models_in_zip(Path(tmpdir)) == []


class TestMatchingPresets:
    PRESETS = {
        "draft": {"settings": {"layer_height": "0.3", "speed_print": "80"}},
        "fine": {"settings": {"layer_height": "0.12", "speed_print": "40"}},
        "PETG": {"settings": {"material_print_temperature": "235"}},
    }

    def test_full_match(self):
        overrides = {"layer_height": "0.3", "speed_print": "80"}
        assert matching_presets(overrides, self.PRESETS) == ["draft"]

    def test_superset_matches(self):
        overrides = {"layer_height": "0.3", "speed_print": "80", "infill_sparse_density": "15"}
        assert "draft" in matching_presets(overrides, self.PRESETS)

    def test_partial_no_match(self):
        overrides = {"layer_height": "0.3"}
        assert "draft" not in matching_presets(overrides, self.PRESETS)

    def test_multiple_matches(self):
        overrides = {"layer_height": "0.3", "speed_print": "80", "material_print_temperature": "235"}
        result = matching_presets(overrides, self.PRESETS)
        assert "draft" in result
        assert "PETG" in result

    def test_no_match(self):
        assert matching_presets({"layer_height": "0.15"}, self.PRESETS) == []

    def test_empty_overrides(self):
        assert matching_presets({}, self.PRESETS) == []


class TestFormatSettingsSummary:
    def test_override_lines(self):
        result = format_settings_summary({"layer_height": "0.2", "speed_print": "60"}, {})
        assert "layer_height = 0.2\n" in result
        assert "speed_print = 60\n" in result

    def test_uses_labels_when_registry_provided(self):
        reg = _make_registry([
            SettingDefinition(key="layer_height", label="Layer Height", description="",
                              setting_type="float", default_value=0.2),
            SettingDefinition(key="speed_print", label="Print Speed", description="",
                              setting_type="float", default_value=60.0),
        ])
        result = format_settings_summary(
            {"layer_height": "0.2", "speed_print": "60"}, {}, registry=reg,
        )
        assert "Layer Height = 0.2\n" in result
        assert "Print Speed = 60\n" in result
        assert "layer_height" not in result
        assert "speed_print" not in result

    def test_falls_back_to_key_without_registry(self):
        result = format_settings_summary({"layer_height": "0.2"}, {})
        assert "layer_height = 0.2\n" in result

    def test_preset_lines(self):
        presets = {"PETG": {"settings": {"material_print_temperature": "235"}}}
        result = format_settings_summary({"material_print_temperature": "235"}, presets)
        assert "preset: PETG\n\n" in result

    def test_no_blank_line_without_presets(self):
        result = format_settings_summary({"layer_height": "0.2"}, {})
        assert not result.startswith("\n")

    def test_skips_multiline_values(self):
        assert format_settings_summary({"gcode": "line1\nline2"}, {}) == ""

    def test_empty_overrides(self):
        assert format_settings_summary({}, {}) == ""


class TestFormatMetadataComments:
    def test_override_lines(self):
        result = format_metadata_comments({"layer_height": "0.2", "speed_print": "60"}, {})
        assert "; override: layer_height = 0.2\n" in result
        assert "; override: speed_print = 60\n" in result

    def test_preset_lines(self):
        presets = {"PETG": {"settings": {"material_print_temperature": "235"}}}
        result = format_metadata_comments({"material_print_temperature": "235"}, presets)
        assert "; preset: PETG\n" in result
        assert "; override: material_print_temperature = 235\n" in result

    def test_presets_before_overrides(self):
        presets = {"PETG": {"settings": {"material_print_temperature": "235"}}}
        result = format_metadata_comments({"material_print_temperature": "235"}, presets)
        preset_pos = result.index("; preset:")
        override_pos = result.index("; override:")
        assert preset_pos < override_pos

    def test_overrides_sorted_by_key(self):
        result = format_metadata_comments({"z_key": "1", "a_key": "2"}, {})
        a_pos = result.index("a_key")
        z_pos = result.index("z_key")
        assert a_pos < z_pos

    def test_skips_multiline_values(self):
        result = format_metadata_comments({"gcode": "line1\nline2"}, {})
        assert result == ""

    def test_skips_long_values(self):
        result = format_metadata_comments({"key": "x" * 101}, {})
        assert result == ""

    def test_empty_overrides(self):
        assert format_metadata_comments({}, {}) == ""


class TestInjectMetadata:
    def test_injects_after_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            gcode.write_text("; header comment\nG28 ;Home\nG1 X0\n")
            inject_metadata(gcode, {"layer_height": "0.2"}, {})
            content = gcode.read_text()
            assert "; override: layer_height = 0.2" in content
            # Metadata should be between header and body
            assert content.index("; override:") < content.index("G28")

    def test_no_overrides_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            original = "; header\nG28\n"
            gcode.write_text(original)
            inject_metadata(gcode, {}, {})
            assert gcode.read_text() == original

    def test_preset_and_override_injected(self):
        presets = {"fine": {"settings": {"layer_height": "0.12"}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            gcode.write_text("; generated by CuraEngine\nG28\n")
            inject_metadata(gcode, {"layer_height": "0.12"}, presets)
            content = gcode.read_text()
            assert "; preset: fine" in content
            assert "; override: layer_height = 0.12" in content


SAMPLE_STDERR = (
    "[2026-02-13 14:50:28.851] [info] Gcode header after slicing: ;FLAVOR:Marlin\n"
    ";TIME:2659\n"
    ";Filament used: 1.95583m\n"
    ";Layer height: 0.2\n"
    ";MINX:76.906\n"
    ";MINY:81.321\n"
    ";MINZ:0.2\n"
    ";MAXX:158.087\n"
    ";MAXY:153.687\n"
    ";MAXZ:11\n"
    ";TARGET_MACHINE.NAME:Creality Ender-3\n"
    "[2026-02-13 14:50:28.851] [info] Print time (s): 2659\n"
)

PLACEHOLDER_HEADER = (
    ";FLAVOR:Marlin\n"
    ";TIME:6666\n"
    ";Filament used: 0m\n"
    ";Layer height: 0.2\n"
    ";MINX:2.14748e+06\n"
    ";MINY:2.14748e+06\n"
    ";MINZ:2.14748e+06\n"
    ";MAXX:-2.14748e+06\n"
    ";MAXY:-2.14748e+06\n"
    ";MAXZ:-2.14748e+06\n"
    ";TARGET_MACHINE.NAME:Creality Ender-3\n"
)


class TestParseGcodeHeader:
    def test_parses_time(self):
        header = parse_gcode_header(SAMPLE_STDERR)
        assert header[";TIME"] == "2659"

    def test_parses_filament(self):
        header = parse_gcode_header(SAMPLE_STDERR)
        assert header[";Filament used"] == " 1.95583m"

    def test_parses_bounds(self):
        header = parse_gcode_header(SAMPLE_STDERR)
        assert header[";MINX"] == "76.906"
        assert header[";MAXX"] == "158.087"

    def test_parses_all_keys(self):
        header = parse_gcode_header(SAMPLE_STDERR)
        assert len(header) == 11

    def test_empty_stderr(self):
        assert parse_gcode_header("") == {}

    def test_no_header_block(self):
        assert parse_gcode_header("[info] Slicing done\n") == {}


class TestPatchGcodeHeader:
    def test_replaces_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            gcode.write_text(PLACEHOLDER_HEADER + "\nG28\n")
            header = parse_gcode_header(SAMPLE_STDERR)
            patch_gcode_header(gcode, header)
            content = gcode.read_text()
            assert ";TIME:2659" in content
            assert ";TIME:6666" not in content
            assert ";Filament used: 1.95583m" in content
            assert ";Filament used: 0m" not in content
            assert ";MINX:76.906" in content
            assert "2.14748e+06" not in content

    def test_empty_header_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            original = ";FLAVOR:Marlin\nG28\n"
            gcode.write_text(original)
            patch_gcode_header(gcode, {})
            assert gcode.read_text() == original

    def test_preserves_body(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gcode = Path(tmpdir) / "test.gcode"
            gcode.write_text(PLACEHOLDER_HEADER + "\nG28\nG1 X100\n")
            header = parse_gcode_header(SAMPLE_STDERR)
            patch_gcode_header(gcode, header)
            content = gcode.read_text()
            assert "G28\n" in content
            assert "G1 X100\n" in content


class TestResolveScale:
    def test_defaults_to_100(self):
        assert _resolve_scale({}, {}) == (100.0, 100.0, 100.0)

    def test_master_propagates_to_axes(self):
        assert _resolve_scale({"scale": "150"}, {}) == (150.0, 150.0, 150.0)

    def test_per_axis_override(self):
        sx, sy, sz = _resolve_scale({"scale": "150"}, {"scale_x": "200"})
        assert sx == 200.0
        assert sy == 150.0
        assert sz == 150.0

    def test_user_overrides_win_over_config(self):
        sx, sy, sz = _resolve_scale({"scale": "100"}, {"scale": "200"})
        assert sx == 200.0
        assert sy == 200.0
        assert sz == 200.0

    def test_config_default_scale(self):
        sx, sy, sz = _resolve_scale({"scale": "50"}, {})
        assert sx == 50.0
        assert sy == 50.0
        assert sz == 50.0

    def test_per_axis_without_master(self):
        sx, sy, sz = _resolve_scale({}, {"scale_y": "300"})
        assert sx == 100.0
        assert sy == 300.0
        assert sz == 100.0


class TestScaleKeysStripped:
    def test_scale_keys_absent_from_resolved(self):
        reg = _make_registry([
            _make_setting("layer_height", default_value=0.2),
            _make_setting("scale", default_value=100.0),
            _make_setting("scale_x", default_value=100.0, expr="scale"),
            _make_setting("scale_y", default_value=100.0, expr="scale"),
            _make_setting("scale_z", default_value=100.0, expr="scale"),
        ])
        result = resolve_settings(reg, {"scale": "150"}, {})
        for key in SCALE_KEYS:
            assert key not in result

    def test_scale_keys_absent_even_with_user_override(self):
        reg = _make_registry([
            _make_setting("scale", default_value=100.0),
            _make_setting("scale_x", default_value=100.0, expr="scale"),
            _make_setting("scale_y", default_value=100.0, expr="scale"),
            _make_setting("scale_z", default_value=100.0, expr="scale"),
        ])
        result = resolve_settings(reg, {}, {"scale": "200", "scale_x": "300"})
        for key in SCALE_KEYS:
            assert key not in result


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_duration(65) == "1m 5s"

    def test_hours_minutes_seconds(self):
        assert format_duration(3661) == "1h 1m 1s"

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h 0m 0s"

    def test_exact_minute(self):
        assert format_duration(60) == "1m 0s"


class TestExtractStats:
    def test_normal_header(self):
        header = {";TIME": "2659", ";Filament used": " 1.95583m"}
        result = extract_stats(header)
        assert result == {"time_seconds": 2659, "filament_meters": 1.96}

    def test_missing_time(self):
        header = {";Filament used": " 1.95583m"}
        assert extract_stats(header) == {}

    def test_missing_filament(self):
        header = {";TIME": "2659"}
        assert extract_stats(header) == {}

    def test_empty_header(self):
        assert extract_stats({}) == {}

    def test_integer_filament(self):
        header = {";TIME": "100", ";Filament used": " 3m"}
        result = extract_stats(header)
        assert result == {"time_seconds": 100, "filament_meters": 3.0}


class TestSliceFile3MF:
    def _make_config(self, archive_dir):
        config = MagicMock(spec=Config)
        config.archive_dir = Path(archive_dir)
        config.registry = _make_registry([_make_setting("layer_height", default_value=0.2)])
        config.defaults = {}
        config.forced_keys = set()
        config.cura_bin = Path("/usr/bin/CuraEngine")
        config.def_dir = Path("/defs")
        config.printer_def = "printer.def.json"
        return config

    @patch("auto_slicer.slicer.generate_thumbnails", return_value=None)
    @patch("auto_slicer.slicer.subprocess.run")
    def test_3mf_with_scaling_skips_scale_stl(self, mock_run, mock_thumbs):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            model = tmpdir / "model.3mf"
            model.write_bytes(b"fake 3mf data")
            # inject_metadata reads gcode, so create a fake one
            (tmpdir / "model.gcode").write_text("; header\nG28\n")
            config = self._make_config(tmpdir / "archive")

            with patch("auto_slicer.slicer.scale_stl") as mock_scale:
                success, msg, _, _ = slice_file(config, model, {"scale": "200"})

            assert success
            mock_scale.assert_not_called()
            assert "Scaling skipped" in msg
            assert ".3mf" in msg

    @patch("auto_slicer.slicer.generate_thumbnails", return_value=None)
    @patch("auto_slicer.slicer.subprocess.run")
    def test_3mf_without_scaling_works_normally(self, mock_run, mock_thumbs):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            model = tmpdir / "model.3mf"
            model.write_bytes(b"fake 3mf data")
            config = self._make_config(tmpdir / "archive")

            success, msg, _, _ = slice_file(config, model, {})

            assert success
            assert "Scaling skipped" not in msg

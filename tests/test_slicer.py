"""Tests for slicer pure functions."""

from pathlib import Path

from auto_slicer.slicer import build_cura_command, merge_settings


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

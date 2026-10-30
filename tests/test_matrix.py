"""Tests for the GitHub slicing matrix: composition, options and folder names."""

from auto_slicer.defaults import PRESETS
from auto_slicer.matrix import (
    DEFAULT_SELECTION, DIMENSIONS, all_selections, cell_prefix, compose_overrides,
    folder_name, full_selection, options, rule_prefix, selection_error,
)


class TestComposeOverrides:
    def test_defaults(self):
        overrides = compose_overrides({})
        assert overrides["support_enable"] == "false"
        assert overrides["adhesion_type"] == "skirt"
        assert overrides["layer_height"] == PRESETS["standard"]["settings"]["layer_height"]
        assert overrides["material_print_temperature"] == "220"

    def test_material_speed_beats_quality(self):
        overrides = compose_overrides({"material": "TPU", "quality": "Draft"})
        assert overrides["speed_print"] == "25"
        assert overrides["layer_height"] == "0.3"

    def test_petg_speed_beats_fine(self):
        overrides = compose_overrides({"material": "PETG", "quality": "Fine"})
        assert overrides["speed_print"] == "45"
        assert overrides["layer_height"] == "0.12"

    def test_vase_turns_off_support(self):
        overrides = compose_overrides({"support": "Vase"})
        assert overrides["magic_spiralize"] == "true"
        assert overrides["support_enable"] == "false"

    def test_support_and_brim(self):
        overrides = compose_overrides({"support": "Support", "adhesion": "Brim"})
        assert overrides["support_enable"] == "true"
        assert overrides["adhesion_type"] == "brim"

    def test_tpu_retraction(self):
        overrides = compose_overrides({"material": "TPU"})
        assert overrides["retraction_amount"] == "3"
        assert overrides["retraction_speed"] == "20"

    def test_does_not_mutate_presets(self):
        compose_overrides({"support": "Vase"})
        assert "support_enable" not in PRESETS["Vase"]["settings"]


class TestSelection:
    def test_full_selection_fills_defaults(self):
        assert full_selection({"material": "PETG"}) == {**DEFAULT_SELECTION, "material": "PETG"}

    def test_valid(self):
        assert selection_error({"material": "TPU", "quality": "Strong"}) is None

    def test_unknown_dimension(self):
        assert "dimension" in selection_error({"colour": "red"})

    def test_unknown_option(self):
        assert "material" in selection_error({"material": "ABS"})

    def test_non_string_option(self):
        assert selection_error({"material": ["PLA"]}) is not None


class TestOptions:
    def test_without_rules(self):
        result = options(None)
        assert result["matrix"] is True
        assert result["rules"] == {}
        assert [d["name"] for d in result["dimensions"]] == ["material", "support", "adhesion", "quality"]

    def test_dimension_entries(self):
        material = options(None)["dimensions"][0]
        assert material == {"name": "material", "options": ["PLA", "PETG", "TPU"], "default": "PLA"}

    def test_with_rules(self):
        rules = {"matrix": False, "rules": {"lid": {"description": "", "settings": {"layer_height": "0.1"}}}}
        result = options(rules)
        assert result["matrix"] is False
        assert result["rules"] == rules["rules"]


class TestFolderNames:
    def test_default_cell(self):
        assert cell_prefix({}) == "PLA_No-support_Skirt_Standard"

    def test_folder_name(self):
        assert folder_name(cell_prefix({}), "20261030_141500") == "PLA_No-support_Skirt_Standard_20261030_141500"

    def test_cell_order_is_display_order(self):
        selection = {"quality": "Fine", "adhesion": "Brim", "support": "Vase", "material": "TPU"}
        assert cell_prefix(selection) == "TPU_Vase_Brim_Fine"

    def test_rule_prefix_sanitized(self):
        assert rule_prefix("lid / thin") == "lid-thin"

    def test_all_selections_unique_prefixes(self):
        cells = all_selections()
        expected = 1
        for dimension in DIMENSIONS.values():
            expected *= len(dimension)
        assert len(cells) == expected
        assert len({cell_prefix(cell) for cell in cells}) == expected

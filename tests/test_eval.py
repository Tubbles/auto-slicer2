"""Tests for the expression evaluator."""

import configparser
from pathlib import Path

import pytest

from auto_slicer.config import load_config
from auto_slicer.settings_registry import (
    SettingDefinition, SettingsRegistry, _build_indexes,
)
from auto_slicer.settings_eval import (
    extract_deps, build_dep_graph, build_reverse_deps,
    topological_order, evaluate_expressions, EvalResult, _coerce,
)


# --- Helpers ---

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


# --- extract_deps ---

class TestExtractDeps:
    def test_simple_name(self):
        assert extract_deps("layer_height") == {"layer_height"}

    def test_arithmetic(self):
        deps = extract_deps("wall_thickness / line_width + 1")
        assert deps == {"wall_thickness", "line_width"}

    def test_ternary(self):
        deps = extract_deps("1 if magic_spiralize else max(1, wall_line_count)")
        assert deps == {"magic_spiralize", "wall_line_count"}

    def test_math_module(self):
        deps = extract_deps("math.ceil(bottom_thickness / layer_height)")
        assert deps == {"bottom_thickness", "layer_height"}

    def test_resolve_or_value(self):
        deps = extract_deps("resolveOrValue('layer_height')")
        assert "layer_height" in deps

    def test_extruder_value(self):
        deps = extract_deps("extruderValue(support_roof_extruder_nr, 'support_interface_line_width')")
        assert "support_interface_line_width" in deps
        assert "support_roof_extruder_nr" in deps

    def test_extruder_values(self):
        deps = extract_deps("sum(extruderValues('machine_extruder_start_pos_x'))")
        assert "machine_extruder_start_pos_x" in deps

    def test_ignores_builtins(self):
        deps = extract_deps("max(1, round(x))")
        assert deps == {"x"}
        assert "max" not in deps
        assert "round" not in deps

    def test_ignores_math(self):
        deps = extract_deps("math.pi")
        assert "math" not in deps

    def test_syntax_error_returns_empty(self):
        assert extract_deps("this is not python %%%") == set()

    def test_constant_expression(self):
        assert extract_deps("100") == set()

    def test_string_literal(self):
        assert extract_deps("'hello'") == set()

    def test_complex_cura_expression(self):
        expr = "math.ceil(round(bottom_thickness / resolveOrValue('layer_height'), 4))"
        deps = extract_deps(expr)
        assert "bottom_thickness" in deps
        assert "layer_height" in deps


# --- build_dep_graph ---

class TestBuildDepGraph:
    def test_basic(self):
        reg = _make_registry([
            _make_setting("a", expr="b + c"),
            _make_setting("b"),
            _make_setting("c"),
        ])
        graph = build_dep_graph(reg)
        assert graph == {"a": {"b", "c"}}

    def test_no_expressions(self):
        reg = _make_registry([_make_setting("a"), _make_setting("b")])
        assert build_dep_graph(reg) == {}

    def test_filters_nonexistent_deps(self):
        reg = _make_registry([
            _make_setting("a", expr="b + nonexistent"),
            _make_setting("b"),
        ])
        graph = build_dep_graph(reg)
        assert graph == {"a": {"b"}}


# --- build_reverse_deps ---

class TestBuildReverseDeps:
    def test_basic(self):
        graph = {"a": {"b", "c"}, "d": {"b"}}
        reverse = build_reverse_deps(graph)
        assert reverse == {"b": {"a", "d"}, "c": {"a"}}

    def test_empty(self):
        assert build_reverse_deps({}) == {}


# --- topological_order ---

class TestTopologicalOrder:
    def test_simple_chain(self):
        graph = {"c": {"b"}, "b": {"a"}, "a": set()}
        order = topological_order(graph)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_independent_nodes(self):
        graph = {"a": set(), "b": set(), "c": set()}
        order = topological_order(graph)
        assert set(order) == {"a", "b", "c"}

    def test_diamond(self):
        graph = {"d": {"b", "c"}, "b": {"a"}, "c": {"a"}, "a": set()}
        order = topological_order(graph)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_included(self):
        graph = {"a": {"b"}, "b": {"a"}}
        order = topological_order(graph)
        assert set(order) == {"a", "b"}


# --- _coerce ---

class TestCoerce:
    def test_int_rounds(self):
        assert _coerce(3.7, "int") == 4
        assert _coerce(3.2, "int") == 3

    def test_float(self):
        assert _coerce(3, "float") == 3.0

    def test_bool(self):
        assert _coerce(1, "bool") is True
        assert _coerce(0, "bool") is False

    def test_str(self):
        assert _coerce(42, "str") == "42"

    def test_fallback(self):
        assert _coerce("not_a_num", "int") == "not_a_num"


# --- evaluate_expressions ---

class TestEvaluateExpressions:
    def test_simple_arithmetic(self):
        reg = _make_registry([
            _make_setting("a", default_value=10.0),
            _make_setting("b", expr="a * 2"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["b"] == 20.0

    def test_chained_deps(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
            _make_setting("b", expr="a + 1"),
            _make_setting("c", expr="b * 2"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["b"] == 6.0
        assert result.values["c"] == 12.0

    def test_pinned_skips_eval(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
            _make_setting("b", expr="a + 1"),
        ])
        result = evaluate_expressions(reg, {"b": "99"}, {})
        assert "b" not in result.values  # pinned = not in computed
        assert result.errors == {}

    def test_pinned_affects_dependents(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
            _make_setting("b", expr="a * 2"),
        ])
        result = evaluate_expressions(reg, {"a": "10"}, {})
        assert result.values["b"] == 20.0

    def test_config_defaults_used(self):
        reg = _make_registry([
            _make_setting("a", default_value=5.0),
            _make_setting("b", expr="a + 1"),
        ])
        result = evaluate_expressions(reg, {}, {"a": "20"})
        assert result.values["b"] == 21.0

    def test_ternary_expression(self):
        reg = _make_registry([
            _make_setting("flag", setting_type="bool", default_value=True),
            _make_setting("result", setting_type="int", default_value=0, expr="10 if flag else 20"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["result"] == 10

    def test_math_functions(self):
        reg = _make_registry([
            _make_setting("a", default_value=1.5),
            _make_setting("b", setting_type="int", default_value=0, expr="math.ceil(a)"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["b"] == 2

    def test_resolve_or_value(self):
        reg = _make_registry([
            _make_setting("layer_height", default_value=0.2),
            _make_setting("computed", expr="resolveOrValue('layer_height') * 3"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert abs(result.values["computed"] - 0.6) < 0.001

    def test_extruder_value(self):
        reg = _make_registry([
            _make_setting("src", default_value=42.0),
            _make_setting("nr", setting_type="int", default_value=0),
            _make_setting("dest", expr="extruderValue(nr, 'src')"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["dest"] == 42.0

    def test_extruder_values(self):
        reg = _make_registry([
            _make_setting("x", default_value=5.0),
            _make_setting("result", expr="sum(extruderValues('x'))"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["result"] == 5.0

    def test_type_coercion_int(self):
        reg = _make_registry([
            _make_setting("a", default_value=3.0),
            _make_setting("b", setting_type="int", default_value=0, expr="a + 0.7"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert result.values["b"] == 4  # rounded
        assert isinstance(result.values["b"], int)

    def test_eval_error_captured(self):
        reg = _make_registry([
            _make_setting("bad", expr="1 / 0"),
        ])
        result = evaluate_expressions(reg, {}, {})
        assert "bad" in result.errors
        assert "bad" not in result.values

    def test_no_expressions_returns_empty(self):
        reg = _make_registry([_make_setting("a", default_value=1.0)])
        result = evaluate_expressions(reg, {}, {})
        assert result.values == {}
        assert result.errors == {}


# --- Integration test with real Cura definitions ---

@pytest.fixture(scope="module")
def real_registry():
    c = configparser.ConfigParser()
    c.read("config.ini")
    config = load_config(c)
    return config.registry


class TestRealCuraExpressions:
    def test_bottom_layers_computed(self, real_registry):
        result = evaluate_expressions(real_registry, {}, {})
        assert "bottom_layers" in result.values
        # Ender 3: layer_height=0.1, bottom_thickness→top_bottom_thickness=0.6 → 6 layers
        assert result.values["bottom_layers"] == 6

    def test_layer_height_change_propagates(self, real_registry):
        result = evaluate_expressions(real_registry, {"layer_height": "0.3"}, {})
        # top_bottom_thickness = layer_height_0 + layer_height * 3
        # layer_height_0 defaults to layer_height (0.3), so 0.3 + 0.3*3 = 1.2
        # bottom_layers = ceil(1.2 / 0.3) = 4
        assert result.values["bottom_layers"] == 4

    def test_line_width_follows_nozzle(self, real_registry):
        result = evaluate_expressions(real_registry, {}, {})
        nozzle = real_registry.get("machine_nozzle_size")
        if "line_width" in result.values:
            assert result.values["line_width"] == nozzle.default_value

    def test_many_expressions_evaluate(self, real_registry):
        result = evaluate_expressions(real_registry, {}, {})
        total_expr = sum(
            1 for d in real_registry.all_settings().values()
            if d.value_expression is not None
        )
        evaluated = len(result.values) + len(result.errors)
        # Most expressions should evaluate successfully
        assert evaluated > 0
        success_rate = len(result.values) / total_expr
        assert success_rate > 0.8, f"Only {success_rate:.0%} of expressions evaluated"

    def test_pinned_setting_not_in_computed(self, real_registry):
        result = evaluate_expressions(real_registry, {"bottom_layers": "10"}, {})
        assert "bottom_layers" not in result.values

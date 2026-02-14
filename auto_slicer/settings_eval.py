"""Expression evaluator for Cura setting value expressions.

Evaluates Python expressions from Cura's definition files using restricted
eval(). CuraEngine does not evaluate expressions — it only receives flat
-s key=value flags. All expression evaluation is our responsibility.
"""

import ast
import math
from dataclasses import dataclass, field

from .settings_registry import SettingsRegistry


@dataclass
class EvalResult:
    values: dict[str, object] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


# Python builtins allowed in expressions
_SAFE_BUILTINS = {
    "max": max,
    "min": min,
    "round": round,
    "int": int,
    "float": float,
    "bool": bool,
    "str": str,
    "len": len,
    "sum": sum,
    "map": map,
    "abs": abs,
    "any": any,
    "all": all,
    "True": True,
    "False": False,
}


def extract_deps(expr: str) -> set[str]:
    """Extract setting key dependencies from a value expression.

    Collects bare Name references and string arguments to Cura helper
    functions (resolveOrValue, extruderValue, extruderValues).
    """
    deps = set()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return deps

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _SAFE_BUILTINS and node.id != "math":
            deps.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname == "resolveOrValue" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    deps.add(arg.value)
            elif fname == "extruderValue" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    deps.add(arg.value)
                # First arg may be a setting name
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id not in _SAFE_BUILTINS:
                    deps.add(first.id)
            elif fname == "extruderValues" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    deps.add(arg.value)

    return deps


def build_dep_graph(registry: SettingsRegistry) -> dict[str, set[str]]:
    """Map each setting with a value_expression to its dependency keys."""
    graph = {}
    all_keys = registry.keys()
    for key, defn in registry.all_settings().items():
        if defn.value_expression is None:
            continue
        deps = extract_deps(defn.value_expression)
        # Only keep deps that are actual setting keys
        graph[key] = deps & all_keys
    return graph


def build_reverse_deps(dep_graph: dict[str, set[str]]) -> dict[str, set[str]]:
    """Invert the dependency graph: key -> set of keys that depend on it."""
    reverse = {}
    for key, deps in dep_graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(key)
    return reverse


def topological_order(dep_graph: dict[str, set[str]]) -> list[str]:
    """Topological sort via Kahn's algorithm. Dependencies come first."""
    # in_degree counts how many deps each node has (within the graph)
    in_degree = {k: 0 for k in dep_graph}
    for key, deps in dep_graph.items():
        for dep in deps:
            if dep in in_degree:
                pass  # dep is also a computed setting
        in_degree[key] = len(deps & set(dep_graph.keys()))

    # Build adjacency: dep -> list of dependents
    adj: dict[str, list[str]] = {k: [] for k in dep_graph}
    for key, deps in dep_graph.items():
        for dep in deps:
            if dep in adj:
                adj[dep].append(key)

    queue = [k for k, d in in_degree.items() if d == 0]
    order = []

    while queue:
        node = queue.pop(0)
        order.append(node)
        for dependent in adj.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Any remaining nodes are in cycles — append them anyway
    for k in dep_graph:
        if k not in order:
            order.append(k)

    return order


def _coerce(value: object, setting_type: str) -> object:
    """Coerce an eval result to the expected setting type."""
    try:
        if setting_type == "int":
            return int(round(float(value)))
        elif setting_type == "float":
            return float(value)
        elif setting_type == "bool":
            return bool(value)
        elif setting_type == "str":
            return str(value)
    except (ValueError, TypeError):
        pass
    return value


def evaluate_expressions(
    registry: SettingsRegistry,
    pinned_values: dict[str, str],
    config_defaults: dict[str, str],
) -> EvalResult:
    """Evaluate all value expressions, respecting pinned (user) overrides.

    Priority: pinned_values > config_defaults > evaluated expression > default_value.
    Pinned settings skip evaluation entirely.
    """
    result = EvalResult()

    # Start with all default values
    namespace: dict[str, object] = {}
    for key, defn in registry.all_settings().items():
        namespace[key] = defn.default_value

    # Apply config defaults (these are strings — coerce to native types)
    for key, val in config_defaults.items():
        defn = registry.get(key)
        if defn:
            namespace[key] = _coerce(val, defn.setting_type)

    # Apply pinned values (user overrides)
    pinned_keys = set()
    for key, val in pinned_values.items():
        defn = registry.get(key)
        if defn:
            namespace[key] = _coerce(val, defn.setting_type)
            pinned_keys.add(key)

    # Build dep graph and evaluation order
    dep_graph = build_dep_graph(registry)
    order = topological_order(dep_graph)

    # Cura helper functions (single-extruder simplification)
    def resolveOrValue(key):
        return namespace.get(key)

    def extruderValue(_n, key):
        return namespace.get(key)

    def extruderValues(key):
        val = namespace.get(key)
        return [val] if val is not None else []

    eval_globals = {"__builtins__": {}, "math": math}
    eval_globals.update(_SAFE_BUILTINS)
    eval_globals["resolveOrValue"] = resolveOrValue
    eval_globals["extruderValue"] = extruderValue
    eval_globals["extruderValues"] = extruderValues

    # Evaluate expressions in topological order
    for key in order:
        if key in pinned_keys:
            continue

        defn = registry.get(key)
        if not defn or not defn.value_expression:
            continue

        local_ns = dict(namespace)
        try:
            raw = eval(defn.value_expression, eval_globals, local_ns)  # noqa: S307
            coerced = _coerce(raw, defn.setting_type)
            namespace[key] = coerced
        except Exception as e:
            result.errors[key] = str(e)

    # Collect all computed values (anything that had a value_expression and wasn't pinned)
    for key in dep_graph:
        if key not in pinned_keys and key not in result.errors:
            result.values[key] = namespace[key]

    return result

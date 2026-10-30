"""Slicing matrix for GitHub models: four dimensions whose options map to settings overrides."""

import itertools

from .defaults import PRESETS
from .slicer import _sanitize_preset_name


def _preset(name: str) -> dict[str, str]:
    """Copy a built-in preset's settings."""
    return dict(PRESETS[name]["settings"])


# Display order, also the order of the labels in an archive folder name
DIMENSIONS: dict[str, dict[str, dict[str, str]]] = {
    "material": {"PLA": _preset("PLA"), "PETG": _preset("PETG"), "TPU": _preset("TPU")},
    "support": {
        "No support": {"support_enable": "false"},
        "Support": {"support_enable": "true"},
        "Vase": {**_preset("Vase"), "support_enable": "false"},
    },
    "adhesion": {"Skirt": {"adhesion_type": "skirt"}, "Brim": {"adhesion_type": "brim"}},
    "quality": {
        "Draft": _preset("draft"), "Standard": _preset("standard"),
        "Fine": _preset("fine"), "Strong": _preset("strong"),
    },
}

DEFAULT_SELECTION = {"material": "PLA", "support": "No support", "adhesion": "Skirt", "quality": "Standard"}

# Later wins: a material's speed_print beats the quality's, so TPU stays slow
MERGE_ORDER = ("quality", "material", "support", "adhesion")


def full_selection(selection: dict[str, str]) -> dict[str, str]:
    """Fill the dimensions a selection leaves out with their defaults."""
    return {**DEFAULT_SELECTION, **selection}


def selection_error(selection: dict) -> str | None:
    """Return why a selection is invalid, or None when every dimension and option exists."""
    for dimension, option in selection.items():
        if dimension not in DIMENSIONS:
            return f"Unknown dimension: {dimension}"
        if not isinstance(option, str) or option not in DIMENSIONS[dimension]:
            return f"Unknown {dimension}: {option}"
    return None


def compose_overrides(selection: dict[str, str]) -> dict[str, str]:
    """Merge the selected options' settings in MERGE_ORDER."""
    chosen = full_selection(selection)
    overrides: dict[str, str] = {}
    for dimension in MERGE_ORDER:
        overrides.update(DIMENSIONS[dimension][chosen[dimension]])
    return overrides


def _dimension_entry(name: str) -> dict:
    """One dimension's options and default, for a UI."""
    return {"name": name, "options": list(DIMENSIONS[name]), "default": DEFAULT_SELECTION[name]}


def options(project_rules: dict | None) -> dict:
    """The matrix dimensions plus a project's rules (named presets) and whether its matrix is on."""
    rules = project_rules or {}
    return {
        "dimensions": [_dimension_entry(name) for name in DIMENSIONS],
        "rules": rules.get("rules", {}),
        "matrix": rules.get("matrix", True),
    }


def all_selections() -> list[dict[str, str]]:
    """Every cell of the matrix."""
    return [dict(zip(DIMENSIONS, combo)) for combo in itertools.product(*DIMENSIONS.values())]


def cell_prefix(selection: dict[str, str]) -> str:
    """Archive folder prefix of a cell, e.g. PLA_No-support_Skirt_Standard."""
    chosen = full_selection(selection)
    return "_".join(_sanitize_preset_name(chosen[dimension]) for dimension in DIMENSIONS)


def rule_prefix(rule: str) -> str:
    """Archive folder prefix of a rule."""
    return _sanitize_preset_name(rule)


def folder_name(prefix: str, timestamp: str) -> str:
    """Archive folder name of one slice, e.g. PLA_No-support_Skirt_Standard_20261030_141500."""
    return f"{prefix}_{timestamp}"

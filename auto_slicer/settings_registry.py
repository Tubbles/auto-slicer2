import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SettingDefinition:
    key: str
    label: str
    description: str
    setting_type: str  # float, int, bool, enum, str, ...
    default_value: object
    unit: str = ""
    minimum_value: float | None = None
    maximum_value: float | None = None
    minimum_value_warning: float | None = None
    maximum_value_warning: float | None = None
    options: dict[str, str] = field(default_factory=dict)  # enum key→label
    category: str = ""


@dataclass
class SettingsRegistry:
    settings: dict[str, SettingDefinition]
    label_to_key_map: dict[str, str]    # lowercase label → key
    normalized_key_map: dict[str, str]   # normalized key → key

    def get(self, key: str) -> SettingDefinition | None:
        return self.settings.get(key)

    def all_settings(self) -> dict[str, SettingDefinition]:
        return self.settings

    def label_to_key(self) -> dict[str, str]:
        return self.label_to_key_map

    def keys(self) -> set[str]:
        return set(self.settings.keys())


def _try_parse_number(value) -> float | None:
    """Parse a numeric bound, returning None for expressions or missing values."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_def(def_dir: Path, name: str) -> dict:
    """Read and parse a Cura definition JSON file."""
    path = def_dir / f"{name}.def.json"
    with open(path) as f:
        return json.load(f)


def _resolve_chain(def_dir: Path, printer_definition: str) -> list[str]:
    """Walk the inherits chain from printer_definition up to the root."""
    chain = []
    name = printer_definition.removesuffix(".def.json")
    while name:
        chain.append(name)
        data = _read_def(def_dir, name)
        name = data.get("inherits")
    chain.reverse()
    return chain


def _make_setting(key: str, value: dict, category: str) -> SettingDefinition | None:
    """Create a SettingDefinition from a JSON node, or None if not a leaf setting."""
    setting_type = value.get("type", "")
    if setting_type not in ("float", "int", "bool", "enum", "str"):
        return None
    return SettingDefinition(
        key=key,
        label=value.get("label", key),
        description=value.get("description", ""),
        setting_type=setting_type,
        default_value=value.get("default_value"),
        unit=value.get("unit", ""),
        minimum_value=_try_parse_number(value.get("minimum_value")),
        maximum_value=_try_parse_number(value.get("maximum_value")),
        minimum_value_warning=_try_parse_number(value.get("minimum_value_warning")),
        maximum_value_warning=_try_parse_number(value.get("maximum_value_warning")),
        options=value.get("options", {}),
        category=category,
    )


def _flatten_settings(node: dict, category: str) -> dict[str, SettingDefinition]:
    """Recursively flatten a nested settings tree into a flat dict."""
    result = {}
    for key, value in node.items():
        setting_type = value.get("type", "")
        current_category = category

        if setting_type == "category":
            current_category = value.get("label", key)

        defn = _make_setting(key, value, current_category)
        if defn:
            result[key] = defn

        if "children" in value:
            result.update(_flatten_settings(value["children"], current_category))

    return result


def _apply_overrides(settings: dict[str, SettingDefinition], overrides: dict) -> None:
    """Apply overrides from an inheriting definition (mutates settings in place)."""
    for key, override in overrides.items():
        if key not in settings:
            continue
        defn = settings[key]
        if "default_value" in override:
            defn.default_value = override["default_value"]
        if "minimum_value" in override:
            defn.minimum_value = _try_parse_number(override["minimum_value"])
        if "maximum_value" in override:
            defn.maximum_value = _try_parse_number(override["maximum_value"])
        if "minimum_value_warning" in override:
            defn.minimum_value_warning = _try_parse_number(override["minimum_value_warning"])
        if "maximum_value_warning" in override:
            defn.maximum_value_warning = _try_parse_number(override["maximum_value_warning"])


def _build_indexes(settings: dict[str, SettingDefinition]) -> tuple[dict[str, str], dict[str, str]]:
    """Build label-to-key and normalized-key-to-key lookup indexes."""
    label_map = {}
    normalized_map = {}
    for key, defn in settings.items():
        label_map[defn.label.lower()] = key
        normalized_map[key.lower().replace(" ", "_")] = key
    return label_map, normalized_map


def load_registry(definition_dir: Path, printer_definition: str) -> SettingsRegistry:
    """Load Cura definitions and build a SettingsRegistry."""
    chain = _resolve_chain(definition_dir, printer_definition)

    # Base definition has all settings
    base_data = _read_def(definition_dir, chain[0])
    settings = _flatten_settings(base_data.get("settings", {}), category="")

    # Apply overrides from each child in the chain
    for name in chain[1:]:
        data = _read_def(definition_dir, name)
        _apply_overrides(settings, data.get("overrides", {}))

    label_map, normalized_map = _build_indexes(settings)
    return SettingsRegistry(settings, label_map, normalized_map)

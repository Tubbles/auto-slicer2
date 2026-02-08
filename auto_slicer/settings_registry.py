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
            # Expression-based bound (e.g. "0.8 * min(extruderValues(...))")
            return None
    return None


class SettingsRegistry:
    def __init__(self, definition_dir: Path, printer_definition: str):
        self._settings: dict[str, SettingDefinition] = {}
        # Indexes for fast lookup
        self._label_to_key: dict[str, str] = {}  # lowercase label → key
        self._normalized_key_to_key: dict[str, str] = {}  # spaces→underscores, lowercase

        self._def_dir = definition_dir
        self._load(printer_definition)

    def _load(self, printer_definition: str) -> None:
        # Build the inheritance chain: [fdmprinter, ..., printer_definition]
        chain = self._resolve_chain(printer_definition)

        # The first entry in the chain is the base (fdmprinter) with all settings
        base_name = chain[0]
        base_data = self._read_def(base_name)
        self._flatten_settings(base_data.get("settings", {}), category="")

        # Apply overrides from each child in the chain
        for name in chain[1:]:
            data = self._read_def(name)
            self._apply_overrides(data.get("overrides", {}))

        # Build lookup indexes
        for key, defn in self._settings.items():
            self._label_to_key[defn.label.lower()] = key
            self._normalized_key_to_key[key.lower().replace(" ", "_")] = key

    def _resolve_chain(self, printer_definition: str) -> list[str]:
        """Walk the inherits chain from printer_definition up to the root."""
        chain = []
        name = printer_definition.removesuffix(".def.json")
        while name:
            chain.append(name)
            data = self._read_def(name)
            name = data.get("inherits")
        chain.reverse()
        return chain

    def _read_def(self, name: str) -> dict:
        path = self._def_dir / f"{name}.def.json"
        with open(path) as f:
            return json.load(f)

    def _flatten_settings(self, node: dict, category: str) -> None:
        """Recursively flatten the nested settings tree into self._settings."""
        for key, value in node.items():
            setting_type = value.get("type", "")
            current_category = category

            if setting_type == "category":
                current_category = value.get("label", key)
            elif setting_type in ("float", "int", "bool", "enum", "str"):
                defn = SettingDefinition(
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
                    category=current_category,
                )
                self._settings[key] = defn

            # Recurse into children (both categories and parent settings can have them)
            if "children" in value:
                self._flatten_settings(
                    value["children"],
                    category=current_category,
                )

    def _apply_overrides(self, overrides: dict) -> None:
        """Apply overrides from an inheriting definition."""
        for key, override in overrides.items():
            if key not in self._settings:
                continue
            defn = self._settings[key]
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

    def get(self, key: str) -> SettingDefinition | None:
        return self._settings.get(key)

    def all_settings(self) -> dict[str, SettingDefinition]:
        return self._settings

    def label_to_key(self) -> dict[str, str]:
        return self._label_to_key

    def keys(self) -> set[str]:
        return set(self._settings.keys())

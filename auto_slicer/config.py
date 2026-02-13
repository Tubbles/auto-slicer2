import os
from dataclasses import dataclass, field
from pathlib import Path

from .defaults import (
    SETTINGS, extract_bounds_overrides, extract_defaults,
    extract_expression_overrides, extract_forced_keys,
)
from .settings_registry import SettingsRegistry, load_registry


RELOAD_CHAT_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / ".reload_chat_id"


@dataclass
class Config:
    archive_dir: Path
    cura_bin: Path
    def_dir: Path
    printer_def: str
    defaults: dict[str, str]
    telegram_token: str
    allowed_users: set[int]
    notify_chat_id: int | None
    registry: SettingsRegistry
    forced_keys: set[str] = field(default_factory=set)
    api_port: int = 0
    webapp_url: str = ""
    api_base_url: str = ""


def _parse_allowed_users(raw: str) -> set[int]:
    """Parse comma-separated user IDs into a set."""
    return set(int(x) for x in raw.split(",") if x.strip())


BOUNDS_FIELD_NAMES = (
    "minimum_value", "maximum_value",
    "minimum_value_warning", "maximum_value_warning",
)


def _apply_bounds(registry: SettingsRegistry, overrides: dict[str, dict[str, float]]) -> None:
    """Apply nested bounds overrides {key: {field: value}} from defaults.py."""
    for key, fields in overrides.items():
        defn = registry.get(key)
        if not defn:
            continue
        for field_name, value in fields.items():
            if field_name in BOUNDS_FIELD_NAMES:
                setattr(defn, field_name, float(value))


def _apply_expressions(registry: SettingsRegistry, overrides: dict[str, str]) -> None:
    """Apply value_expression overrides from defaults.py to registry definitions."""
    for key, expr in overrides.items():
        defn = registry.get(key)
        if defn:
            defn.value_expression = expr


def _apply_bounds_from_ini(registry: SettingsRegistry, config_section) -> None:
    """Apply flat bounds overrides from config.ini (e.g. retraction_amount.maximum_value = 4)."""
    for entry, value in config_section.items():
        if "." not in entry:
            continue
        key, field_name = entry.rsplit(".", 1)
        defn = registry.get(key)
        if defn and field_name in BOUNDS_FIELD_NAMES:
            setattr(defn, field_name, float(value))


def load_config(config) -> Config:
    """Build a Config from a parsed configparser object."""
    archive_dir = Path(config["PATHS"]["archive_directory"])
    cura_bin = Path(config["PATHS"]["cura_engine_path"])
    def_dir = Path(config["PATHS"]["definition_dir"])
    printer_def = config["PATHS"]["printer_definition"]
    defaults = extract_defaults(SETTINGS)
    if config.has_section("DEFAULT_SETTINGS"):
        defaults.update(config["DEFAULT_SETTINGS"])
    forced_keys = extract_forced_keys(SETTINGS)
    telegram_token = config["TELEGRAM"]["bot_token"]

    allowed = config["TELEGRAM"].get("allowed_users", "").strip()
    allowed_users = _parse_allowed_users(allowed) if allowed else set()

    notify = config["TELEGRAM"].get("notify_chat_id", "").strip()
    notify_chat_id = int(notify) if notify else None

    api_port = int(config["TELEGRAM"].get("api_port", "0").strip() or "0")
    webapp_url = config["TELEGRAM"].get("webapp_url", "").strip()
    api_base_url = config["TELEGRAM"].get("api_base_url", "").strip()

    registry = load_registry(def_dir, printer_def)
    _apply_expressions(registry, extract_expression_overrides(SETTINGS))
    _apply_bounds(registry, extract_bounds_overrides(SETTINGS))
    if config.has_section("BOUNDS_OVERRIDES"):
        _apply_bounds_from_ini(registry, config["BOUNDS_OVERRIDES"])

    return Config(
        archive_dir=archive_dir,
        cura_bin=cura_bin,
        def_dir=def_dir,
        printer_def=printer_def,
        defaults=defaults,
        forced_keys=forced_keys,
        telegram_token=telegram_token,
        allowed_users=allowed_users,
        notify_chat_id=notify_chat_id,
        registry=registry,
        api_port=api_port,
        webapp_url=webapp_url,
        api_base_url=api_base_url,
    )


def is_allowed(config: Config, user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    if not config.allowed_users:
        return False
    return user_id in config.allowed_users

import os
from dataclasses import dataclass
from pathlib import Path

from .settings_registry import SettingsRegistry, load_registry


USERS_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / "allowed_users.txt"
RELOAD_CHAT_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / ".reload_chat_id"


@dataclass
class Config:
    archive_dir: Path
    cura_bin: Path
    def_dir: Path
    printer_def: str
    defaults: dict[str, str]
    telegram_token: str
    admin_users: set[int]
    chat_users: set[tuple[int, int]]
    notify_chat_id: int | None
    registry: SettingsRegistry
    api_port: int = 0
    webapp_url: str = ""
    api_base_url: str = ""


def _parse_admin_users(raw: str) -> set[int]:
    """Parse comma-separated user IDs into a set."""
    return set(int(x) for x in raw.split(",") if x.strip())


def _load_chat_users(users_file: Path) -> set[tuple[int, int]]:
    """Load chat-specific user permissions from file: 'user_id,chat_id' per line."""
    result: set[tuple[int, int]] = set()
    if not users_file.exists():
        return result
    for line in users_file.read_text().strip().split("\n"):
        line = line.split("#")[0].strip()
        if "," in line:
            user_id, chat_id = line.split(",", 1)
            result.add((int(user_id.strip()), int(chat_id.strip())))
    return result


def _apply_bounds_overrides(registry: SettingsRegistry, config_section) -> None:
    """Apply bounds overrides from config (e.g. retraction_amount.maximum_value = 4)."""
    for entry, value in config_section.items():
        if "." not in entry:
            continue
        key, field_name = entry.rsplit(".", 1)
        defn = registry.get(key)
        if defn and field_name in ("minimum_value", "maximum_value",
                                   "minimum_value_warning", "maximum_value_warning"):
            setattr(defn, field_name, float(value))


def load_config(config) -> Config:
    """Build a Config from a parsed configparser object."""
    archive_dir = Path(config["PATHS"]["archive_directory"])
    cura_bin = Path(config["PATHS"]["cura_engine_path"])
    def_dir = Path(config["PATHS"]["definition_dir"])
    printer_def = config["PATHS"]["printer_definition"]
    defaults = dict(config["DEFAULT_SETTINGS"])
    telegram_token = config["TELEGRAM"]["bot_token"]

    allowed = config["TELEGRAM"].get("allowed_users", "").strip()
    admin_users = _parse_admin_users(allowed) if allowed else set()
    chat_users = _load_chat_users(USERS_FILE)

    notify = config["TELEGRAM"].get("notify_chat_id", "").strip()
    notify_chat_id = int(notify) if notify else None

    api_port = int(config["TELEGRAM"].get("api_port", "0").strip() or "0")
    webapp_url = config["TELEGRAM"].get("webapp_url", "").strip()
    api_base_url = config["TELEGRAM"].get("api_base_url", "").strip()

    registry = load_registry(def_dir, printer_def)
    if config.has_section("BOUNDS_OVERRIDES"):
        _apply_bounds_overrides(registry, config["BOUNDS_OVERRIDES"])

    return Config(
        archive_dir=archive_dir,
        cura_bin=cura_bin,
        def_dir=def_dir,
        printer_def=printer_def,
        defaults=defaults,
        telegram_token=telegram_token,
        admin_users=admin_users,
        chat_users=chat_users,
        notify_chat_id=notify_chat_id,
        registry=registry,
        api_port=api_port,
        webapp_url=webapp_url,
        api_base_url=api_base_url,
    )


def save_users(config: Config) -> None:
    """Save chat-specific user permissions to file."""
    lines = [f"{uid},{cid}" for uid, cid in sorted(config.chat_users)]
    USERS_FILE.write_text("\n".join(lines) + "\n" if lines else "")


def is_allowed(config: Config, user_id: int, chat_id: int) -> bool:
    """Check if user is allowed in this chat."""
    if not config.admin_users and not config.chat_users:
        return True
    if user_id in config.admin_users:
        return True
    return (user_id, chat_id) in config.chat_users


def is_admin(config: Config, user_id: int) -> bool:
    """Check if user is an admin (from config.ini)."""
    return user_id in config.admin_users

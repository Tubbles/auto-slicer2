import os
from pathlib import Path

from .settings_registry import load_registry


USERS_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / "allowed_users.txt"
RELOAD_CHAT_FILE = Path(os.path.dirname(os.path.dirname(__file__))) / ".reload_chat_id"


class Config:
    def __init__(self, config):
        self.archive_dir = Path(config["PATHS"]["archive_directory"])
        self.cura_bin = Path(config["PATHS"]["cura_engine_path"])
        self.def_dir = Path(config["PATHS"]["definition_dir"])
        self.printer_def = config["PATHS"]["printer_definition"]
        self.defaults = dict(config["DEFAULT_SETTINGS"])
        self.telegram_token = config["TELEGRAM"]["bot_token"]
        # Load admin users from config (global access)
        allowed = config["TELEGRAM"].get("allowed_users", "").strip()
        self.admin_users: set[int] = set(int(x) for x in allowed.split(",") if x.strip())
        # Load chat-specific user permissions from file: "user_id,chat_id" per line
        self.chat_users: set[tuple[int, int]] = set()
        if USERS_FILE.exists():
            for line in USERS_FILE.read_text().strip().split("\n"):
                line = line.split("#")[0].strip()  # Remove comments
                if "," in line:
                    user_id, chat_id = line.split(",", 1)
                    self.chat_users.add((int(user_id.strip()), int(chat_id.strip())))
        notify = config["TELEGRAM"].get("notify_chat_id", "").strip()
        self.notify_chat_id: int | None = int(notify) if notify else None
        self.registry = load_registry(self.def_dir, self.printer_def)
        # Apply bounds overrides from config (e.g. retraction_amount.maximum_value = 4)
        if config.has_section("BOUNDS_OVERRIDES"):
            for entry, value in config["BOUNDS_OVERRIDES"].items():
                # Format: setting_key.field = value (e.g. retraction_amount.maximum_value = 4)
                if "." not in entry:
                    continue
                key, field = entry.rsplit(".", 1)
                defn = self.registry.get(key)
                if defn and field in ("minimum_value", "maximum_value",
                                      "minimum_value_warning", "maximum_value_warning"):
                    setattr(defn, field, float(value))


def save_users(config: Config) -> None:
    """Save chat-specific user permissions to file."""
    lines = [f"{uid},{cid}" for uid, cid in sorted(config.chat_users)]
    USERS_FILE.write_text("\n".join(lines) + "\n" if lines else "")


def is_allowed(config: Config, user_id: int, chat_id: int) -> bool:
    """Check if user is allowed in this chat."""
    # No restrictions if admin list is empty and no chat users defined
    if not config.admin_users and not config.chat_users:
        return True
    # Admins have global access
    if user_id in config.admin_users:
        return True
    # Check chat-specific permission
    return (user_id, chat_id) in config.chat_users


def is_admin(config: Config, user_id: int) -> bool:
    """Check if user is an admin (from config.ini)."""
    return user_id in config.admin_users

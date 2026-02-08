#!/usr/bin/env python

import os
import sys
import time
import subprocess
import shutil
import tempfile
import configparser
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


USERS_FILE = Path(__file__).parent / "allowed_users.txt"
RELOAD_CHAT_FILE = Path(__file__).parent / ".reload_chat_id"


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


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = {}


def slice_file(config: Config, stl_path: Path, overrides: dict) -> tuple[bool, str, Path | None]:
    """Slice an STL file and return (success, message, archive_path)."""
    active_settings = config.defaults.copy()
    active_settings.update(overrides)

    gcode_path = stl_path.with_suffix(".gcode")

    # CuraEngine needs both definitions and extruders directories
    extruders_dir = config.def_dir.parent / "extruders"

    cmd = [
        str(config.cura_bin),
        "slice",
        "-d", str(config.def_dir),
        "-d", str(extruders_dir),
        "-j", config.printer_def,
        "-l", str(stl_path),
        "-o", str(gcode_path),
    ]

    for key, val in active_settings.items():
        cmd.extend(["-s", f"{key}={val}"])

    print(f"[Slicing] {stl_path.name}")
    print(f"[Command] {' '.join(cmd)}")
    print(f"[Settings] {active_settings}")

    try:
        result = subprocess.run(cmd, cwd=str(config.def_dir), capture_output=True, text=True)

        if result.stdout:
            print(f"[stdout] {result.stdout}")
        if result.stderr:
            print(f"[stderr] {result.stderr}")
        print(f"[Exit code] {result.returncode}")

        if result.returncode == 0:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            job_folder = config.archive_dir / f"{stl_path.stem}_{timestamp}"
            job_folder.mkdir(parents=True, exist_ok=True)

            shutil.move(str(stl_path), job_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)

            print(f"[Success] Archived to {job_folder}")
            return True, "Slicing completed successfully", job_folder
        else:
            error_dir = config.archive_dir / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stl_path), error_dir / stl_path.name)
            # Include both stdout and stderr in error message
            output = result.stdout + result.stderr
            error_msg = output.strip()[:500] if output.strip() else f"Exit code {result.returncode}"
            print(f"[Failed] {error_msg}")
            return False, f"CuraEngine error:\n{error_msg}", error_dir

    except Exception as e:
        print(f"[Exception] {e}")
        return False, f"System error: {e}", None


HELP_TEXT = """Auto-Slicer Bot

Send me an STL file and I'll slice it with CuraEngine.

Commands:
/help - Show this message
/settings - Show available settings
/settings key=value ... - Set slicer overrides
/mysettings - Show your current settings
/clear - Reset to defaults

Admin commands:
/adduser - Reply to add user (this chat only)
/removeuser - Reply to remove user from this chat
/listusers - Show allowed users for this chat
/reload - Pull updates and restart"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id, update.effective_chat.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    await update.message.reply_text(HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id, update.effective_chat.id):
        return
    await update.message.reply_text(HELP_TEXT)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command to set user overrides."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /settings key=value ...\n\n"
            "Common settings:\n"
            "  layer_height - Layer height in mm (0.1-0.3)\n"
            "  infill_sparse_density - Infill % (0-100)\n"
            "  wall_line_count - Number of walls (2-4)\n"
            "  top_layers - Top solid layers (3-6)\n"
            "  bottom_layers - Bottom solid layers (3-6)\n"
            "  support_enable - Generate supports (true/false)\n"
            "  adhesion_type - skirt, brim, raft, none\n"
            "  material_print_temperature - Hotend temp\n"
            "  material_bed_temperature - Bed temp\n"
            "  speed_print - Print speed in mm/s\n\n"
            "Example: /settings layer_height=0.2 infill_sparse_density=20"
        )
        return

    if user_id not in user_settings:
        user_settings[user_id] = {}

    parsed = []
    for arg in context.args:
        if "=" in arg:
            key, val = arg.split("=", 1)
            user_settings[user_id][key] = val
            parsed.append(f"{key}={val}")

    if parsed:
        await update.message.reply_text(f"Settings saved: {', '.join(parsed)}")
    else:
        await update.message.reply_text("No valid key=value pairs found.")


async def mysettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mysettings command to show current overrides."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return
    settings = user_settings.get(user_id, {})

    if settings:
        lines = [f"  {k}={v}" for k, v in settings.items()]
        await update.message.reply_text("Your settings:\n" + "\n".join(lines))
    else:
        await update.message.reply_text("No custom settings. Using defaults.")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command to reset user settings."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return
    user_settings.pop(user_id, None)
    await update.message.reply_text("Settings cleared. Using defaults.")


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reload command to pull updates and restart."""
    config: Config = context.bot_data["config"]
    if not is_admin(config, update.effective_user.id):
        return

    await update.message.reply_text("Pulling latest changes...")

    script_dir = Path(__file__).parent
    result = subprocess.run(
        ["git", "pull"], cwd=script_dir, capture_output=True, text=True
    )

    if result.returncode != 0:
        await update.message.reply_text(f"Git pull failed:\n{result.stderr[:500]}")
        return

    await update.message.reply_text(f"{result.stdout.strip()}\n\nRestarting...")
    RELOAD_CHAT_FILE.write_text(str(update.effective_chat.id))
    os._exit(0)


async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adduser command - reply to a message to add that user to this chat."""
    config: Config = context.bot_data["config"]
    if not is_admin(config, update.effective_user.id):
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Reply to a message from the user you want to add.")
        return

    target = reply.from_user
    chat_id = update.effective_chat.id

    if target.id in config.admin_users:
        await update.message.reply_text(f"@{target.username or target.id} is already an admin (global access).")
        return

    if (target.id, chat_id) in config.chat_users:
        await update.message.reply_text(f"@{target.username or target.id} is already allowed in this chat.")
        return

    config.chat_users.add((target.id, chat_id))
    save_users(config)
    name = f"@{target.username}" if target.username else target.full_name
    await update.message.reply_text(f"Added {name} ({target.id}) to this chat.")


async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removeuser command - reply to a message to remove that user from this chat."""
    config: Config = context.bot_data["config"]
    if not is_admin(config, update.effective_user.id):
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text("Reply to a message from the user you want to remove.")
        return

    target = reply.from_user
    chat_id = update.effective_chat.id

    if target.id in config.admin_users:
        await update.message.reply_text("Cannot remove admin users (edit config.ini instead).")
        return

    if (target.id, chat_id) not in config.chat_users:
        await update.message.reply_text(f"@{target.username or target.id} is not in this chat's allowed list.")
        return

    config.chat_users.discard((target.id, chat_id))
    save_users(config)
    name = f"@{target.username}" if target.username else target.full_name
    await update.message.reply_text(f"Removed {name} ({target.id}) from this chat.")


async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /listusers command - show allowed users for this chat."""
    config: Config = context.bot_data["config"]
    if not is_admin(config, update.effective_user.id):
        return

    chat_id = update.effective_chat.id

    if not config.admin_users and not config.chat_users:
        await update.message.reply_text("No user restrictions (everyone allowed).")
        return

    lines = []
    # Show admins (global access)
    for uid in sorted(config.admin_users):
        lines.append(f"  {uid} (admin - global)")
    # Show users with access to this specific chat
    for uid, cid in sorted(config.chat_users):
        if cid == chat_id:
            lines.append(f"  {uid} (this chat only)")

    if lines:
        await update.message.reply_text("Allowed users:\n" + "\n".join(lines))
    else:
        await update.message.reply_text("No users allowed in this chat (admins only).")


async def post_init(app) -> None:
    """Send startup notification to reload origin chat, or fallback to config."""
    config: Config = app.bot_data["config"]
    chat_id = None
    if RELOAD_CHAT_FILE.exists():
        try:
            chat_id = int(RELOAD_CHAT_FILE.read_text().strip())
        except ValueError:
            pass
        RELOAD_CHAT_FILE.unlink()
    if not chat_id:
        chat_id = config.notify_chat_id
    if chat_id:
        await app.bot.send_message(chat_id, "Auto-Slicer Bot is online!")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle STL file uploads."""
    document = update.message.document

    if not document.file_name.lower().endswith(".stl"):
        return

    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return
    overrides = user_settings.get(user_id, {})

    await update.message.reply_text(f"Received {document.file_name}, slicing...")

    with tempfile.TemporaryDirectory() as tmpdir:
        stl_path = Path(tmpdir) / document.file_name
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(stl_path)

        success, message, archive_path = slice_file(config, stl_path, overrides)

        if success:
            await update.message.reply_text(
                f"Done! Archived to:\n{archive_path}"
            )
        else:
            await update.message.reply_text(f"Slicing failed: {message}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Auto-slicer Telegram bot")
    parser.add_argument("-c", "--config", type=str, default="config.ini", help="Path to config file")
    args = parser.parse_args()

    config_file = configparser.ConfigParser()
    config_file.read(args.config)
    config = Config(config_file)

    config.archive_dir.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(config.telegram_token).post_init(post_init).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("mysettings", mysettings_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("listusers", listusers_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

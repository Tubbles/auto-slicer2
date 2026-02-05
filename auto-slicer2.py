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


class Config:
    def __init__(self, config):
        self.archive_dir = Path(config["PATHS"]["archive_directory"])
        self.cura_bin = Path(config["PATHS"]["cura_engine_path"])
        self.def_dir = Path(config["PATHS"]["definition_dir"])
        self.printer_def = config["PATHS"]["printer_definition"]
        self.defaults = dict(config["DEFAULT_SETTINGS"])
        self.telegram_token = config["TELEGRAM"]["bot_token"]
        allowed = config["TELEGRAM"].get("allowed_users", "").strip()
        self.allowed_users: set[int] = set(int(x) for x in allowed.split(",") if x.strip())
        notify = config["TELEGRAM"].get("notify_chat_id", "").strip()
        self.notify_chat_id: int | None = int(notify) if notify else None


def is_allowed(config: Config, user_id: int) -> bool:
    """Check if user is allowed (empty whitelist = everyone allowed)."""
    return not config.allowed_users or user_id in config.allowed_users


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = {}


def slice_file(config: Config, stl_path: Path, overrides: dict) -> tuple[bool, str, Path | None]:
    """Slice an STL file and return (success, message, archive_path)."""
    active_settings = config.defaults.copy()
    active_settings.update(overrides)

    gcode_path = stl_path.with_suffix(".gcode")

    cmd = [
        str(config.cura_bin),
        "slice",
        "-d",
        str(config.def_dir),
        "-j",
        config.printer_def,
        "-l",
        str(stl_path),
        "-o",
        str(gcode_path),
    ]

    for key, val in active_settings.items():
        cmd.extend(["-s", f"{key}={val}"])

    try:
        result = subprocess.run(cmd, cwd=str(config.def_dir), capture_output=True, text=True)

        if result.returncode == 0:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            job_folder = config.archive_dir / f"{stl_path.stem}_{timestamp}"
            job_folder.mkdir(parents=True, exist_ok=True)

            shutil.move(str(stl_path), job_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)

            return True, "Slicing completed successfully", job_folder
        else:
            error_dir = config.archive_dir / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stl_path), error_dir / stl_path.name)
            return False, f"CuraEngine error: {result.stderr[:500]}", error_dir

    except Exception as e:
        return False, f"System error: {e}", None


HELP_TEXT = """Auto-Slicer Bot

Send me an STL file and I'll slice it with CuraEngine.

Commands:
/help - Show this message
/settings - Show available settings
/settings key=value ... - Set slicer overrides
/mysettings - Show your current settings
/clear - Reset to defaults
/reload - Pull updates and restart"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    await update.message.reply_text(HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id):
        return
    await update.message.reply_text(HELP_TEXT)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command to set user overrides."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id):
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
    if not is_allowed(config, user_id):
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
    if not is_allowed(config, user_id):
        return
    user_settings.pop(user_id, None)
    await update.message.reply_text("Settings cleared. Using defaults.")


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reload command to pull updates and restart."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id):
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
    os._exit(0)


async def post_init(app) -> None:
    """Send startup notification."""
    config: Config = app.bot_data["config"]
    if config.notify_chat_id:
        await app.bot.send_message(config.notify_chat_id, "Auto-Slicer Bot is online!")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle STL file uploads."""
    document = update.message.document

    if not document.file_name.lower().endswith(".stl"):
        return

    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id):
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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

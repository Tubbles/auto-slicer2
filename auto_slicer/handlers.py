import subprocess
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from .config import Config, RELOAD_CHAT_FILE, save_users, is_allowed, is_admin
from .slicer import slice_file


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = {}


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
    import os
    config: Config = context.bot_data["config"]
    if not is_admin(config, update.effective_user.id):
        return

    await update.message.reply_text("Pulling latest changes...")

    script_dir = Path(__file__).parent.parent
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

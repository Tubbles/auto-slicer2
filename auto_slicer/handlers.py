import json
import subprocess
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonDefault, Update, WebAppInfo
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .config import Config, RELOAD_CHAT_FILE, is_allowed
from .slicer import slice_file


SETTINGS_FILE = Path(__file__).parent.parent / "user_settings.json"


def load_user_settings(path: Path) -> dict[int, dict]:
    """Load per-user settings overrides from a JSON file."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {int(k): v for k, v in data.items()}


def save_user_settings(path: Path, settings: dict[int, dict]) -> None:
    """Atomically write per-user settings overrides to a JSON file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.rename(path)


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = load_user_settings(SETTINGS_FILE)


HELP_TEXT = """Auto-Slicer Bot

Send me an STL file and I'll slice it with CuraEngine.

Commands:
/webapp - Open settings Mini App
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


async def webapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /webapp command — open settings Mini App."""
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id):
        return

    if not config.webapp_url or not config.api_base_url:
        await update.message.reply_text("Mini App is not configured.")
        return

    url = f"{config.webapp_url}?api={config.api_base_url}"
    button = InlineKeyboardButton("Open Settings", web_app=WebAppInfo(url=url))
    keyboard = InlineKeyboardMarkup([[button]])
    await update.message.reply_text("Tap to open settings:", reply_markup=keyboard)


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reload command to pull updates and restart."""
    import os
    config: Config = context.bot_data["config"]
    if not is_allowed(config, update.effective_user.id):
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


async def post_init(app) -> None:
    """Send startup notification and start HTTP API if configured."""
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
        try:
            await app.bot.send_message(chat_id, "Auto-Slicer Bot is online!")
        except Exception as e:
            print(f"Startup notification failed: {e}")

    # Clear any previously-set menu button (bot-wide default + per-chat)
    default_btn = MenuButtonDefault()
    await app.bot.set_chat_menu_button(menu_button=default_btn)
    if config.notify_chat_id:
        try:
            await app.bot.set_chat_menu_button(
                chat_id=config.notify_chat_id, menu_button=default_btn,
            )
        except Exception:
            pass

    # Start HTTP API server if configured
    if config.api_port > 0:
        from aiohttp import web as aio_web
        from .web_api import create_web_app

        save_fn = lambda: save_user_settings(SETTINGS_FILE, user_settings)
        web_app = create_web_app(config, user_settings, save_fn=save_fn)
        runner = aio_web.AppRunner(web_app)
        await runner.setup()
        site = aio_web.TCPSite(runner, "0.0.0.0", config.api_port)
        await site.start()
        app.bot_data["_api_runner"] = runner
        print(f"HTTP API started on port {config.api_port}")


async def post_shutdown(app) -> None:
    """Clean up the HTTP API server."""
    runner = app.bot_data.get("_api_runner")
    if runner:
        await runner.cleanup()


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
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT,
    )

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

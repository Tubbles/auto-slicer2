import json
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonDefault, Update, WebAppInfo
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .config import Config, RELOAD_CHAT_FILE, is_allowed
from .slicer import slice_file
from .web_api import generate_token, TOKEN_TTL


SETTINGS_FILE = Path(__file__).parent.parent / "user_settings.json"
STARRED_FILE = Path(__file__).parent.parent / "starred_keys.json"
STARRED_DEFAULT_FILE = Path(__file__).parent.parent / "starred_keys.default.json"


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


def load_starred_keys(path: Path, default_path: Path) -> set[str]:
    """Load starred setting keys from a JSON file.

    If the runtime file is missing, copies from the default template.
    Returns an empty set if neither file exists.
    """
    if not path.exists():
        if default_path.exists():
            data = json.loads(default_path.read_text())
            save_starred_keys(path, set(data))
            return set(data)
        return set()
    return set(json.loads(path.read_text()))


def save_starred_keys(path: Path, keys: set[str]) -> None:
    """Atomically write starred keys to a JSON file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(keys), indent=2))
    tmp.rename(path)


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = load_user_settings(SETTINGS_FILE)

# Globally shared starred keys
starred_keys: set[str] = load_starred_keys(STARRED_FILE, STARRED_DEFAULT_FILE)


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

    token = generate_token()
    tokens = context.bot_data["tokens"]
    now = time.time()
    tokens[token] = (update.effective_user.id, now + TOKEN_TTL, now)

    url = f"{config.webapp_url}?api={config.api_base_url}&token={token}"
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

        tokens: dict = app.bot_data.setdefault("tokens", {})
        save_fn = lambda: save_user_settings(SETTINGS_FILE, user_settings)
        save_starred_fn = lambda: save_starred_keys(STARRED_FILE, starred_keys)
        web_app = create_web_app(
            config, user_settings, save_fn=save_fn,
            starred_keys=starred_keys, save_starred_fn=save_starred_fn,
            tokens=tokens,
        )
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


def _find_stls_in_zip(zip_dir: Path) -> list[Path]:
    """Recursively find STL files in an extracted ZIP, skipping macOS artifacts."""
    return [
        p for p in zip_dir.rglob("*.[sS][tT][lL]")
        if "__MACOSX" not in p.parts and not p.name.startswith("._")
    ]


async def _handle_zip(update: Update, config: Config, zip_path: Path, overrides: dict) -> None:
    """Extract a ZIP and slice all STL files inside it."""
    with tempfile.TemporaryDirectory() as extract_dir:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            await update.message.reply_text("Invalid ZIP file.")
            return

        stls = _find_stls_in_zip(Path(extract_dir))
        if not stls:
            await update.message.reply_text("No STL files found in ZIP.")
            return

        n = len(stls)
        await update.message.reply_text(
            f"Received {zip_path.name}, slicing {n} STL file{'s' if n != 1 else ''}..."
        )

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive_folder = config.archive_dir / zip_path.stem / timestamp

        failures = []
        for stl in sorted(stls):
            success, message, _ = slice_file(config, stl, overrides, archive_folder=archive_folder)
            if not success:
                failures.append((stl.name, message))

        ok = n - len(failures)
        lines = [f"Done! {ok}/{n} sliced.", f"Archived to: {archive_folder}"]
        for name, msg in failures:
            lines.append(f"Failed: {name} — {msg}")
        await update.message.reply_text("\n".join(lines))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle STL and ZIP file uploads."""
    document = update.message.document
    name_lower = document.file_name.lower()

    if not name_lower.endswith((".stl", ".zip")):
        return

    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id):
        return
    overrides = user_settings.get(user_id, {})

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / document.file_name
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(file_path)

        if name_lower.endswith(".zip"):
            await _handle_zip(update, config, file_path, overrides)
        else:
            await update.message.reply_text(f"Received {document.file_name}, slicing...")
            success, message, archive_path = slice_file(config, file_path, overrides)
            if success:
                await update.message.reply_text(f"Done! Archived to:\n{archive_path}")
            else:
                await update.message.reply_text(f"Slicing failed: {message}")

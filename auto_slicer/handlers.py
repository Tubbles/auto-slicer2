import subprocess
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .config import Config, RELOAD_CHAT_FILE, save_users, is_allowed, is_admin
from .slicer import slice_file
from .settings_match import SettingsMatcher
from .settings_validate import SettingsValidator
from .presets import PresetManager


# Maximum callback_data length in Telegram Bot API
_MAX_CALLBACK_DATA = 64


# Per-user settings overrides, keyed by Telegram user ID
user_settings: dict[int, dict] = {}


HELP_TEXT = """Auto-Slicer Bot

Send me an STL file and I'll slice it with CuraEngine.

Settings:
/settings key=value - Set overrides (names or labels)
/settings search <query> - Find settings by keyword
/mysettings - Show your current overrides
/preset - List presets (draft, standard, fine, strong)
/preset <name> - Apply a preset
/clear - Reset to defaults

Admin:
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


def _parse_settings_args(text: str) -> list[tuple[str, str]]:
    """Parse 'key=value' pairs from message text after the /settings command.

    Supports quoted keys: "layer height"=0.2 or 'layer height'=0.2
    and plain keys: layer_height=0.2
    """
    import re
    # Strip the /settings command prefix
    text = re.sub(r"^/settings(@\S+)?\s*", "", text).strip()
    if not text:
        return []

    pairs = []
    # Match: optional quotes around key, then =, then value (up to next space or end)
    pattern = re.compile(
        r"""(?:"([^"]+)"|'([^']+)'|(\S+?))=(\S+)""",
    )
    for m in pattern.finditer(text):
        key = m.group(1) or m.group(2) or m.group(3)
        val = m.group(4)
        pairs.append((key, val))
    return pairs


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command to set user overrides."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return

    matcher = SettingsMatcher(config.registry)
    validator = SettingsValidator()

    if not context.args:
        await update.message.reply_text(
            "Usage: /settings key=value ...\n\n"
            "Common settings:\n"
            "  layer_height - Layer height in mm\n"
            "  infill_sparse_density - Infill % (0-100)\n"
            "  wall_line_count - Number of walls\n"
            "  support_enable - Generate supports (true/false)\n"
            "  adhesion_type - skirt, brim, raft, none\n"
            "  material_print_temperature - Hotend temp\n"
            "  speed_print - Print speed in mm/s\n\n"
            "You can use setting names or labels:\n"
            '  /settings layer_height=0.2\n'
            '  /settings "layer height"=0.2\n\n'
            "Use /settings search <query> to find settings."
        )
        return

    # Delegate to search handler (commit 6 adds this)
    if context.args[0].lower() == "search":
        await _settings_search(update, context)
        return

    pairs = _parse_settings_args(update.message.text)
    if not pairs:
        await update.message.reply_text("No valid key=value pairs found.")
        return

    if user_id not in user_settings:
        user_settings[user_id] = {}

    response_lines = []
    for query, raw_value in pairs:
        resolved_key, candidates = matcher.resolve(query)

        if resolved_key is None and not candidates:
            response_lines.append(f"Unknown setting: '{query}'")
            continue

        if resolved_key is None:
            # Ambiguous — send buttons for each candidate (needs its own message)
            buttons = []
            for c in candidates[:5]:
                if raw_value:
                    cb_data = f"disambig:{c.key}:{raw_value}"
                else:
                    cb_data = f"pick:{c.key}"
                if len(cb_data.encode()) <= _MAX_CALLBACK_DATA:
                    buttons.append([InlineKeyboardButton(
                        f"{c.label} ({c.key})", callback_data=cb_data,
                    )])
            if buttons:
                keyboard = InlineKeyboardMarkup(buttons)
                await update.message.reply_text(
                    f"Ambiguous: '{query}'. Did you mean:",
                    reply_markup=keyboard,
                )
            else:
                names = [f"  {c.key} ({c.label})" for c in candidates[:5]]
                response_lines.append(
                    f"Ambiguous: '{query}'. Did you mean:\n" + "\n".join(names)
                )
            continue

        defn = config.registry.get(resolved_key)
        result = validator.validate(defn, raw_value)

        if not result.ok:
            response_lines.append(f"{defn.label}: {result.error}")
            continue

        user_settings[user_id][resolved_key] = result.coerced_value
        unit = f" {defn.unit}" if defn.unit else ""
        response_lines.append(f"{defn.label}: {result.coerced_value}{unit}")
        if result.warning:
            response_lines.append(f"  Warning: {result.warning}")

    if response_lines:
        await update.message.reply_text("\n".join(response_lines))


async def _settings_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings search <query> — search settings by key, label, or description."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    query = " ".join(context.args[1:]).lower()

    if not query:
        await update.message.reply_text("Usage: /settings search <query>\nExample: /settings search infill")
        return

    overrides = user_settings.get(user_id, {})
    results = []
    for key, defn in config.registry.all_settings().items():
        if (query in key.lower()
                or query in defn.label.lower()
                or query in defn.description.lower()):
            results.append(defn)

    if not results:
        await update.message.reply_text(f"No settings found matching '{query}'.")
        return

    lines = [f"Settings matching '{query}' ({min(len(results), 10)} of {len(results)}):\n"]
    buttons = []
    for defn in results[:10]:
        unit = f" {defn.unit}" if defn.unit else ""
        current = overrides.get(defn.key)
        current_str = f" (set: {current})" if current else ""
        lines.append(
            f"  {defn.key}\n"
            f"    {defn.label} [{defn.setting_type}]"
            f" default: {defn.default_value}{unit}{current_str}"
        )
        cb_data = f"pick:{defn.key}"
        if len(cb_data.encode()) <= _MAX_CALLBACK_DATA:
            buttons.append([InlineKeyboardButton(
                f"Set {defn.label}", callback_data=cb_data,
            )])

    text = "\n".join(lines)
    # Telegram message limit is 4096 chars
    if len(text) > 4096:
        text = text[:4090] + "\n..."
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(text, reply_markup=keyboard)


def _format_mysettings(config: Config, settings: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """Format the /mysettings text and remove-buttons keyboard."""
    if not settings:
        return "No custom settings. Using defaults.", None

    lines = []
    buttons = []
    for key, val in settings.items():
        defn = config.registry.get(key)
        if defn:
            unit = f" {defn.unit}" if defn.unit else ""
            lines.append(f"  {defn.label}: {val}{unit}")
            label = defn.label
        else:
            lines.append(f"  {key}: {val}")
            label = key
        cb_data = f"rm:{key}"
        if len(cb_data.encode()) <= _MAX_CALLBACK_DATA:
            buttons.append([InlineKeyboardButton(
                f"x {label}", callback_data=cb_data,
            )])

    text = "Your settings:\n" + "\n".join(lines)
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    return text, keyboard


async def mysettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mysettings command to show current overrides."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return
    settings = user_settings.get(user_id, {})

    text, keyboard = _format_mysettings(config, settings)
    await update.message.reply_text(text, reply_markup=keyboard)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command to reset user settings."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return
    user_settings.pop(user_id, None)
    await update.message.reply_text("Settings cleared. Using defaults.")


async def preset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /preset command to list or apply presets."""
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_allowed(config, user_id, update.effective_chat.id):
        return

    presets = PresetManager()

    if not context.args:
        buttons = []
        for name, preset in presets.list_presets().items():
            buttons.append(InlineKeyboardButton(
                name.capitalize(), callback_data=f"preset:{name}",
            ))
        keyboard = InlineKeyboardMarkup([buttons])
        await update.message.reply_text("Choose a preset:", reply_markup=keyboard)
        return

    name = context.args[0].lower()
    preset = presets.get(name)
    if not preset:
        await update.message.reply_text(
            f"Unknown preset '{name}'. Available: {', '.join(presets.names())}"
        )
        return

    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id].update(preset["settings"])

    lines = [f"Applied preset '{name}':\n"]
    for key, val in preset["settings"].items():
        defn = config.registry.get(key)
        if defn:
            unit = f" {defn.unit}" if defn.unit else ""
            lines.append(f"  {defn.label}: {val}{unit}")
        else:
            lines.append(f"  {key}: {val}")
    await update.message.reply_text("\n".join(lines))


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


# --- Inline keyboard helpers ---

def _build_value_picker(defn) -> InlineKeyboardMarkup | None:
    """Build an inline keyboard for bool/enum settings. Returns None for others."""
    if defn.setting_type == "bool":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("True", callback_data=f"val:{defn.key}:true"),
            InlineKeyboardButton("False", callback_data=f"val:{defn.key}:false"),
        ]])

    if defn.setting_type == "enum":
        buttons = []
        for opt_key, opt_label in defn.options.items():
            cb_data = f"val:{defn.key}:{opt_key}"
            if len(cb_data.encode()) > _MAX_CALLBACK_DATA:
                continue  # Skip options that exceed Telegram's limit
            buttons.append(InlineKeyboardButton(opt_label, callback_data=cb_data))
        if not buttons:
            return None
        # Arrange in rows of 3
        rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
        return InlineKeyboardMarkup(rows)

    return None


# --- Inline keyboard callback handling ---

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline keyboard callbacks by prefix."""
    query = update.callback_query
    data = query.data

    if data.startswith("preset:"):
        await _cb_preset(update, context)
    elif data == "undo_preset":
        await _cb_undo_preset(update, context)
    elif data.startswith("pick:"):
        await _cb_pick(update, context)
    elif data.startswith("val:"):
        await _cb_val(update, context)
    elif data.startswith("rm:"):
        await _cb_rm(update, context)
    elif data.startswith("disambig:"):
        await _cb_disambig(update, context)
    else:
        await query.answer("Unknown action.")


async def _cb_preset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    name = query.data.removeprefix("preset:")

    presets = PresetManager()
    preset = presets.get(name)
    if not preset:
        await query.answer(f"Unknown preset '{name}'.")
        return

    # Store previous settings for undo
    context.user_data["prev_settings"] = dict(user_settings.get(user_id, {}))

    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id].update(preset["settings"])

    lines = [f"Applied preset '{name}':\n"]
    for key, val in preset["settings"].items():
        defn = config.registry.get(key)
        if defn:
            unit = f" {defn.unit}" if defn.unit else ""
            lines.append(f"  {defn.label}: {val}{unit}")
        else:
            lines.append(f"  {key}: {val}")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Undo", callback_data="undo_preset")],
    ])
    await query.answer()
    await query.edit_message_text("\n".join(lines), reply_markup=keyboard)


async def _cb_undo_preset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    prev = context.user_data.get("prev_settings")

    if prev is None:
        await query.answer("Nothing to undo.")
        return

    user_settings[user_id] = prev
    context.user_data.pop("prev_settings", None)

    await query.answer("Preset undone.")
    await query.edit_message_text("Preset undone. Previous settings restored.")


async def _cb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    key = query.data.removeprefix("pick:")

    defn = config.registry.get(key)
    if not defn:
        await query.answer("Unknown setting.")
        return

    keyboard = _build_value_picker(defn)
    if keyboard:
        unit = f" ({defn.unit})" if defn.unit else ""
        await query.answer()
        await query.edit_message_text(
            f"{defn.label}{unit}:", reply_markup=keyboard,
        )
    else:
        unit = f" {defn.unit}" if defn.unit else ""
        await query.answer()
        await query.edit_message_text(
            f"Type: /settings {defn.key}=<value>\n"
            f"  {defn.label} [{defn.setting_type}] default: {defn.default_value}{unit}",
        )


async def _cb_val(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id

    # Format: val:<key>:<value>
    parts = query.data.split(":", 2)
    if len(parts) < 3:
        await query.answer("Invalid callback data.")
        return
    key, raw_value = parts[1], parts[2]

    defn = config.registry.get(key)
    if not defn:
        await query.answer("Unknown setting.")
        return

    validator = SettingsValidator()
    result = validator.validate(defn, raw_value)
    if not result.ok:
        await query.answer(result.error[:200])  # Telegram answer limit
        return

    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id][key] = result.coerced_value

    unit = f" {defn.unit}" if defn.unit else ""
    text = f"{defn.label}: {result.coerced_value}{unit}"
    if result.warning:
        text += f"\nWarning: {result.warning}"

    await query.answer()
    await query.edit_message_text(text)


async def _cb_rm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    key = query.data.removeprefix("rm:")

    settings = user_settings.get(user_id, {})
    removed = settings.pop(key, None)
    if not settings:
        user_settings.pop(user_id, None)

    defn = config.registry.get(key)
    label = defn.label if defn else key
    await query.answer(f"Removed {label}.")

    # Re-render the /mysettings message in-place
    remaining = user_settings.get(user_id, {})
    text, keyboard = _format_mysettings(config, remaining)
    await query.edit_message_text(text, reply_markup=keyboard)


async def _cb_disambig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id

    # Format: disambig:<key>:<value>
    parts = query.data.split(":", 2)
    if len(parts) < 3:
        await query.answer("Invalid callback data.")
        return
    key, raw_value = parts[1], parts[2]

    defn = config.registry.get(key)
    if not defn:
        await query.answer("Unknown setting.")
        return

    validator = SettingsValidator()
    result = validator.validate(defn, raw_value)
    if not result.ok:
        await query.answer(result.error[:200])
        return

    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id][key] = result.coerced_value

    unit = f" {defn.unit}" if defn.unit else ""
    text = f"{defn.label}: {result.coerced_value}{unit}"
    if result.warning:
        text += f"\nWarning: {result.warning}"

    await query.answer()
    await query.edit_message_text(text)

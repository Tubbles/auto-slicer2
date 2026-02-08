# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

auto-slicer2 is a Telegram bot that slices STL files using CuraEngine. Users send STL files to the bot, optionally configure slicer settings via commands, and receive notifications when slicing completes.

## Running the Bot

```bash
# Install dependency
pip install python-telegram-bot

# Run with default config.ini
python auto-slicer2.py

# Run with custom config
python auto-slicer2.py -c /path/to/config.ini
```

## Dependencies

- Python 3.10+ (uses `X | Y` union syntax)
- `python-telegram-bot` library
- CuraEngine binary (path configured in config.ini)
- Cura printer definitions directory

## Bot Commands

- `/start` - Welcome message and usage
- `/settings key=value ...` - Set overrides (supports names, labels, fuzzy matching)
- `/settings search <query>` - Find settings by keyword
- `/mysettings` - Show current overrides with [x] remove buttons
- `/preset` - Choose a preset via inline buttons
- `/preset <name>` - Apply a preset directly (draft, standard, fine, strong)
- `/clear` - Reset to defaults

## Architecture

### File Structure

```
auto_slicer/
  __init__.py              # empty
  config.py                # Config class, user file I/O, permission checks
  slicer.py                # slice_file()
  handlers.py              # Telegram command/callback handlers, inline keyboards
  settings_registry.py     # SettingDefinition dataclass + SettingsRegistry
  settings_match.py        # SettingsMatcher (fuzzy/natural language resolution)
  settings_validate.py     # SettingsValidator (type + bounds checking)
  presets.py               # PresetManager + BUILTIN_PRESETS
auto-slicer2.py            # thin entry point (argparse, app wiring)
tests/
  test_settings.py         # tests for registry, matcher, validator, presets, inline keyboards
```

### Key Components

**Config** (`config.py`): Loads paths, defaults, and Telegram token from config.ini. Creates a `SettingsRegistry` at init time.

**SettingsRegistry** (`settings_registry.py`): Loads CuraEngine's fdmprinter.def.json, flattens the nested settings tree, follows the inherits chain (e.g. creality_ender3 → creality_base → fdmprinter), and builds label→key indexes.

**SettingsMatcher** (`settings_match.py`): Resolves user queries to setting keys via tiered matching: exact key, exact label, substring, then fuzzy (difflib).

**SettingsValidator** (`settings_validate.py`): Type-checks and bounds-checks values for float, int, bool, enum, and str settings. Hard bounds reject; warning bounds accept with a warning.

**PresetManager** (`presets.py`): Built-in presets (draft/standard/fine/strong) and optional custom presets from presets.json.

**Per-user settings**: `user_settings` dict in handlers.py stores overrides keyed by Telegram user ID (in-memory, resets on restart).

### Inline Keyboard Callbacks

All inline button callbacks route through `callback_router()`, dispatched by prefix:

```
preset:<name>            → apply preset (e.g. "preset:draft")
undo_preset              → restore pre-preset settings
pick:<key>               → show value picker for setting
val:<key>:<value>        → apply a value to a setting
rm:<key>                 → remove single override from /mysettings
disambig:<key>:<value>   → resolve ambiguous match with known value
```

Telegram limits callback_data to 64 bytes. Settings with extremely long keys are skipped for buttons and fall back to text prompts.

### Workflow

1. User sends `/settings layer_height=0.1` (or `/settings "layer height"=0.1`, or `/preset fine`)
2. Setting key resolved via SettingsMatcher, value validated via SettingsValidator
3. User sends STL file as document
4. Bot downloads to temp directory
5. `slice_file()` invokes CuraEngine with merged settings (defaults + user overrides)
6. On success: archives STL+gcode to timestamped subfolder, notifies user with path
7. On failure: moves STL to `archive/errors/`, sends error message

## Configuration (config.ini)

- `[PATHS]`: archive_directory, cura_engine_path, definition_dir, printer_definition
- `[DEFAULT_SETTINGS]`: CuraEngine setting key-value pairs
- `[TELEGRAM]`: bot_token, allowed_users (comma-separated user IDs, empty = everyone)

## Git Workflow

- Create small, focused commits as you go so changes are easy to review and revert.
- Each commit should address a single concern (one bug fix, one feature, one refactor).
- Use a succinct imperative commit title (e.g. "Add retry logic for API calls").
- Include gotchas, caveats, or non-obvious side effects in the commit message body.
- Never add "Co-Authored-By" lines or email addresses to commit messages.
- Push freely without asking, but never use `git push --force` or any force-push variant.

## Systemd User Service

```bash
# Install
mkdir -p ~/.config/systemd/user
cp auto-slicer2.service ~/.config/systemd/user/
systemctl --user daemon-reload

# Enable and start
systemctl --user enable auto-slicer2
systemctl --user start auto-slicer2

# Check status / logs
systemctl --user status auto-slicer2
journalctl --user -u auto-slicer2 -f
```

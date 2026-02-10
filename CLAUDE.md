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
- `/help` - Show help text
- `/webapp` - Open settings Mini App
- `/reload` - Pull updates and restart

## Coding Style

- **No OOP patterns.** Do not use inheritance, polymorphism, or class hierarchies. Dataclasses for holding data are fine; classes with methods that dispatch on type or override behavior are not. Think C/Rust, not Java.
- **Small, focused functions.** Aim for 5-10 lines per function. Extract logic into named helpers rather than writing long functions.
- **Pure functions where possible.** Functions should take inputs, return outputs, and avoid side effects. Side effects (I/O, mutating shared state) should be pushed to the edges — thin handler functions that call pure logic.
- **Test everything with pytest.** Every non-trivial function should have corresponding tests in `tests/`. Pure functions are easy to test; if a function is hard to test, it probably does too much.

## Architecture

### File Structure

```
auto_slicer/
  __init__.py              # empty
  config.py                # Config class, permission checks
  slicer.py                # slice_file()
  handlers.py              # Telegram command handlers (start, help, webapp, reload, document)
  settings_registry.py     # SettingDefinition dataclass + SettingsRegistry
  settings_match.py        # SettingsMatcher (fuzzy/natural language resolution)
  settings_validate.py     # SettingsValidator (type + bounds checking)
  presets.py               # PresetManager + BUILTIN_PRESETS
  web_auth.py              # Telegram initData HMAC-SHA256 validation
  web_api.py               # aiohttp HTTP API for Mini App
auto-slicer2.py            # thin entry point (argparse, app wiring)
webapp/
  index.html               # Mini App frontend (deployed to GitHub Pages)
tests/
  test_settings.py         # tests for registry, matcher, validator, presets
  test_web_api.py          # tests for web API pure helpers
  test_web_auth.py         # tests for Telegram initData validation
```

### Key Components

**Config** (`config.py`): Loads paths, defaults, and Telegram token from config.ini. Creates a `SettingsRegistry` at init time. Permission model: `allowed_users` from config.ini (empty = nobody allowed).

**SettingsRegistry** (`settings_registry.py`): Loads CuraEngine's fdmprinter.def.json, flattens the nested settings tree, follows the inherits chain (e.g. creality_ender3 → creality_base → fdmprinter), and builds label→key indexes.

**SettingsMatcher** (`settings_match.py`): Resolves user queries to setting keys via tiered matching: exact key, exact label, substring, then fuzzy (difflib). Used by the web API.

**SettingsValidator** (`settings_validate.py`): Type-checks and bounds-checks values for float, int, bool, enum, and str settings. Hard bounds reject; warning bounds accept with a warning. Used by the web API.

**PresetManager** (`presets.py`): Built-in presets (draft/standard/fine/strong) and optional custom presets from presets.json.

**Per-user settings**: `user_settings` dict in handlers.py stores overrides keyed by Telegram user ID (in-memory, resets on restart). Modified via the Mini App web API.

### Workflow

1. User configures settings via the Mini App (webapp)
2. User sends STL file as document
4. Bot downloads to temp directory
5. `slice_file()` invokes CuraEngine with merged settings (defaults + user overrides)
6. On success: archives STL+gcode to timestamped subfolder, notifies user with path
7. On failure: moves STL to `archive/errors/`, sends error message

## Configuration (config.ini)

- `[PATHS]`: archive_directory, cura_engine_path, definition_dir, printer_definition
- `[DEFAULT_SETTINGS]`: CuraEngine setting key-value pairs
- `[TELEGRAM]`: bot_token, allowed_users (comma-separated user IDs, empty = nobody), api_port, webapp_url, api_base_url

## Git Workflow

- **Always commit and push when you're done with a task.** Do not wait to be asked — committing and pushing is part of completing the work.
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

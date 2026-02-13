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
- `aiohttp` library (HTTP API for Mini App)
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
  defaults.py              # SETTINGS (Cura-style per-key config), PRESETS
  config.py                # Config class, permission checks
  slicer.py                # slice_file()
  handlers.py              # Telegram command handlers (start, help, webapp, reload, document)
  settings_registry.py     # SettingDefinition dataclass + SettingsRegistry
  settings_match.py        # resolve_setting() fuzzy/natural language resolution
  settings_validate.py     # validate() type + bounds checking
  settings_eval.py         # Expression evaluator (dependency graph, safe eval)
  presets.py               # load_presets() (BUILTIN_PRESETS re-exported from defaults.py)
  thumbnails.py            # OpenSCAD STL→PNG rendering + Klipper gcode thumbnail injection
  web_auth.py              # Telegram initData HMAC-SHA256 validation (legacy, not used for API auth)
  web_api.py               # aiohttp HTTP API for Mini App (ephemeral Bearer token auth)
auto-slicer2.py            # thin entry point (argparse, app wiring)
starred_keys.default.json  # default starred settings template (checked in)
webapp/
  index.html               # Mini App frontend (deployed to GitHub Pages)
tests/
  test_settings.py         # tests for registry, matcher, validator, presets, persistence
  test_slicer.py           # tests for slicer command building and settings merge
  test_eval.py             # tests for expression evaluator
  test_thumbnails.py       # tests for OpenSCAD thumbnail rendering and gcode injection
  test_web_api.py          # tests for web API helpers and endpoints
  test_web_auth.py         # tests for Telegram initData validation
```

### Key Components

**Defaults** (`defaults.py`): `SETTINGS` dict with Cura-style subkeys (`default_value`, `forced`, `maximum_value`, etc.) and `PRESETS`. Pure extractor functions derive flat dicts for config.py.

**Config** (`config.py`): Loads paths and Telegram token from config.ini, merges checked-in defaults from `defaults.py` with any config.ini overrides. Creates a `SettingsRegistry` at init time. Permission model: `allowed_users` from config.ini (empty = nobody allowed).

**SettingsRegistry** (`settings_registry.py`): Loads CuraEngine's fdmprinter.def.json, flattens the nested settings tree, follows the inherits chain (e.g. creality_ender3 → creality_base → fdmprinter), and builds label→key indexes.

**Settings matching** (`settings_match.py`): `resolve_setting()` resolves user queries to setting keys via tiered matching: exact key, exact label, substring, then fuzzy (difflib).

**Settings validation** (`settings_validate.py`): `validate()` type-checks and bounds-checks values for float, int, bool, enum, and str settings. Hard bounds reject; warning bounds accept with a warning.

**Expression evaluator** (`settings_eval.py`): Evaluates Cura's Python value expressions via restricted `eval()`. Builds a dependency graph from `value_expression` fields, topologically sorts, and evaluates in order. Used for webapp preview only — CuraEngine evaluates its own expressions at slice time. Exposed via `POST /api/evaluate`.

**Presets** (`presets.py`): Re-exports `BUILTIN_PRESETS` from `defaults.py` and provides `load_presets()` which merges in optional custom presets from presets.json.

**Per-user settings**: `user_settings` dict in handlers.py stores overrides keyed by Telegram user ID. File-backed via `user_settings.json` — persisted on every mutation, loaded on startup. Modified via the Mini App web API.

**Starred keys**: Globally shared set of "favorite" setting keys, shown in the Mini App's "Starred" tab. File-backed via `starred_keys.json` (gitignored, created from `starred_keys.default.json` template on first run). Any authenticated user can star/unstar settings via `POST /api/starred`.

**API authentication**: Ephemeral Bearer tokens with 30-minute sliding TTL. The `/webapp` bot command generates a random token, stores `(user_id, expiry)` in memory, and embeds it in the webapp URL. The frontend sends `Authorization: Bearer <token>` on all requests. The auth middleware validates tokens and refreshes expiry on each use. `/api/health` is the only endpoint that does not require a Bearer token. `web_auth.py` (initData HMAC validation) is retained but no longer used for API auth.

### Workflow

1. User configures settings via the Mini App (webapp)
2. User sends STL file as document
3. Bot downloads to temp directory
4. `slice_file()` invokes CuraEngine with merged settings (defaults + user overrides)
5. On success: archives STL+gcode to timestamped subfolder, notifies user with path
6. On failure: moves STL to `archive/errors/`, sends error message

## Configuration (config.ini)

- `[PATHS]`: archive_directory, cura_engine_path, definition_dir, printer_definition
- `[TELEGRAM]`: bot_token, allowed_users (comma-separated user IDs, empty = nobody), notify_chat_id, api_port, webapp_url, api_base_url

Slicer defaults and bounds overrides live in `auto_slicer/defaults.py` (version-controlled).
Optional `[DEFAULT_SETTINGS]` and `[BOUNDS_OVERRIDES]` sections in config.ini merge on top of the checked-in values.

## Git Workflow

- **Always commit and push when you're done with a task.** Do not wait to be asked — committing and pushing is part of completing the work.
- Create small, focused commits as you go so changes are easy to review and revert.
- Each commit should address a single concern (one bug fix, one feature, one refactor).
- Use a succinct imperative commit title (e.g. "Add retry logic for API calls").
- Include gotchas, caveats, or non-obvious side effects in the commit message body.
- Never add "Co-Authored-By" lines or email addresses to commit messages.
- Push freely without asking, but never use `git push --force` or any force-push variant.
- **Keep all documentation up to date.** When changing behavior, update CLAUDE.md and code comments in the same commit. Stale docs are worse than no docs.

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

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
- `/settings key=value ...` - Set per-user slicer overrides
- `/mysettings` - Show current user settings
- `/clear` - Reset to defaults

## Architecture

**Config class**: Loads paths, defaults, and Telegram token from config.ini

**Per-user settings**: `user_settings` dict stores overrides keyed by Telegram user ID (in-memory, resets on restart)

**Workflow**:

1. User sends `/settings layer_height=0.1` to set overrides
2. User sends STL file as document
3. Bot downloads to temp directory
4. `slice_file()` invokes CuraEngine with merged settings (defaults + user overrides)
5. On success: archives STL+gcode to timestamped subfolder, notifies user with path
6. On failure: moves STL to `archive/errors/`, sends error message

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

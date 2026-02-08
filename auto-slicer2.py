#!/usr/bin/env python

import argparse
import configparser

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from auto_slicer.config import Config
from auto_slicer.handlers import (
    start_command,
    help_command,
    settings_command,
    mysettings_command,
    clear_command,
    preset_command,
    reload_command,
    adduser_command,
    removeuser_command,
    listusers_command,
    post_init,
    handle_document,
)


def main():
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
    app.add_handler(CommandHandler("preset", preset_command))
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("listusers", listusers_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

#!/usr/bin/env python

import argparse
import configparser

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from auto_slicer.config import load_config
from auto_slicer.handlers import (
    start_command,
    help_command,
    webapp_command,
    reload_command,
    post_init,
    post_shutdown,
    handle_document,
)


def main():
    parser = argparse.ArgumentParser(description="Auto-slicer Telegram bot")
    parser.add_argument("-c", "--config", type=str, default="config.ini", help="Path to config file")
    args = parser.parse_args()

    config_file = configparser.ConfigParser()
    config_file.read(args.config)
    config = load_config(config_file)

    config.archive_dir.mkdir(parents=True, exist_ok=True)

    app = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("webapp", webapp_command))
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

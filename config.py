# config.py
# Configuration file for the bot.
# It's better to use environment variables in production.

import os
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# Telegram Bot Token from @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in the environment variables!")

# Your numeric Telegram user ID.
# You can get this from bots like @userinfobot
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID")
if not BOT_OWNER_ID:
    raise ValueError("BOT_OWNER_ID is not set in the environment variables!")

# The username (without '@') of the main channel users must join.
MAIN_CHANNEL_USERNAME = os.getenv("MAIN_CHANNEL_USERNAME", "Unix_Bots")

# Optional: A chat ID (can be a user, group, or channel) to send logs/errors to.
# The bot must be a member of this chat.
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")


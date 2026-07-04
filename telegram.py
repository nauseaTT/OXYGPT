import os
import asyncio

from telegram import TelegramBot

if __name__ == '__main__':
    API_ID = int(os.environ.get("TELEGRAM_API_ID", ""))
    API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_BOT_TOKEN must be set in environment or .env")

    bot_app = TelegramBot(api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    asyncio.run(bot_app.run())

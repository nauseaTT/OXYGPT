import os
import asyncio

# Load a local .env file when present so bare-metal runs (and `python
# telegram.py` inside the container) pick up credentials without extra steps.
# In Docker these usually come from `env_file:`/`-e`, but loading .env too is
# harmless and convenient. Missing python-dotenv is not fatal.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from telegram import TelegramBot

if __name__ == '__main__':
    # Validate the raw strings FIRST so a clear message is shown, rather than an
    # opaque `int('')` ValueError when TELEGRAM_API_ID is empty/unset.
    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not api_id_raw or not API_HASH or not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_BOT_TOKEN must be "
            "set in the environment or .env file."
        )

    try:
        API_ID = int(api_id_raw)
    except ValueError:
        raise RuntimeError(
            f"TELEGRAM_API_ID must be an integer, got {api_id_raw!r}."
        )

    bot_app = TelegramBot(api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    asyncio.run(bot_app.run())

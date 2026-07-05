import logging
import asyncio
import os
import shutil
from datetime import datetime
from telethon import TelegramClient

logger = logging.getLogger(__name__)

_BACKUP_TASK = None


async def register_handlers(client: TelegramClient) -> None:
    from .database import init_db
    from .handlers.entry import register_entry_handlers
    from .handlers.setup import register_setup_handlers
    from .handlers.template_builder import register_template_handlers
    from .handlers.live_form import register_live_form_handlers
    from .handlers.symbols import register_symbol_handlers
    from .handlers.settings import register_settings_handlers
    from .handlers.search import register_search_handlers
    from .handlers.bulk import register_bulk_handlers
    from .handlers.notifications import register_notification_handlers

    await init_db()
    register_entry_handlers(client)
    register_setup_handlers(client)
    register_template_handlers(client)
    register_live_form_handlers(client)
    register_symbol_handlers(client)
    register_settings_handlers(client)
    register_search_handlers(client)
    register_bulk_handlers(client)
    register_notification_handlers(client)

    _start_auto_backup(client)

    logger.info("Trade Journal handlers registered successfully")


def _start_auto_backup(client: TelegramClient) -> None:
    global _BACKUP_TASK

    async def _backup_loop():
        while True:
            try:
                await asyncio.sleep(3 * 3600)  # 3 hours
                try:
                    await _do_backup(client)
                except Exception as e:
                    logger.error(f"Backup error: {e}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Auto backup loop error: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 minutes before retrying

    try:
        _BACKUP_TASK = asyncio.ensure_future(_backup_loop())
    except RuntimeError:
        _BACKUP_TASK = None


async def _do_backup(client: TelegramClient) -> None:
    from .database import _DB_PATH, _get_conn
    import aiosqlite

    admin_id_env = os.environ.get("ADMIN_USER_ID")
    ADMIN_USER_ID = int(admin_id_env) if admin_id_env and admin_id_env.strip().lstrip('-').isdigit() else 8071301975

    if not os.path.exists(_DB_PATH):
        logger.warning("Database file not found for backup")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(__file__), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    backup_path = os.path.join(backup_dir, f"journal_backup_{timestamp}.db")
    
    try:
        try:
            source_conn = await asyncio.wait_for(aiosqlite.connect(_DB_PATH), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error("Database connection timeout during backup")
            return
            
        try:
            backup_conn = await asyncio.wait_for(aiosqlite.connect(backup_path), timeout=5.0)
        except asyncio.TimeoutError:
            await source_conn.close()
            logger.error("Backup file connection timeout")
            return

        try:
            await source_conn.backup(backup_conn)
            await backup_conn.close()
            await source_conn.close()
            logger.info(f"Database backup created: {backup_path}")
        except Exception as e:
            logger.error(f"Error during database backup: {e}")
            try:
                await backup_conn.close()
            except Exception:
                pass
            try:
                await source_conn.close()
            except Exception:
                pass
            return

        try:
            backup_size = os.path.getsize(backup_path)
            if backup_size > 45 * 1024 * 1024:
                logger.warning(f"Backup too large ({backup_size} bytes), skipping send")
                return
        except Exception as e:
            logger.warning(f"Could not check backup size: {e}")
            return

        max_retries = 2
        for attempt in range(max_retries):
            try:
                caption = (
                    f"📦 <b>Journal Database Backup</b>\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"💾 Size: {backup_size / 1024:.1f} KB"
                )
                
                await asyncio.wait_for(
                    client.send_file(
                        ADMIN_USER_ID,
                        backup_path,
                        caption=caption,
                        parse_mode="html",
                        thumb=None
                    ),
                    timeout=15.0
                )
                logger.info(f"Database backup sent to user {ADMIN_USER_ID} successfully")
                break
            except asyncio.TimeoutError:
                logger.warning(f"Backup send timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
            except Exception as e:
                if "Server" in str(e) and "older message" in str(e):
                    logger.warning(f"Server resent older message error (attempt {attempt+1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(3)
                else:
                    logger.error(f"Error sending backup (attempt {attempt+1}/{max_retries}): {e}")
                    break

        try:
            old_backups = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.db')],
                key=os.path.getctime
            )
            while len(old_backups) > 5:
                try:
                    os.remove(old_backups.pop(0))
                    logger.debug("Removed old backup file")
                except Exception as e:
                    logger.warning(f"Could not remove old backup: {e}")
        except Exception as e:
            logger.warning(f"Error cleaning up old backups: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in backup routine: {e}", exc_info=True)

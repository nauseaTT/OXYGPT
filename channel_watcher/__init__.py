"""Channel Watcher module — intelligent Telegram channel monitoring.

Each monitored channel runs in its own ``asyncio.Task`` that periodically
fetches new messages, analyses them with AI, and delivers summary cards
to the user's PM or a group.  Tasks are started/stopped dynamically as
monitors are created, paused, or deleted.

Module-level entry point::

    from channel_watcher import register_handlers
    await register_handlers(client, bot_instance)
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

# v2: `from telethon import TelegramClient` -> compat re-export (aliased to
# v2's `Client`). The user-session login below also uses v2's connect/
# request_login_code/sign_in flow instead of the removed `client.start(...)`.
from telethon_compat import TelegramClient

try:
    from zoneinfo import ZoneInfo
    tz_tehran = ZoneInfo("Asia/Tehran")
except ImportError:
    from datetime import timezone, timedelta
    tz_tehran = timezone(timedelta(hours=3, minutes=30))

logger = logging.getLogger(__name__)

_client: Optional[TelegramClient] = None
_bot: Any = None
_user_client: Optional[TelegramClient] = None

_monitor_tasks: Dict[int, asyncio.Task] = {}
_all_monitors_started: bool = False


# ── Analysis cycle (shared by scheduler and manual "check now") ───────


async def run_analysis_cycle(
    monitor: Dict[str, Any],
    client: TelegramClient,
    user_client: TelegramClient,
    bot_instance: Any,
    *,
    notify_empty: bool = False,
) -> Dict[str, Any]:
    """Run one fetch → classify → filter → analyse → notify cycle.

    Returns a status dict::

        {
            "status": "ok"|"empty_fetch"|"empty_filtered"|"empty_analysis"
                       |"limited"|"no_client",
            "sent": int,
            "fetched": int,   # new posts pulled from the channel
            "kept": int,      # posts that survived the importance filter
        }

    The three "empty_*" statuses distinguish *why* nothing was sent —
    this matters a lot for diagnosing "بررسی فوری میگه چیزی پیدا نشد"
    reports: ``empty_fetch`` means the channel genuinely has no new
    posts since the last check (or the fetch itself failed/returned
    nothing — see logs), while ``empty_filtered`` means new posts *were*
    found but the classifier judged none of them "high" importance
    (expected behaviour when ``importance_filter == "important"``, not
    a bug). Collapsing these into one generic "empty" made it
    impossible to tell the two apart from the UI.

    ``notify_empty`` controls whether a 'nothing new' outcome is treated
    as a soft result (used by the manual *Check now* button for feedback).
    Always advances ``last_checked_at`` on completion.
    """
    from . import database as db_mod
    from .services.fetcher import ChannelFetcher
    from .services.analyzer import ChannelAnalyzer, ChannelClassifier
    from .services.notifier import ChannelNotifier

    monitor_id = monitor["id"]
    uid = monitor["user_id"]
    now = datetime.now(tz_tehran)

    if not user_client:
        return {"status": "no_client", "sent": 0, "fetched": 0, "kept": 0}

    fetcher = ChannelFetcher(user_client)
    messages = await fetcher.fetch_new_messages(monitor, db_mod)
    fetched_count = len(messages)
    if not messages:
        await db_mod.set_last_checked(monitor_id, now.isoformat())
        return {"status": "empty_fetch", "sent": 0, "fetched": 0, "kept": 0}

    # Step 1 — cheap classifier (no user quota).
    classifier_model = bot_instance.db.get_setting(
        "cw_classifier_model", "gemini-3.1-flash-lite",
    )
    classifier = ChannelClassifier(
        bot_instance, classifier_model, monitor.get("system_prompt"),
    )
    messages = await classifier.classify_all(messages)

    # Step 2 — importance filter (fail-open on unclassified posts).
    imp_filter = monitor.get("importance_filter", "important")
    if imp_filter == "important":
        messages = [
            m for m in messages
            if m.get("classification", {}).get("importance") in ("high", None)
            or not m.get("classification")
        ]
        if not messages:
            await db_mod.set_last_checked(monitor_id, now.isoformat())
            return {"status": "empty_filtered", "sent": 0, "fetched": fetched_count, "kept": 0}

    kept_count = len(messages)

    # Step 3 — user rate limits (only the real analyzer counts quota).
    is_limited, _ = bot_instance.check_user_limits(uid)
    if is_limited:
        await ChannelNotifier(client).send_error_notification(
            uid, monitor_id,
            "❌ به دلیل پر شدن محدودیت اشتراک، تحلیل خودکار انجام نشد.",
        )
        await db_mod.set_last_checked(monitor_id, now.isoformat())
        return {"status": "limited", "sent": 0, "fetched": fetched_count, "kept": kept_count}

    # Step 4 — full AI analysis. Each message is analysed one at a time
    # with a delay in between (see ANALYZE_DELAY_SECONDS); we stream each
    # result to the user as soon as it's ready via ``on_analyzed`` instead
    # of waiting for the whole (potentially long, due to that delay)
    # batch to finish before sending anything.
    notifier = ChannelNotifier(client)

    async def _on_analyzed(analysis: Dict[str, Any], index: int, total: int) -> None:
        await notifier.send_analysis(uid, monitor, analysis, db_mod, index, total)

    analyzer = ChannelAnalyzer(bot_instance)
    analyses = await analyzer.analyze_messages(
        monitor, messages, db_mod, on_analyzed=_on_analyzed
    )
    await db_mod.set_last_checked(monitor_id, now.isoformat())
    if not analyses:
        return {"status": "empty_analysis", "sent": 0, "fetched": fetched_count, "kept": kept_count}

    return {"status": "ok", "sent": len(analyses), "fetched": fetched_count, "kept": kept_count}


# ── Scheduler worker ──────────────────────────────────────────────────


async def _monitor_worker(
    monitor_id: int,
    client: TelegramClient,
    user_client: TelegramClient,
    bot_instance: Any,
) -> None:
    """Background task for a single monitor.

    Checks every 60 seconds whether it's time to fetch and analyse
    new messages.  Exits when the monitor is deleted, paused, or
    deactivated.

    Receives *client*, *user_client*, and *bot_instance* directly
    (not via module globals) to avoid ``AttributeError`` when the
    globals haven't been set yet.
    """
    from . import database as db_mod

    uid = 0

    while True:
        try:
            await asyncio.sleep(60)

            if not user_client:
                logger.critical(
                    "Monitor %s: user client not available, stopping worker",
                    monitor_id,
                )
                break

            monitor = await db_mod.get_monitor(monitor_id)
            if not monitor:
                logger.info("Monitor %s deleted, stopping worker", monitor_id)
                break
            if not monitor["is_active"] or monitor["is_paused"]:
                logger.info("Monitor %s paused/inactive, stopping worker", monitor_id)
                break

            last = monitor.get("last_checked_at")
            now = datetime.now(tz_tehran)
            uid = monitor["user_id"]

            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    elapsed = (now - last_dt).total_seconds() / 3600
                    if elapsed < monitor["interval_hours"]:
                        continue
                except (ValueError, TypeError):
                    pass

            await run_analysis_cycle(monitor, client, user_client, bot_instance)

        except asyncio.CancelledError:
            logger.info("Monitor %s worker cancelled", monitor_id)
            break
        except Exception as exc:
            logger.exception("Error in monitor worker %s", monitor_id)
            try:
                if uid:
                    await ChannelNotifier(client).send_error_notification(
                        uid, monitor_id,
                        "❌ خطا در بررسی خودکار کانال.",
                    )
            except Exception:
                pass
            await asyncio.sleep(300)


# ── Task management ───────────────────────────────────────────────────


def start_monitor_task(monitor_id: int, client: TelegramClient, user_client: TelegramClient, bot_instance: Any) -> None:
    """Start a background worker for *monitor_id* if not already running."""
    if monitor_id in _monitor_tasks and not _monitor_tasks[monitor_id].done():
        return
    task = asyncio.create_task(_monitor_worker(monitor_id, client, user_client, bot_instance))
    _monitor_tasks[monitor_id] = task
    logger.info("Started monitor task %s", monitor_id)


def stop_monitor_task(monitor_id: int) -> None:
    """Cancel the background worker for *monitor_id* if running."""
    task = _monitor_tasks.pop(monitor_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("Stopped monitor task %s", monitor_id)


async def _cleanup_worker() -> None:
    """Daily cleanup of old analysed messages (90-day retention)."""
    while True:
        try:
            await asyncio.sleep(86400)  # every 24 hours
            from .database import cleanup_old_analyses
            await cleanup_old_analyses(retention_days=90)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Cleanup worker error: %s", exc)


def _start_all_monitors() -> None:
    """Start workers for every active (non-paused) monitor in the database."""
    global _all_monitors_started
    if _all_monitors_started:
        return

    async def _init():
        global _all_monitors_started
        if not _user_client:
            logger.warning("No user client — cannot start monitor workers")
            _all_monitors_started = True
            return
        from .database import get_active_monitors
        monitors = await get_active_monitors()
        for m in monitors:
            start_monitor_task(m["id"], _client, _user_client, _bot)
        logger.info("Started %d monitor worker(s)", len(monitors))

        asyncio.create_task(_cleanup_worker())
        _all_monitors_started = True

    try:
        asyncio.create_task(_init())
    except RuntimeError:
        logger.warning("No event loop running — monitor tasks deferred")


# ── Module entry point ────────────────────────────────────────────────


async def register_handlers(client: TelegramClient, bot_instance: Any, user_client: Optional[TelegramClient] = None) -> None:
    """Register all Channel Watcher handlers and start the scheduler.

    Call this from ``TelegramBot.run()``, after the trade journal module
    is loaded::

        from channel_watcher import register_handlers as register_cw
        await register_cw(self.bot, self)

    Args:
        client: The Telethon ``TelegramClient`` instance.
        bot_instance: The ``TelegramBot`` instance (for subscription checks,
            AI sessions, and usage tracking).
        user_client: Optional user-session ``TelegramClient`` for channel
            reading.  If not provided one is created from env vars.
    """
    global _client, _bot, _user_client
    _client = client
    _bot = bot_instance

    if user_client is not None:
        _user_client = user_client
    else:
        try:
            api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
            api_hash = os.environ.get("TELEGRAM_API_HASH", "")
            if not api_id or not api_hash:
                raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
            phone = os.environ.get("CW_USER_PHONE", "")
            if not phone:
                raise ValueError("CW_USER_PHONE must be set in .env")
            _user_client = TelegramClient("channel_watcher_user", api_id, api_hash)
            # v2: `client.start(phone=, code_callback=)` was removed. The
            #     user-account login is now an explicit flow:
            #       connect() -> is_authorized()?  -> request_login_code(phone)
            #       -> sign_in(token, code)  [-> check_password() if 2FA].
            #     An already-authorised .session skips straight past sign-in, so
            #     interactive input is only requested on a genuine first login.
            #     NOTE: existing v1 .session files are NOT compatible with v2 and
            #     must be re-created once (a fresh code prompt on first v2 run).
            await _user_client.connect()
            if not await _user_client.is_authorized():
                # v2: request a login code, then read it interactively (same UX
                #     as v1's `code_callback`).
                login_token = await _user_client.request_login_code(phone)
                code = input("🔑 Telegram code for Channel Watcher: ").strip()
                result = await _user_client.sign_in(login_token, code)
                # v2: `sign_in` returns a `PasswordToken` instead of a `User`
                #     when the account has 2FA enabled; complete it with the
                #     password from the environment (falls back to a prompt).
                from telethon._impl.client.types.password_token import PasswordToken
                if isinstance(result, PasswordToken):
                    password = os.environ.get("CW_USER_PASSWORD", "") or input(
                        "🔒 2FA password for Channel Watcher: "
                    )
                    await _user_client.check_password(result, password)
            logger.info("Channel Watcher user client started successfully")
        except Exception as exc:
            logger.critical("Failed to start Channel Watcher user client: %s", exc)
            _user_client = None

    from .database import init_db
    await init_db()

    from .handlers.setup import register_setup_handlers
    from .handlers.settings import register_settings_handlers
    from .handlers.callbacks import register_callback_handlers

    register_setup_handlers(client, bot_instance, _user_client)
    register_settings_handlers(client, bot_instance, _user_client)
    register_callback_handlers(client, bot_instance)

    _start_all_monitors()

    logger.info("Channel Watcher module registered successfully")

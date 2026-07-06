"""Settings panel handler — view and modify monitor configuration.

Supports interval (paid), system prompt (paid), importance filter (paid),
pause/resume, activity stats, and monitor deletion with two-step confirm.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

# v2: `from telethon import TelegramClient, events, Button` -> compat re-exports
# plus `filters`/`data_regex` for the split event+filter registration model.
from telethon_compat import TelegramClient, events, Button, filters, data_regex

from ..database import (
    get_monitor,
    update_monitor,
    delete_monitor,
    toggle_pause,
    get_stats,
    get_hourly_distribution,
)
from ..states import UserState, get_state_manager
from ..ui.keyboards import (
    settings_keyboard,
    back_keyboard,
    delete_confirm_keyboard,
    stats_back_keyboard,
    empty_monitors_keyboard,
    interval_presets_keyboard,
    history_keyboard,
)
from ..database import get_analyses
from ..ui.formatter import (
    format_settings_text,
    format_stats_text,
    format_history_text,
)
from ..ui.safe_edit import safe_edit

try:
    from zoneinfo import ZoneInfo
    tz_tehran = ZoneInfo("Asia/Tehran")
except ImportError:
    from datetime import timezone, timedelta
    tz_tehran = timezone(timedelta(hours=3, minutes=30))

logger = logging.getLogger(__name__)

_state_mgr = get_state_manager()

# Module-level references set during registration
_client: Optional[TelegramClient] = None
_bot: Any = None
_user_client: Optional[TelegramClient] = None


def _extract_id(data: bytes) -> int:
    try:
        parts = data.decode().split("_")
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


async def _owned_monitor(event) -> Optional[dict]:
    """Fetch the monitor referenced by the callback, enforcing ownership.

    Returns the monitor dict when it exists and belongs to the caller,
    otherwise answers the callback with an alert and returns ``None``.
    Every settings action MUST route through this to prevent one user
    from acting on another user's monitor (IDOR).
    """
    monitor_id = _extract_id(event.data)
    if not monitor_id:
        await event.answer("داده نامعتبر.", alert=True)
        return None
    monitor = await get_monitor(monitor_id)
    if not monitor or not monitor.get("is_active"):
        await event.answer("مونیتور یافت نشد.", alert=True)
        return None
    if monitor["user_id"] != event.sender_id:
        await event.answer("⛔️ دسترسی غیرمجاز!", alert=True)
        return None
    return monitor


# ── Exported handlers (callable from pending_message_handler) ────────


async def cw_set_interval_save(event) -> None:
    """Called from pending_message_handler when user types an interval value."""
    uid = event.sender_id
    state, data = await _state_mgr.get_state(uid)
    if state != UserState.AWAITING_INTERVAL or not data:
        return
    text = (event.text or "").strip()
    try:
        hours = int(text)
        if hours < 1 or hours > 24:
            raise ValueError
    except (ValueError, TypeError):
        await event.reply(
            "❌ عدد نامعتبر. لطفاً یک عدد بین ۱ تا ۲۴ وارد کنید.",
            buttons=back_keyboard(f"cw_settings_{data.get('monitor_id', 0)}"),
        )
        return
    monitor_id = data["monitor_id"]
    await update_monitor(monitor_id, interval_hours=hours)
    await _state_mgr.clear_state(uid)
    await event.reply(
        f"✅ بازه تحلیل به {hours} ساعت تغییر یافت.",
        buttons=[[Button.inline("⚙️ برگشت به تنظیمات", f"cw_settings_{monitor_id}")]],
    )


async def cw_set_prompt_save(event) -> None:
    """Save the user's free-text interests (sanitised) for a monitor."""
    uid = event.sender_id
    state, data = await _state_mgr.get_state(uid)
    if state != UserState.AWAITING_SYSTEM_PROMPT or not data:
        return
    from ..services.analyzer import sanitize_interests
    raw = (event.text or "").strip()
    cleaned = None if raw in ("-", "−", "خالی") else sanitize_interests(raw)
    monitor_id = data["monitor_id"]
    await update_monitor(monitor_id, system_prompt=cleaned)
    await _state_mgr.clear_state(uid)
    if cleaned:
        msg = (
            "✅ علایق شما ذخیره شد. از این پس تحلیل‌ها با تمرکز روی این موضوعات "
            "انجام می‌شود.\n\n"
            f"🎯 <b>علایق ثبت‌شده:</b>\n<blockquote>{cleaned}</blockquote>"
        )
    else:
        msg = "🗑 علایق پاک شد. تحلیل‌ها به حالت پیش‌فرض (عمومی) برمی‌گردند."
    await event.reply(
        msg,
        buttons=[[Button.inline("⚙️ برگشت به تنظیمات", f"cw_settings_{monitor_id}")]],
        parse_mode="html",
    )


# ── Handler registration ──────────────────────────────────────────────


def register_settings_handlers(client: TelegramClient, bot_instance: Any, user_client: Optional[TelegramClient] = None) -> None:
    """Register all settings-related callback handlers."""
    global _client, _bot, _user_client
    _client = client
    _bot = bot_instance
    _user_client = user_client

    SHOW_SETTINGS_RE = r"^cw_settings_\d+$"
    INTERVAL_RE = r"^cw_interval_\d+$"
    PROMPT_RE = r"^cw_prompt_\d+$"
    IMPORTANCE_RE = r"^cw_importance_\d+$"
    PAUSE_RE = r"^cw_toggle_pause_\d+$"
    DELETE_REQ_RE = r"^cw_delete_\d+$"
    DELETE_CONF_RE = r"^cw_delete_confirm_\d+$"
    STATS_RE = r"^cw_stats_\d+$"

    @client.on(events.ButtonCallback, data_regex(SHOW_SETTINGS_RE))
    async def cw_show_settings(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        uid = event.sender_id
        sub_type = _bot.get_user_subscription(uid)
        last_check = monitor.get("last_checked_at")
        if last_check:
            try:
                last_dt = datetime.fromisoformat(last_check)
                next_dt = last_dt + timedelta(hours=monitor["interval_hours"])
                next_check = next_dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                next_check = "به زودی"
        else:
            next_check = "به زودی"

        text = format_settings_text(monitor, next_check)
        kb = settings_keyboard(monitor, sub_type)
        await safe_edit(event, text, buttons=kb, parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(INTERVAL_RE))
    async def cw_set_interval_start(event):
        """Show quick interval presets (paid)."""
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        if _bot.get_user_subscription(event.sender_id) != "paid":
            await event.answer("این قابلیت نیاز به اشتراک پولی دارد.", alert=True)
            return
        monitor_id = monitor["id"]
        current = monitor.get("interval_hours", 4)
        text = (
            "🕒 <b>بازه‌ی بررسی کانال را انتخاب کنید</b>\n\n"
            "هر چند وقت یک‌بار کانال برای پیام‌های جدید بررسی شود؟\n"
            f"مقدار فعلی: <b>هر {current} ساعت</b>"
        )
        await safe_edit(event, text, buttons=interval_presets_keyboard(monitor_id, current), parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(r"^cw_setint_\d+_\d+$"))
    async def cw_set_interval_preset(event):
        """Apply a one-tap interval preset."""
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        if _bot.get_user_subscription(event.sender_id) != "paid":
            await event.answer("این قابلیت نیاز به اشتراک پولی دارد.", alert=True)
            return
        try:
            parts = event.data.decode().split("_")
            monitor_id, hours = int(parts[2]), int(parts[3])
        except (IndexError, ValueError):
            await event.answer("داده نامعتبر.", alert=True)
            return
        if not 1 <= hours <= 24:
            await event.answer("مقدار نامعتبر.", alert=True)
            return
        await update_monitor(monitor_id, interval_hours=hours)
        await event.answer(f"✅ بازه به هر {hours} ساعت تغییر یافت.", alert=False)
        monitor["interval_hours"] = hours
        await safe_edit(
            event,
            "🕒 <b>بازه‌ی بررسی به‌روزرسانی شد</b>",
            buttons=interval_presets_keyboard(monitor_id, hours),
            parse_mode="html",
            fallback_reply=False,
        )

    @client.on(events.ButtonCallback, data_regex(r"^cw_interval_custom_\d+$"))
    async def cw_set_interval_custom(event):
        """Fall back to typing a custom interval value."""
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        if _bot.get_user_subscription(event.sender_id) != "paid":
            await event.answer("این قابلیت نیاز به اشتراک پولی دارد.", alert=True)
            return
        monitor_id = monitor["id"]
        await _state_mgr.set_state(event.sender_id, UserState.AWAITING_INTERVAL, {"monitor_id": monitor_id})
        text = (
            "✏️ <b>عدد بازه را وارد کنید</b>\n\n"
            "یک عدد بین ۱ تا ۲۴ (به ساعت) بفرستید.\n"
            "برای لغو، /cancel را بزنید."
        )
        await safe_edit(event, text, buttons=back_keyboard(f"cw_settings_{monitor_id}"), parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(PROMPT_RE))
    async def cw_set_prompt_start(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        uid = event.sender_id
        if _bot.get_user_subscription(uid) != "paid":
            await event.answer("این قابلیت نیاز به اشتراک پولی دارد.", alert=True)
            return
        monitor_id = monitor["id"]
        await _state_mgr.set_state(uid, UserState.AWAITING_SYSTEM_PROMPT, {"monitor_id": monitor_id})
        current = (monitor.get("system_prompt") or "").strip()
        current_block = (
            f"\n🎯 <b>علایق فعلی:</b>\n<blockquote>{current}</blockquote>\n"
            if current else ""
        )
        guide = (
            "🎯 <b>علایق شما</b>\n\n"
            "اینجا بنویسید چه <b>موضوعاتی</b> برایتان مهم‌تر است تا هوش مصنوعی "
            "هنگام خلاصه‌سازی و تعیین اهمیت پست‌ها، روی همان‌ها تمرکز کند.\n\n"
            "💡 <b>برای بهترین نتیجه:</b>\n"
            "• موضوعات را ساده و شفاف بنویسید، نه دستور به ربات.\n"
            "• چند کلیدواژه یا یک جمله‌ی کوتاه کافی است.\n"
            "• می‌توانید بگویید به چه چیزهایی <b>علاقه دارید</b> و چه چیزهایی <b>برایتان مهم نیست</b>.\n\n"
            "✅ <b>مثال خوب:</b>\n"
            "<i>«سیگنال‌ها و تحلیل‌های تکنیکال ارز دیجیتال مهم‌اند؛ "
            "تبلیغات و اخبار عمومی برایم اهمیتی ندارند.»</i>\n\n"
            f"{current_block}"
            "متن علایق خود را بفرستید (حداکثر ۵۰۰ کاراکتر).\n"
            "برای پاک کردن علایق، یک «-» بفرستید. برای لغو، /cancel."
        )
        await safe_edit(event, guide, buttons=back_keyboard(f"cw_settings_{monitor_id}"), parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(IMPORTANCE_RE))
    async def cw_toggle_importance(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        if _bot.get_user_subscription(event.sender_id) != "paid":
            await event.answer("این قابلیت نیاز به اشتراک پولی دارد.", alert=True)
            return
        monitor_id = monitor["id"]
        new_value = "all" if monitor["importance_filter"] == "important" else "important"
        await update_monitor(monitor_id, importance_filter=new_value)
        monitor["importance_filter"] = new_value
        sub_type = _bot.get_user_subscription(event.sender_id)
        await safe_edit(
            event,
            format_settings_text(monitor, "به‌روزرسانی شد"),
            buttons=settings_keyboard(monitor, sub_type),
            parse_mode="html",
            fallback_reply=False,
        )

    @client.on(events.ButtonCallback, data_regex(PAUSE_RE))
    async def cw_toggle_pause(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        monitor_id = monitor["id"]
        new_state = await toggle_pause(monitor_id)
        await event.answer("پایش متوقف شد ⏸" if new_state else "پایش فعال شد ✅", alert=True)

        from ..__init__ import stop_monitor_task, start_monitor_task
        if new_state:
            stop_monitor_task(monitor_id)
        else:
            start_monitor_task(monitor_id, _client, _user_client, _bot)

        monitor = await get_monitor(monitor_id)
        if monitor:
            uid = event.sender_id
            sub_type = _bot.get_user_subscription(uid)
            await safe_edit(
                event,
                format_settings_text(monitor, "متوقف" if new_state else "فعال"),
                buttons=settings_keyboard(monitor, sub_type),
                parse_mode="html",
                fallback_reply=False,
            )

    @client.on(events.ButtonCallback, data_regex(DELETE_REQ_RE))
    async def cw_delete_request(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        uid = event.sender_id
        monitor_id = monitor["id"]
        await _state_mgr.set_state(uid, UserState.AWAITING_MONITOR_DELETE, {"monitor_id": monitor_id})
        await safe_edit(
            event,
            f"⚠️ <b>آیا از حذف مونیتور مطمئن هستید؟</b>\n\n"
            f"📡 {monitor.get('channel_title', '?')}\n\n"
            "تمام تاریخچه تحلیل‌های این کانال نیز حذف خواهد شد.",
            buttons=delete_confirm_keyboard(monitor_id),
            parse_mode="html",
        )

    @client.on(events.ButtonCallback, data_regex(DELETE_CONF_RE))
    async def cw_delete_confirm(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        uid = event.sender_id
        monitor_id = monitor["id"]
        await delete_monitor(monitor_id)
        await _state_mgr.clear_state(uid)
        from ..__init__ import stop_monitor_task
        stop_monitor_task(monitor_id)
        await safe_edit(event, "✅ مونیتور با موفقیت حذف شد.", buttons=empty_monitors_keyboard())

    @client.on(events.ButtonCallback, data_regex(STATS_RE))
    async def cw_show_stats(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        monitor_id = monitor["id"]
        stats = await get_stats(monitor_id, days=7)
        hourly = await get_hourly_distribution(monitor_id, days=7)
        text = format_stats_text(
            monitor_title=monitor.get("channel_title", ""),
            stats=stats,
            hourly_dist=hourly,
        )
        await safe_edit(event, text, buttons=stats_back_keyboard(monitor_id), parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(r"^cw_history_\d+$"))
    async def cw_show_history(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        monitor_id = monitor["id"]
        analyses = await get_analyses(monitor_id, limit=10)
        text = format_history_text(monitor.get("channel_title", ""), analyses)
        await safe_edit(event, text, buttons=history_keyboard(monitor_id), parse_mode="html")

    @client.on(events.ButtonCallback, data_regex(r"^cw_check_now_\d+$"))
    async def cw_check_now(event):
        monitor = await _owned_monitor(event)
        if not monitor:
            return
        # Paid-tier only. The keyboard already hides this button for free
        # users (showing a locked entry instead), but a free user could
        # still replay/forge the callback data directly, so enforce it
        # here too.
        if _bot.get_user_subscription(event.sender_id) != "paid":
            await event.answer("🔒 «بررسی فوری» فقط برای کاربران دارای اشتراک پولی است.", alert=True)
            return
        monitor_id = monitor["id"]
        if monitor.get("is_paused"):
            await event.answer("پایش این کانال متوقف است. ابتدا آن را فعال کنید.", alert=True)
            return
        await event.answer("🔄 در حال بررسی کانال… نتیجه به‌زودی ارسال می‌شود.", alert=False)
        try:
            from ..__init__ import run_analysis_cycle
            result = await run_analysis_cycle(monitor, _client, _user_client, _bot)
        except Exception:
            logger.exception("Manual check failed for monitor %s", monitor_id)
            result = {"status": "error", "sent": 0}

        status = result.get("status")
        sent = result.get("sent", 0)
        fetched = result.get("fetched", 0)
        kept = result.get("kept", 0)
        if status == "ok":
            note = f"✅ بررسی انجام شد — {sent} تحلیل جدید ارسال شد. (از {fetched} پیام جدید)"
        elif status == "empty_fetch":
            note = (
                "ℹ️ بررسی انجام شد — هیچ پیام جدیدی در کانال از آخرین بررسی پیدا نشد.\n"
                "اگر مطمئنید پیام تازه‌ای در کانال هست ولی باز هم اینجا نمی‌آید، "
                "احتمالاً مشکل در خواندن کانال است (نه فیلتر اهمیت) — با پشتیبانی تماس بگیرید."
            )
        elif status == "empty_filtered":
            note = (
                f"✅ بررسی انجام شد — {fetched} پیام جدید پیدا شد، اما هوش مصنوعی "
                "هیچ‌کدام را «مهم» تشخیص نداد (فیلتر فعلی: فقط پیام‌های مهم).\n"
                "برای دیدن همه‌ی پیام‌ها بدون فیلتر، «فیلتر اهمیت» را روی «همه پیام‌ها» بگذارید."
            )
        elif status == "empty_analysis":
            note = (
                f"⚠️ بررسی انجام شد — {kept} پیام مهم پیدا شد، اما تحلیل هوش مصنوعی برای "
                "هیچ‌کدام کامل نشد (خطای موقت مدل). دوباره تلاش کنید."
            )
        elif status == "limited":
            note = "⚠️ محدودیت اشتراک شما پر شده؛ تحلیل انجام نشد."
        elif status == "no_client":
            note = "⚠️ سرویس خواندن کانال در دسترس نیست. با پشتیبانی تماس بگیرید."
        else:
            note = "❌ در بررسی کانال خطایی رخ داد."

        # Refresh the settings view with a status line.
        sub_type = _bot.get_user_subscription(event.sender_id)
        fresh = await get_monitor(monitor_id)
        if not fresh:
            return
        last_check = fresh.get("last_checked_at")
        if last_check:
            try:
                next_dt = datetime.fromisoformat(last_check) + timedelta(hours=fresh["interval_hours"])
                next_check = next_dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                next_check = "به زودی"
        else:
            next_check = "به زودی"
        text = f"{note}\n\n" + format_settings_text(fresh, next_check)
        await safe_edit(event, text, buttons=settings_keyboard(fresh, sub_type), parse_mode="html")

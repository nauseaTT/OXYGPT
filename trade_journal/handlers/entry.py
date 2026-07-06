import logging
import asyncio
from typing import Any, List, Optional, Dict

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
# The compat layer aliases `TelegramClient` to v2's `Client`, provides the
# v2 `events` module, `filters`, and the `data_regex` helper that reproduces
# v1's `events.CallbackQuery(pattern=...)` regex-matching semantics.
from telethon_compat import TelegramClient, events, Button, filters, data_regex, photo_dedup_key

from .. import database as db
from ..states import (
    get_state, get_state_str, get_state_data, set_state, update_state_data,
    clear_state, IDLE, FILLING_FORM, AWAIT_TEXT, AWAIT_PHOTO,
    AWAIT_CHANNEL_FORWARD, AWAIT_TEMPLATE_NAME, AWAIT_FIELD_LABEL,
    AWAIT_SYMBOL_NAME, SHOWING_TEMPLATES, SHOWING_SYMBOLS, SHOWING_SETTINGS,
    AWAIT_MARGIN
)
from ..ui.keyboards import (
    journal_reply_keyboard, exit_reply_keyboard, group_inline_keyboard,
    template_select_keyboard, journal_inline_panel, channel_post_keyboard
)
from ..services.channel_service import check_channel_validity, delete_channel_message
from ..utils import auto_delete_message

logger = logging.getLogger(__name__)

WAIT_STATES = {
    AWAIT_CHANNEL_FORWARD, AWAIT_TEXT, AWAIT_PHOTO,
    AWAIT_TEMPLATE_NAME, AWAIT_FIELD_LABEL, AWAIT_SYMBOL_NAME, AWAIT_MARGIN
}

_user_locks = {}

def get_user_lock(uid: int) -> asyncio.Lock:
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]


def _get_callback_parts(event: Any) -> List[str]:
    data = getattr(event, "data", None)
    if not data:
        return []
    try:
        s = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        return []
    return s.split(":")


def _parse_callback_int(event: Any, idx: int = 1) -> Optional[int]:
    parts = _get_callback_parts(event)
    if len(parts) <= idx:
        return None
    try:
        return int(parts[idx])
    except Exception:
        return None


def register_entry_handlers(client: TelegramClient) -> None:
    client.on(events.ButtonCallback, filters.Data(b"tj_panel"))(_handle_tj_panel)
    client.on(events.ButtonCallback, filters.Data(b"trading_ai_panel"))(_handle_trading_ai_panel)
    client.on(events.ButtonCallback, filters.Data(b"tj_back_journal"))(_handle_back_journal)
    client.on(events.ButtonCallback, filters.Data(b"tj_new_trade"))(_handle_new_trade)
    client.on(events.ButtonCallback, filters.Data(b"tj_templates"))(_handle_templates)
    client.on(events.ButtonCallback, filters.Data(b"tj_symbols"))(_handle_symbols_menu)
    client.on(events.ButtonCallback, filters.Data(b"tj_settings"))(_handle_settings_menu)
    client.on(events.ButtonCallback, filters.Data(b"tj_exit"))(_handle_exit)
    client.on(events.ButtonCallback, filters.Data(b"tj_export"))(_handle_export)
    client.on(events.ButtonCallback, filters.Data(b"tj_trades_list"))(_handle_trades_list)
    client.on(events.ButtonCallback, data_regex(r"tj_trade_view:"))(_handle_trade_view)
    client.on(events.ButtonCallback, data_regex(r"tj_trade_del:"))(_handle_trade_delete)
    client.on(events.ButtonCallback, data_regex(r"tj_confirm_delete_trade:"))(_handle_trade_confirm_delete)
    client.on(events.ButtonCallback, data_regex(r"tj_trades_page:"))(_handle_trades_page)
    client.on(events.ButtonCallback, data_regex(r"tj_trade_edit:"))(_handle_trade_edit)
    client.on(events.ButtonCallback, filters.Data(b"tj_group_stats"))(_handle_group_stats)
    client.on(events.ButtonCallback, filters.Data(b"tj_group_quick_trade"))(_handle_group_quick_trade)
    client.on(events.ButtonCallback, filters.Data(b"tj_group_templates"))(_handle_group_templates)
    client.on(events.ButtonCallback, filters.Data(b"tj_group_search"))(_handle_group_search)
    client.on(events.ButtonCallback, filters.Data(b"tj_group_help"))(_handle_group_help)
    client.on(events.ButtonCallback, filters.Data(b"tj_detailed_analysis"))(_handle_detailed_analysis)
    client.on(events.ButtonCallback, data_regex(r"tj_ch_edit:"))(_handle_channel_edit)
    client.on(events.ButtonCallback, data_regex(r"tj_ch_delete:"))(_handle_channel_delete)
    client.on(events.ButtonCallback, data_regex(r"tj_ch_analysis:"))(_handle_channel_analysis)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_header"))(_handle_placeholder)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_section"))(_handle_placeholder)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_limit"))(_handle_placeholder)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_default"))(_handle_placeholder)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_trade_info"))(_handle_placeholder)
    client.on(events.ButtonCallback, filters.Data(b"placeholder_photo"))(_handle_placeholder)
    # v2: `events.NewMessage(incoming=True)` (event+filter fused in one call) ->
    #     `events.NewMessage` as the event type plus a separate `filters.Incoming()`
    #     filter. v2 requires the event class and the filter as two arguments to
    #     `client.on(...)`; the `NewMessage` class has no public constructor.
    client.on(events.NewMessage, filters.Incoming())(_handle_message_router)


async def _handle_placeholder(event: Any) -> None:
    await event.answer()


async def _handle_trading_ai_panel(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    try:
        if event.is_private:
            await event.edit("🔧 <b>ژورنال معاملات</b>\n\nاین بخش در دست تعمیر است و به زودی باز خواهد گشت.", parse_mode="html")
        else:
            await event.edit("🔧 <b>ژورنال معاملات</b>\n\nاین بخش در دست تعمیر است و به زودی باز خواهد گشت.", parse_mode="html")
        return
    except Exception as e:
        await event.answer("این بخش در دست تعمیر است.", alert=True)
        logger.error(f"Error in _handle_trading_ai_panel: {e}")

    if not event.is_private:
        await db.ensure_user(uid)
        user = await db.get_user(uid)
        if not user or not user.get("channel_id"):
            try:
                me = await event.client.get_me()
                bot_username = me.username or "Bot"
                msg = await event.respond(
                    "📊 <b>ژورنال معاملات</b>\n\n"
                    "شما باید ابتدا در پیوی ربات ست‌اپ کنید.",
                    buttons=[[Button.url("📨 باز کردن ربات", f"https://t.me/{bot_username}")]],
                    parse_mode="html"
                )
                asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
            except Exception as e:
                logger.error(f"Error sending group redirect: {e}")
            return
        clear_state(uid)
        is_valid, msg = await check_channel_validity(event.client, user["channel_id"])
        if not is_valid:
            await event.respond(
                f"⚠️ <b>خطا در اتصال کانال</b>\n\n{msg}",
                parse_mode="html"
            )
            return
        await _show_journal_panel_group(event, uid)
        return

    await db.ensure_user(uid)
    clear_state(uid)

    user = await db.get_user(uid)
    if not user or not user.get("channel_id"):
        await _prompt_channel_setup(event, uid)
        return

    is_valid, msg = await check_channel_validity(event.client, user["channel_id"])
    if not is_valid:
        await event.edit(
            f"⚠️ <b>خطا در اتصال کانال</b>\n\n{msg}\n\n"
            "لطفاً کانال را مجدداً تنظیم کنید:",
                buttons=[[Button.inline("🔄 تنظیم کانال", b"tj_settings_channel")],
                         [Button.inline("🔙 بازگشت", b"tj_back_journal")]],
            parse_mode="html"
        )
        return

    await _show_journal_panel_inline(event, uid)


async def _cleanup_old_messages(event: Any, uid: int, keep_last: int = 3) -> None:
    state = get_state(uid)
    msg_ids = state.get("data", {}).get("_message_ids", [])
    if len(msg_ids) > keep_last:
        to_delete = msg_ids[:-keep_last]
        for mid in to_delete:
            try:
                await event.client.delete_messages(uid, mid)
            except Exception:
                pass
        state["data"]["_message_ids"] = msg_ids[-keep_last:]
        update_state_data(uid, _message_ids=state["data"]["_message_ids"])


async def _track_message_id(uid: int, msg_id: int) -> None:
    state = get_state(uid)
    msg_ids = state.get("data", {}).get("_message_ids", [])
    msg_ids.append(msg_id)
    if len(msg_ids) > 10:
        msg_ids = msg_ids[-10:]
    update_state_data(uid, _message_ids=msg_ids)


async def _show_journal_panel_inline(event: Any, uid: int) -> None:
    from ..ui.design_system import Box
    buttons = journal_inline_panel()
    try:
        await event.edit(
            f"{Box.TL}{Box.H * 30}{Box.TR}\n"
            f"{Box.V}  📊 <b>ژورنال معاملات</b>  {Box.V}\n"
            f"{Box.BL}{Box.H * 30}{Box.BR}\n\n"
            "یک گزینه انتخاب کنید:",
            buttons=buttons, parse_mode="html"
        )
    except Exception:
        await event.respond(
            f"{Box.TL}{Box.H * 30}{Box.TR}\n"
            f"{Box.V}  📊 <b>ژورنال معاملات</b>  {Box.V}\n"
            f"{Box.BL}{Box.H * 30}{Box.BR}\n\n"
            "یک گزینه انتخاب کنید:",
            buttons=buttons, parse_mode="html"
        )


async def _show_journal_panel_group(event: Any, uid: int) -> None:
    from ..ui.design_system import Box
    buttons = journal_inline_panel()
    try:
        await _cleanup_old_messages(event, uid)
        privacy = await db.get_privacy(uid)
        mode = privacy.get("mode", "off")
        priv_note = ""
        if mode != "off":
            level_map = {"low": "🟨 راحت", "medium": "🟠 متوسط", "high": "🔴 شدید"}
            priv_note = "\n🛡 حریم خصوصی: " + level_map.get(mode, "متوسط")
        msg = await event.respond(
            f"{Box.TL}{Box.H * 30}{Box.TR}\n"
            f"{Box.V}  📊 <b>ژورنال معاملات</b>  {Box.V}\n"
            f"{Box.BL}{Box.H * 30}{Box.BR}"
            f"{priv_note}\n\n"
            "یک گزینه انتخاب کنید:",
            buttons=buttons, parse_mode="html"
        )
        if msg:
            await _track_message_id(uid, msg.id)
    except Exception as e:
        logger.error(f"Error showing group panel: {e}")


async def _handle_tj_panel(event: Any) -> None:
    uid = event.sender_id

    if not event.is_private:
        await event.answer()
        await db.ensure_user(uid)
        user = await db.get_user(uid)
        if not user or not user.get("channel_id"):
            try:
                me = await event.client.get_me()
                bot_username = me.username or "Bot"
                msg = await event.respond(
                    "📊 <b>ژورنال معاملات</b>\n\n"
                    "شما باید ابتدا در پیوی ربات ست‌اپ کنید.",
                    buttons=[[Button.url("📨 باز کردن ربات", f"https://t.me/{bot_username}")]],
                    parse_mode="html"
                )
                asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
            except Exception as e:
                logger.error(f"Error sending group redirect: {e}")
            return
        clear_state(uid)
        is_valid, msg = await check_channel_validity(event.client, user["channel_id"])
        if not is_valid:
            msg = await event.respond(
                f"⚠️ <b>خطا در اتصال کانال</b>\n\n{msg}",
                parse_mode="html"
            )
            asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
            return
        await _show_journal_panel_group(event, uid)
        return

    await event.answer()
    await db.ensure_user(uid)
    clear_state(uid)

    user = await db.get_user(uid)
    if not user or not user.get("channel_id"):
        await _prompt_channel_setup(event, uid)
        return

    is_valid, msg = await check_channel_validity(event.client, user["channel_id"])
    if not is_valid:
        await event.edit(
            f"⚠️ <b>خطا در اتصال کانال</b>\n\n{msg}\n\n"
            "لطفاً کانال را مجدداً تنظیم کنید:",
                buttons=[[Button.inline("🔄 تنظیم کانال", b"tj_settings_channel")],
                         [Button.inline("🔙 بازگشت", b"tj_back_journal")]],
            parse_mode="html"
        )
        return

    await _show_journal_panel_inline(event, uid)


async def _handle_back_journal(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    clear_state(uid)
    if event.is_private:
        await _show_journal_panel_inline(event, uid)
    else:
        await _show_journal_panel_group(event, uid)


async def _handle_new_trade(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    user = await db.get_user(uid)
    if not user or not user.get("channel_id"):
        await _prompt_channel_setup(event, uid)
        return

    templates = await db.get_templates(uid)
    if not templates:
        from ..handlers.template_builder import _create_default_and_prompt
        await _create_default_and_prompt(event, uid)
        return

    default_id = _get_default_template_id(templates)
    if default_id is not None:
        await _start_trade_with_template(event, uid, default_id)
        return

    if len(templates) == 1:
        await _start_trade_with_template(event, uid, templates[0]["id"])
        return

    await event.edit(
        "<b>📋 قالب مورد نظر را انتخاب کنید:</b>\n\n"
        "💡 <i>برای روش انتخاب اصلی برای این قالب، از بخش تنظیمات گزینه قالب دیفالت را فعال کنید.</i>",
        buttons=template_select_keyboard(templates),
        parse_mode="html"
    )


def _get_default_template_id(templates):
    for t in templates:
        if t.get("is_default"):
            return t["id"]
    return None


async def _handle_templates(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    if not event.is_private:
        try:
            me = await event.client.get_me()
            username = me.username or "Bot"
            msg = await event.respond(
                "📋 <b>مدیریت قالب‌ها</b>\n\n"
                f"<a href='https://t.me/{username}'>📨 باز کردن در پیوی ربات</a>",
                parse_mode="html"
            )
            asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
        except Exception as e:
            logger.error(f"Error redirecting to PM: {e}")
        return
    from ..ui.keyboards import template_list_keyboard
    templates = await db.get_templates(uid)
    await event.edit(
        "<b>📋 قالب‌های شما:</b>",
        buttons=template_list_keyboard(templates),
        parse_mode="html"
    )


async def _handle_symbols_menu(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    if not event.is_private:
        try:
            me = await event.client.get_me()
            username = me.username or "Bot"
            msg = await event.respond(
                "🏷️ <b>مدیریت نمادها</b>\n\n"
                f"<a href='https://t.me/{username}'>📨 باز کردن در پیوی ربات</a>",
                parse_mode="html"
            )
            asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
        except Exception as e:
            logger.error(f"Error redirecting to PM: {e}")
        return
    from ..handlers.symbols import _show_symbols_management
    await _show_symbols_management(event, uid)


async def _handle_settings_menu(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    if not event.is_private:
        try:
            me = await event.client.get_me()
            username = me.username or "Bot"
            msg = await event.respond(
                "⚙️ <b>تنظیمات</b>\n\n"
                f"<a href='https://t.me/{username}'>📨 باز کردن در پیوی ربات</a>",
                parse_mode="html"
            )
            asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
        except Exception as e:
            logger.error(f"Error redirecting to PM: {e}")
        return
    from ..handlers.settings import _show_settings
    await _show_settings(event, uid)


async def _handle_exit(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    state = get_state(uid)
    msg_ids = state.get("data", {}).get("_message_ids", [])
    clear_state(uid)
    try:
        await event.edit(
            "✅ ژورنال معاملات بسته شد.\n\nاز /start برای باز کردن منوی اصلی استفاده کنید.",
            parse_mode="html"
        )
    except Exception:
        pass
    for mid in msg_ids:
        try:
            await event.client.delete_messages(uid, mid)
        except Exception:
            pass


async def _handle_trades_list(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    PER_PAGE = 5
    trades = await db.get_user_trades_paginated(uid, limit=PER_PAGE, offset=0)
    total = await db.get_user_trades_count(uid)
    stats = await db.get_trade_stats(uid)

    if not trades:
        await event.edit(
            "🔍 <b>هیچ معامله‌ای ثبت نشده است.</b>\n\n"
            "با زدن دکمه «معامله جدید» شروع کنید.",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
            parse_mode="html"
        )
        return

    from ..ui.keyboards import trade_list_keyboard
    from ..services.ai_analysis import format_analysis_text

    caption_parts = []
    caption_parts.append(f"📋 <b>تاریخچه معاملات</b>")
    caption_parts.append(f"🔢 کل: <b>{total}</b> معاملات")
    caption_parts.append(f"🟢 برد: <b>{stats.get('wins', 0)}</b> | 🔴 باخت: <b>{stats.get('losses', 0)}</b> | 📈 وین ریت: <b>{stats.get('win_rate', 0):.1f}%</b>")
    caption_parts.append(f"💰 سود/زیان کل: <b>{stats.get('total_pnl', 0):+.2f}</b>")

    from ..services.ai_analysis import get_symbol_breakdown
    sym_data = await get_symbol_breakdown(uid)
    if sym_data:
        best_sym = max(sym_data.items(), key=lambda x: x[1]["total_pnl"])
        caption_parts.append(f"🌟 پرسودترین نماد: <b>{best_sym[0]}</b> ({best_sym[1]['total_pnl']:+.2f})")

    analysis = await db.get_detailed_analysis(uid)
    if analysis.get("best_day"):
        caption_parts.append(f"📅 پرسودترین روز: <b>{analysis['best_day']['day']}</b> ({analysis['best_day']['pnl']:+.2f})")
    if analysis.get("worst_day"):
        caption_parts.append(f"📅 پرضررترین روز: <b>{analysis['worst_day']['day']}</b> ({analysis['worst_day']['pnl']:+.2f})")

    text = "\n".join(caption_parts)
    await event.edit(text, buttons=trade_list_keyboard(trades, 0, total), parse_mode="html", link_preview=False)


async def _handle_trade_view(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return
    trade = await db.get_trade(trade_id, uid)

    if not trade:
        await event.answer("❌ معامله یافت نشد.", alert=True)
        return

    d_emoji = "📈" if trade.get("direction") == "LONG" else "📉"
    pnl = trade.get("pnl")
    pnl_icon = "🟢" if (pnl or 0) >= 0 else "🔴"

    lines = []
    lines.append(f"📊 ژورنال #{trade['id']}")
    lines.append(f"━━━━━━━━━━━━━━━━━━")
    lines.append(f"  {d_emoji} <b>{trade['symbol']}</b> — {trade['direction']}")
    if trade.get("trade_date"):
        lines.append(f"  📅 <b>تاریخ:</b> {trade['trade_date']}")
    lines.append("")
    if trade.get("volume"):
        lines.append(f"  📊 <b>حجم:</b> {trade['volume']}")
    if trade.get("entry_price"):
        lines.append(f"  ▶ <b>ورود:</b> {trade['entry_price']}")
    if trade.get("exit_price"):
        lines.append(f"  ◀ <b>خروج:</b> {trade['exit_price']}")
    if trade.get("stoploss_price"):
        lines.append(f"  ⚠️ <b>SL:</b> {trade['stoploss_price']}")
    if pnl is not None:
        lines.append(f"  {pnl_icon} <b>P&L:</b> <code>{pnl:.2f}</code>")
    if trade.get("pip_diff") is not None:
        lines.append(f"  📉 <b>پیپ:</b> <code>{trade['pip_diff']:+.1f}</code>")
    if trade.get("multiple_risk") is not None:
        lines.append(f"  🎯 <b>R:</b> <code>{trade['multiple_risk']:+.2f}</code>")
    if trade.get("risk_reward_ratio"):
        lines.append(f"  📐 <b>R:R:</b> 1:{trade['risk_reward_ratio']:.2f}")

    custom_data = trade.get("custom_data")
    if custom_data:
        lines.append("")
        lines.append("  ━━━ <b>دیگر</b> ━━━")
        for k, v in custom_data.items():
            if not isinstance(v, bool):
                lines.append(f"  📝 <b>{k}:</b> {v}")
            else:
                icon = "☑️" if v else "⬜"
                lines.append(f"  {icon} {k}")

    from ..ui.keyboards import trade_detail_keyboard
    await event.edit("\n".join(lines), buttons=trade_detail_keyboard(trade_id, trade.get("channel_message_id"), trade.get("channel_chat_id")), parse_mode="html", link_preview=False)


async def _handle_trade_edit(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return
    trade = await db.get_trade(trade_id, uid)

    if not trade:
        await event.answer("❌ معامله یافت نشد.", alert=True)
        return

    filled = {}
    cdata = trade.get("custom_data", {})
    if cdata:
        filled.update(cdata)
    filled["symbol"] = trade.get("symbol", "")
    filled["direction"] = trade.get("direction", "")
    filled["volume"] = trade.get("volume")
    filled["entry_price"] = trade.get("entry_price")
    filled["stoploss"] = trade.get("stoploss_price")
    filled["exit_price"] = trade.get("exit_price")
    filled["trade_date"] = trade.get("trade_date", "")

    template_id = trade.get("template_id")
    custom_fields = []
    if template_id:
        from ..services.template_service import get_custom_fields
        custom_fields = await get_custom_fields(template_id)

    from ..ui.formatter import build_status_text
    from ..ui.keyboards import live_panel_keyboard

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    from ..states import set_state, FILLING_FORM, update_state_data
    set_state(uid, FILLING_FORM, {
        "template_id": template_id,
        "filled": filled,
        "photos": [],
        "photo_mode": False,
        "panel_msg_id": None,
        "editing_field": None,
        "edit_trade_id": trade_id,
    })

    try:
        msg = await event.edit(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=event.message_id)
    except Exception as e:
        logger.error(f"Error editing trade: {e}")
        msg = await event.respond(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=msg.id)


async def _handle_trade_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return
    trade = await db.get_trade(trade_id, uid)

    if not trade:
        await event.answer("❌ معامله یافت نشد.", alert=True)
        return

    await event.edit(
        f"⚠️ <b>حذف معاملات</b>\n\n"
        f"آیا از حذف معامله <b>{trade['symbol']}</b> اطمینان دارید؟\n\n"
        f"⚠️ تنها از داده‌های داخلی پاک می‌شود. پست کانال حذف نمی‌شود.",
        buttons=[
            [Button.inline("✅ بله، حذف شود", f"tj_confirm_delete_trade:{trade_id}".encode(), style="danger")],
            [Button.inline("❌ خروج، انصراف", b"tj_trades_list")],
        ],
        parse_mode="html"
    )


async def _handle_trade_confirm_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return

    trade = await db.get_trade(trade_id, uid)
    if trade and trade.get("channel_message_id") and trade.get("channel_chat_id"):
        await delete_channel_message(event.client, trade["channel_chat_id"], trade["channel_message_id"])

    deleted = await db.delete_trade(trade_id, uid)

    if deleted:
        await event.edit("✅ <b>معامله با موفقیت حذف شد.</b>",
                         buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
                         parse_mode="html")
    else:
        await event.edit("❌ <b>خطا در حذف معامله</b>",
                         buttons=[[Button.inline("🔙 بازگشت", b"tj_trades_list")]],
                         parse_mode="html")


async def _handle_export(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    from ..services.ai_analysis import export_trades_json
    import json
    data = await export_trades_json(uid)
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    if len(json_str) > 4000:
        json_str = json_str[:4000] + "\n... (truncated)"
    await event.edit(
        f"<b>📤 خروجی داده‌ها</b>\n\n"
        f"تعداد معاملات: {data['summary']['total_trades']}\n\n"
        f"<code>{json_str}</code>",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
        parse_mode="html"
    )


async def _handle_trades_page(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    page = _parse_callback_int(event)
    if page is None:
        await event.answer("❌ شماره صفحه نامعتبر.", alert=True)
        return
    PER_PAGE = 5
    offset = page * PER_PAGE
    trades = await db.get_user_trades_paginated(uid, limit=PER_PAGE, offset=offset)
    total = await db.get_user_trades_count(uid)
    stats = await db.get_trade_stats(uid)

    if not trades:
        await event.answer("هیچ معاملات‌ای نیست.", alert=True)
        return

    from ..ui.keyboards import trade_list_keyboard

    caption_parts = []
    caption_parts.append(f"📋 <b>تاریخچه معاملات</b>")
    caption_parts.append(f"🔢 کل: <b>{total}</b> معاملات")
    caption_parts.append(f"🟢 برد: <b>{stats.get('wins', 0)}</b> | 🔴 باخت: <b>{stats.get('losses', 0)}</b> | 📈 وین‌ریت: <b>{stats.get('win_rate', 0):.1f}%</b>")

    analysis = await db.get_detailed_analysis(uid)
    if analysis.get("best_day"):
        caption_parts.append(f"📅 پرسودترین روز: <b>{analysis['best_day']['day']}</b> ({analysis['best_day']['pnl']:+.2f})")
    if analysis.get("worst_day"):
        caption_parts.append(f"📅 پرضررترین روز: <b>{analysis['worst_day']['day']}</b> ({analysis['worst_day']['pnl']:+.2f})")

    text = "\n".join(caption_parts)
    await event.edit(text, buttons=trade_list_keyboard(trades, page, total), parse_mode="html", link_preview=False)


async def _handle_detailed_analysis(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    from ..services.ai_analysis import format_analysis_text
    text = await format_analysis_text(uid)
    from ..ui.keyboards import stats_glass_keyboard
    await event.edit(text, buttons=stats_glass_keyboard(), parse_mode="html", link_preview=False)


async def _handle_group_stats(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    privacy = await db.get_privacy(uid)
    mode = privacy.get("mode", "off")

    if mode == "off":
        from ..services.ai_analysis import format_analysis_text
        text = await format_analysis_text(uid)
        await event.respond(text, parse_mode="html", link_preview=False)
        return

    stats = await db.get_trade_stats(uid)
    total = stats.get("total") or 0
    wins = stats.get("wins") or 0
    losses = stats.get("losses") or 0
    total_pnl = stats.get("total_pnl") or 0
    win_rate = stats.get("win_rate") or 0

    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

    lines = [
        "📊 <b>آمار کلی معاملات</b>",
        "",
        f"🔢 کل معاملات: <b>{total}</b>",
    ]

    if mode == "low":
        lines.append(f"✅ بردها: <b>{wins}</b>  ❌ باختها: <b>{losses}</b>")
        lines.append(f"📈 نرخ برد: <b>{win_rate:.1f}%</b>")
        lines.append(f"{pnl_emoji} سود/زیان کل: <b>{total_pnl:.2f}</b>")
    elif mode == "medium":
        pf = stats.get("profit_factor") or 0
        lines.append(f"✅ بردها: <b>{wins}</b>  ❌ باختها: <b>{losses}</b>")
        lines.append(f"{pnl_emoji} سود/زیان کل: <b>{total_pnl:.2f}</b>")
        if pf > 0:
            lines.append(f"📊 نسبت سود/ضرر: <b>{pf:.2f}</b>")
        lines.append("\n🛡 وین‌ریت مخفی شد.")
    elif mode == "high":
        lines.append(f"✅ بردها: <b>{wins}</b>  ❌ باختها: <b>{losses}</b>")
        lines.append("\n🛡 اطلاعات حساس مخفی شد.")

    text = "\n".join(lines)
    await event.respond(text, parse_mode="html", link_preview=False)


async def _handle_group_quick_trade(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    user = await db.get_user(uid)
    try:
        me = await event.client.get_me()
        username = me.username or "Bot"
    except Exception:
        username = "Bot"
    if not user or not user.get("channel_id"):
        msg = await event.respond(
            "⚠️ <b>ثبت ژورنال را در چت خصوصی راه‌اندازی کنید</b>\n\n"
            f"<a href='https://t.me/{username}'>ارسال پیام</a>",
            parse_mode="html"
        )
        asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
        return
    msg = await event.respond(
        "📝 <b>ثبت سریع معامله</b>\n\n"
        "برای ثبت معامله، لطفاً در چت خصوصی اقدام کنید:\n"
        f"<a href='https://t.me/{username}'>📨 باز کردن ژورنال</a>",
        parse_mode="html"
    )
    asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))


async def _handle_group_templates(event: Any) -> None:
    await event.answer()
    try:
        me = await event.client.get_me()
        username = me.username or "Bot"
    except Exception:
        username = "Bot"
    msg = await event.respond(
        "📋 <b>مدیریت قالب‌ها</b>\n\n"
        f"<a href='https://t.me/{username}'>📨 مدیریت قالب‌ها در چت خصوصی</a>",
        parse_mode="html"
    )
    asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))


async def _handle_group_search(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trades = await db.get_user_trades(uid)
    if not trades:
        await event.respond("🔍 <b>معامله‌ای یافت نشد.</b>", parse_mode="html")
        return

    privacy = await db.get_privacy(uid)
    mode = privacy.get("mode", "off")
    hide_pnl = mode in ("medium", "high")

    recent = trades[:5]
    lines = ["🔍 <b>آخرین معاملات:</b>\n"]
    for t in recent:
        d = "📈" if t["direction"] == "LONG" else "📉"
        pnl = t.get("pnl")
        pnl_str = "🛡 مخفی" if hide_pnl else (f"{pnl:.2f}" if pnl is not None else "N/A")
        lines.append(f"  {d} <b>{t['symbol']}</b> | {t['direction']} | P&L: <code>{pnl_str}</code>")
    lines.append("")
    lines.append("💡 برای مشاهده همه معاملات، در چت خصوصی از گزینه آمار استفاده کنید.")
    await event.respond("\n".join(lines), parse_mode="html")


async def _handle_group_help(event: Any) -> None:
    await event.answer()
    await event.respond(
        "💡 <b>راهنمای ژورنال معاملات</b>\n\n"
        "📊 <b>آنالیز و وضعیت</b> — مشاهده آمار کلی معاملات\n"
        "📝 <b>ثبت سریع</b> — ثبت معامله جدید (در چت خصوصی)\n"
        "📋 <b>قالب‌ها</b> — مدیریت قالب‌های معاملاتی\n"
        "🔍 <b>جستجو</b> — مشاهده آخرین معاملات\n\n"
        "💡 برای ثبت کامل معاملات، از چت خصوصی ربات استفاده کنید.",
        parse_mode="html"
    )


async def _prompt_channel_setup(event: Any, uid: int) -> None:
    from ..states import set_state, AWAIT_CHANNEL_FORWARD
    set_state(uid, AWAIT_CHANNEL_FORWARD)
    kb = [[Button.inline("❌ لغو", b"tj_cancel_setup", style="danger")]]
    setup_text = (
        "<b>📺 تنظیم کانال</b>\n\n"
        "برای شروع، ابتدا باید کانال خصوصی خود را متصل کنید.\n\n"
        "<b>روش 1:</b> یک پیام از کانال خود را فوروارد کنید\n"
        "<b>روش 2:</b> آیدی عددی کانال را ارسال کنید\n"
        "<b>روش 3:</b> لینک کانال را ارسال کنید\n\n"
        "⏳ منتظر ورودی شما..."
    )
    try:
        await event.edit(setup_text, buttons=kb, parse_mode="html")
    except Exception:
        await event.respond(setup_text, buttons=kb, parse_mode="html")


async def _start_trade_with_template(event: Any, uid: int, template_id: int) -> None:
    from ..services.template_service import build_initial_filled, get_custom_fields
    from ..ui.keyboards import live_panel_keyboard
    from ..ui.formatter import build_status_text

    filled = await build_initial_filled(template_id)
    filled["_template_id"] = template_id
    custom_fields = await get_custom_fields(template_id)

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    set_state(uid, FILLING_FORM, {
        "template_id": template_id,
        "filled": filled,
        "photos": [],
        "photo_mode": False,
        "panel_msg_id": None,
        "editing_field": None,
        "edit_trade_id": None,
    })

    try:
        await event.edit(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=event.message_id)
    except Exception as e:
        logger.error(f"Error starting trade form: {e}")
        try:
            msg = await event.respond(status_text, buttons=keyboard, parse_mode="html")
            update_state_data(uid, panel_msg_id=msg.id)
        except Exception as e2:
            logger.error(f"Error sending trade form fallback: {e2}")


async def _handle_message_router(event: Any) -> None:
    uid = event.sender_id
    if not event.is_private:
        return

    text = event.text or ""
    state_str = get_state_str(uid)

    try:
        # Fix Bug A: Command Hijacking
        if text.startswith('/'):
            if state_str == AWAIT_PHOTO and text.strip().lower() == "/done":
                # Let it fall through to the photo done handler
                pass
            else:
                clear_state(uid)
                # Let the command propagate to the main bot handlers
                return

        # Force reset state and open menu if the user clicks the Journal button, even if stuck in another state
        if "ژورنال معاملات" in text:
            clear_state(uid)
            logger.debug(f"Journal button triggered for user {uid}")
            try:
                user = await db.get_user(uid)
                if not user or not user.get("channel_id"):
                    await _prompt_channel_setup_msg(event, uid)
                else:
                    buttons = journal_inline_panel()
                    await event.reply(
                        "╭─────────────────────┐\n"
                        "│  📊 <b>ژورنال معاملات</b>  │\n"
                        "╰─────────────────────╯\n\n"
                        "یک گزینه انتخاب کنید:",
                        buttons=buttons, parse_mode="html"
                    )
            except Exception as e:
                logger.error(f"Error showing journal menu for user {uid}: {e}", exc_info=True)
                await event.reply("❌ خطایی در بازکردن منوی ژورنال رخ داد. لطفاً دوباره تلاش کنید.")
            return

        if state_str == AWAIT_CHANNEL_FORWARD:
            from .setup import handle_channel_input
            await handle_channel_input(event, uid)
            return

        if state_str == AWAIT_TEXT:
            from .live_form import handle_text_input
            await handle_text_input(event, uid, text)
            return

        if state_str == AWAIT_TEMPLATE_NAME:
            from .template_builder import handle_template_name_input
            await handle_template_name_input(event, uid, text)
            return

        if state_str == AWAIT_FIELD_LABEL:
            from .template_builder import handle_field_label_input
            await handle_field_label_input(event, uid, text)
            return

        if state_str == AWAIT_SYMBOL_NAME:
            from .symbols import handle_symbol_name_input
            await handle_symbol_name_input(event, uid, text)
            return

        if state_str == AWAIT_MARGIN:
            from .setup import handle_margin_input
            await handle_margin_input(event, uid, text)
            return

        state_data = get_state_data(uid)

        # Fix Bug B: Album/Media Group Race Condition
        if state_str == AWAIT_PHOTO:
            if event.photo:
                async with get_user_lock(uid):
                    state_data = get_state_data(uid)
                    photo_obj = event.photo
                    photos = state_data.get("photos", [])
                    replace_idx = state_data.get("replace_idx")
                    if replace_idx is not None and isinstance(replace_idx, int):
                        if 0 <= replace_idx < len(photos):
                            photos[replace_idx] = photo_obj
                            update_state_data(uid, photos=photos)
                            await event.reply(f"✅ عکس #{replace_idx+1} با موفقیت جتگیری شد.")
                    else:
                        # v2: v1 de-duplicated photos by `photo.id`, but v2's
                        # `event.photo` is a `File` with no `.id`. `photo_dedup_key`
                        # derives a stable identity from the file's internal
                        # input-media (unique per photo), preserving the same
                        # "don't add the same photo twice" behavior.
                        existing_keys = [photo_dedup_key(p) for p in photos]
                        if photo_dedup_key(photo_obj) not in existing_keys:
                            photos.append(photo_obj)
                            update_state_data(uid, photos=photos)
                            count = len(photos)
                            await event.reply(f"✅ عکس #{count} اضافه شد. عکس‌های بیشتری ارسال کنید یا /done بفرستید.")
                return
            if text and text.strip().lower() == "/done":
                from .live_form import _handle_done_photo_msg
                await _handle_done_photo_msg(event, uid)
                return

        if state_str == IDLE:
            if text == "/mystats":
                from .stats import handle_mystats
                await handle_mystats(event, uid)
                return
            if text == "/export":
                await _handle_export(event)
                return
    except Exception as e:
        logger.error(f"Unexpected error in message router for user {uid}: {e}", exc_info=True)
        try:
            await event.reply("❌ خطای غیرمنتظره‌ای رخ داد. لطفاً بعداً دوباره تلاش کنید.")
        except Exception:
            pass


async def _handle_new_trade_text(event: Any, uid: int) -> None:
    user = await db.get_user(uid)
    if not user or not user.get("channel_id"):
        await _prompt_channel_setup_msg(event, uid)
        return

    templates = await db.get_templates(uid)
    if not templates:
        from ..handlers.template_builder import _create_default_and_prompt_msg
        await _create_default_and_prompt_msg(event, uid)
        return

    default_id = _get_default_template_id(templates)
    if default_id is not None:
        await _start_trade_with_template_msg(event, uid, default_id)
        return

    if len(templates) == 1:
        await _start_trade_with_template_msg(event, uid, templates[0]["id"])
        return

    await event.reply(
        "<b>📋 قالب مورد نظر را انتخد کنید:</b>",
        buttons=template_select_keyboard(templates),
        parse_mode="html"
    )
    set_state(uid, SHOWING_TEMPLATES)


async def _prompt_channel_setup_msg(event: Any, uid: int) -> None:
    set_state(uid, AWAIT_CHANNEL_FORWARD)
    kb = [[Button.inline("❌ لغو", b"tj_cancel_setup", style="danger")]]
    await event.reply(
        "<b>📺 تنظیم کانال</b>\n\n"
        "برای شروع، ابتدا باید کانال خصوصی خود را متصل کنید.\n\n"
        "<b>روش 1:</b> یک پیام از کانال خود را فوروارد کنید\n"
        "<b>روش 2:</b> آیدی عددی کانال را ارسال کنید\n"
        "<b>روش 3:</b> لینک کانال را ارسال کنید\n\n"
        "⏳ منتظر ورودی شما...",
        buttons=kb,
        parse_mode="html"
    )


async def _handle_exit_as_message(event: Any, uid: int) -> None:
    clear_state(uid)
    try:
        await event.reply("✅ ژورنال معاملات بسته شد.\n\nاز /start برای باز کردن منوی اصلی استفاده کنید.")
    except Exception:
        pass
    try:
        await event.client.send_message(
            event.chat_id, " ",
            buttons=exit_reply_keyboard()
        )
    except Exception:
        pass


async def _start_trade_with_template_msg(event: Any, uid: int, template_id: int) -> None:
    from ..services.template_service import build_initial_filled, get_custom_fields
    from ..ui.keyboards import live_panel_keyboard
    from ..ui.formatter import build_status_text

    filled = await build_initial_filled(template_id)
    custom_fields = await get_custom_fields(template_id)

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    set_state(uid, FILLING_FORM, {
        "template_id": template_id,
        "filled": filled,
        "photos": [],
        "photo_mode": False,
        "panel_msg_id": None,
        "editing_field": None,
        "edit_trade_id": None,
    })

    try:
        msg = await event.reply(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=msg.id)
    except Exception as e:
        logger.error(f"Error starting trade form: {e}")


async def _handle_channel_edit(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return

    trade = await db.get_trade(trade_id, uid)
    if not trade:
        await event.answer("❌ معامله یافت نشد.", alert=True)
        return

    filled = {}
    cdata = trade.get("custom_data", {})
    if cdata:
        filled.update(cdata)
    filled["symbol"] = trade.get("symbol", "")
    filled["direction"] = trade.get("direction", "")
    filled["volume"] = trade.get("volume")
    filled["entry_price"] = trade.get("entry_price")
    filled["stoploss"] = trade.get("stoploss_price")
    filled["exit_price"] = trade.get("exit_price")
    filled["trade_date"] = trade.get("trade_date", "")

    template_id = trade.get("template_id")
    custom_fields = []
    if template_id:
        from ..services.template_service import get_custom_fields
        custom_fields = await get_custom_fields(template_id)

    from ..ui.formatter import build_status_text
    from ..ui.keyboards import live_panel_keyboard

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    set_state(uid, FILLING_FORM, {
        "template_id": template_id,
        "filled": filled,
        "photos": [],
        "photo_mode": False,
        "panel_msg_id": None,
        "editing_field": None,
        "edit_trade_id": trade_id,
    })

    try:
        msg = await event.edit(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=event.message_id)
    except Exception as e:
        logger.error(f"Error editing trade from channel: {e}")
        msg = await event.respond(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=msg.id)


async def _handle_channel_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = _parse_callback_int(event)
    if trade_id is None:
        await event.answer("❌ شناسه معامله نامعتبر.", alert=True)
        return

    trade = await db.get_trade(trade_id, uid)
    if not trade:
        await event.answer("❌ معامله یافت نشد.", alert=True)
        return

    ch_msg_id = trade.get("channel_message_id")
    ch_chat_id = trade.get("channel_chat_id")
    if ch_msg_id and ch_chat_id:
        await delete_channel_message(event.client, ch_chat_id, ch_msg_id)

    await db.delete_trade(trade_id, uid)

    await event.edit(
        "<b>✅ پیام از کانال حذف شد.</b>",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
        parse_mode="html"
    )


async def _handle_channel_analysis(event: Any) -> None:
    uid = event.sender_id
    await event.answer("در حال انتظار...", alert=False)
    await event.edit(
        "<b>🧠 آنالیز با ای</b>\n\n"
        "این فیچر به زودی اضافه می‌شود.",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
        parse_mode="html"
    )

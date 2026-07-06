import logging
from typing import Any

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
# TelegramClient is aliased to v2's `Client`; `data_regex` reproduces v1's
# `events.CallbackQuery(pattern=...)` regex semantics under v2 filters.
from telethon_compat import TelegramClient, events, filters, data_regex

from .. import database as db
from ..states import (
    set_state, get_state_data, clear_state, update_state_data,
    IDLE, AWAIT_SYMBOL_NAME, FILLING_FORM, SHOWING_SYMBOLS
)
from ..ui.keyboards import symbol_manage_keyboard, journal_reply_keyboard
from ..utils import parse_callback_int

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSDT", "ETHUSDT", "XAUUSD"]


def register_symbol_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.ButtonCallback, filters.Data(b"tj_sym_add"))(wrap(_handle_sym_add))
    client.on(events.ButtonCallback, data_regex(r"tj_sym_del:"))(wrap(_handle_sym_del))
    client.on(events.ButtonCallback, filters.Data(b"tj_back_symbols"))(wrap(_handle_back_symbols))
    client.on(events.ButtonCallback, data_regex(r"tj_sym_info:"))(wrap(_handle_sym_info))


async def _handle_sym_add(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await db.ensure_user(uid)
    set_state(uid, AWAIT_SYMBOL_NAME)
    try:
        await event.edit(
            "🏷️ <b>افزودن نماد</b>\n\n"
            "نام نماد را ارسال کنید (مثلاً EURUSD):",
            parse_mode="html"
        )
    except Exception:
        await event.respond(
            "🏷️ <b>افزودن نماد</b>\n\n"
            "نام نماد را ارسال کنید (مثلاً EURUSD):",
            parse_mode="html"
        )


async def _handle_sym_del(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    sym_id = parse_callback_int(event.data)
    if sym_id is None:
        await event.answer("❌ شناسه نامعتبر.", alert=True)
        return
    await db.delete_symbol(sym_id)

    symbols = await db.get_symbols(uid)
    if symbols:
        await event.edit(
            "✅ نماد حذف شد.\n\n<b>نمادهای شما:</b>",
            buttons=symbol_manage_keyboard(symbols),
            parse_mode="html"
        )
    else:
        from telethon_compat import Button  # v2: buttons module via compat
        await event.edit(
            "✅ نماد حذف شد.\n\nهیچ نمادی ندارید.",
            buttons=[
                [Button.inline("➕ افزودن نماد", b"tj_sym_add")],
                [Button.inline("🔙 بازگشت", b"tj_back_journal")],
            ],
            parse_mode="html"
        )


async def _handle_back_symbols(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    clear_state(uid)
    from .entry import _show_journal_panel_inline
    await _show_journal_panel_inline(event, uid)


async def _handle_sym_info(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    sym_id = parse_callback_int(event.data)
    if sym_id is None:
        await event.answer("❌ شناسه نامعتبر.", alert=True)
        return
    symbols = await db.get_symbols(uid)
    symbol = next((s for s in symbols if s["id"] == sym_id), None)
    if symbol:
        trades = await db.get_user_trades(uid)
        sym_trades = [t for t in trades if t["symbol"] == symbol["name"]]
        wins = sum(1 for t in sym_trades if (t.get("pnl") or 0) > 0)
        losses = sum(1 for t in sym_trades if (t.get("pnl") or 0) < 0)
        total_pnl = sum(t.get("pnl") or 0 for t in sym_trades)
        await event.answer(
            f"🏷️ {symbol['name']}\n"
            f"معاملات: {len(sym_trades)}\n"
            f"برد: {wins} | باخت: {losses}\n"
            f"P&L: {total_pnl:+.2f}",
            alert=True
        )
    else:
        await event.answer("اطلاعات نماد", alert=False)


async def handle_symbol_name_input(event: Any, uid: int, text: str) -> None:
    name = text.strip().upper()
    if not name:
        await event.reply("❌ نام نماد نمی‌تواند خالی باشد. نام نماد را ارسال کنید:")
        return

    existing = await db.get_symbol_by_name(uid, name)
    if existing:
        await event.reply(f"⚠️ نماد <b>{name}</b> از قبل وجود دارد. نام دیگری ارسال کنید:")
        return

    await db.add_symbol(uid, name)

    state_data = get_state_data(uid)

    if state_data.get("_return_to_form"):
        state_data["filled"]["symbol"] = name
        set_state(uid, FILLING_FORM, state_data)
        update_state_data(uid, filled=state_data["filled"])
        await event.reply(f"✅ نماد <b>{name}</b> اضافه شد و انتخاب شد.")
        from .live_form import _rerender_panel_msg
        await _rerender_panel_msg(event, uid)
        return

    clear_state(uid)

    symbols = await db.get_symbols(uid)
    await event.reply(
        f"✅ نماد <b>{name}</b> اضافه شد!",
        buttons=symbol_manage_keyboard(symbols),
        parse_mode="html"
    )


async def _show_symbols_management(event: Any, uid: int) -> None:
    symbols = await db.get_symbols(uid)
    if not symbols:
        await db.ensure_user(uid)
        for s_name in DEFAULT_SYMBOLS:
            await db.add_symbol(uid, s_name)
        symbols = await db.get_symbols(uid)

    await event.edit(
        "🏷️ <b>نمادهای شما:</b>",
        buttons=symbol_manage_keyboard(symbols),
        parse_mode="html"
    )


async def _show_symbols_management_msg(event: Any, uid: int) -> None:
    symbols = await db.get_symbols(uid)
    if not symbols:
        await db.ensure_user(uid)
        for s_name in DEFAULT_SYMBOLS:
            await db.add_symbol(uid, s_name)
        symbols = await db.get_symbols(uid)

    set_state(uid, SHOWING_SYMBOLS)
    await event.reply(
        "🏷️ <b>نمادهای شما:</b>",
        buttons=symbol_manage_keyboard(symbols),
        parse_mode="html"
    )

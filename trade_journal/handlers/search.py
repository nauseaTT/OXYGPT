import logging
from typing import Any, List, Dict

from telethon import TelegramClient, events
from telethon.tl.custom import Button

from .. import database as db
from ..states import set_state, get_state_str, get_state_data, clear_state, IDLE
from ..ui.keyboards import search_keyboard, trade_list_keyboard
from ..ui.formatter import MANDATORY_FIELDS
from ..utils import get_callback_parts, parse_callback_int, parse_callback_str
from ..ui.design_system import Icons, Labels

logger = logging.getLogger(__name__)


def register_search_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.CallbackQuery(data=b"tj_search"))(wrap(_handle_search))
    client.on(events.CallbackQuery(data=b"tj_search_symbol"))(wrap(_handle_search_symbol))
    client.on(events.CallbackQuery(data=b"tj_search_date"))(wrap(_handle_search_date))
    client.on(events.CallbackQuery(data=b"tj_search_pnl"))(wrap(_handle_search_pnl))
    client.on(events.CallbackQuery(data=b"tj_search_tag"))(wrap(_handle_search_tag))
    client.on(events.CallbackQuery(pattern=r"tj_search_sym_select:"))(wrap(_handle_search_sym_select))
    client.on(events.CallbackQuery(pattern=r"tj_search_pnl_type:"))(wrap(_handle_search_pnl_type))


async def _handle_search(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await event.edit(
        "🔍 <b>جستجوی معاملات</b>\n\n"
        "نوع جستجو را انتخاب کنید:",
        buttons=search_keyboard(),
        parse_mode="html"
    )


async def _handle_search_symbol(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    symbols = await db.get_symbols(uid)

    if not symbols:
        await event.edit(
            "🏷️ <b>هیچ نمادی ثبت نشده</b>",
            buttons=[[Button.inline(f"🔙 {Labels.BACK}", b"tj_search")]],
            parse_mode="html"
        )
        return

    rows = []
    for s in symbols:
        rows.append([Button.inline(
            f"🏷️ {s['name']}",
            f"tj_search_sym_select:{s['name']}".encode()
        )])
    rows.append([Button.inline(f"🔙 {Labels.BACK}", b"tj_search")])

    await event.edit(
        "🔍 <b>انتخاب نماد برای جستجو</b>",
        buttons=rows,
        parse_mode="html"
    )


async def _handle_search_sym_select(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    symbol = parse_callback_str(event.data)

    if not symbol:
        await event.answer("❌ نماد نامعتبر", alert=True)
        return

    trades = await db.get_user_trades(uid)
    filtered = [t for t in trades if t["symbol"] == symbol.upper()]

    if not filtered:
        await event.edit(
            f"🔍 <b>هیچ معامله‌ای برای {symbol} یافت نشد</b>",
            buttons=[[Button.inline(f"🔙 {Labels.BACK}", b"tj_search")]],
            parse_mode="html"
        )
        return

    from ..services.ai_analysis import get_symbol_breakdown
    breakdown = await get_symbol_breakdown(uid)
    sym_data = breakdown.get(symbol.upper(), {})

    text = f"🔍 <b>نتایج جستجو: {symbol}</b>\n\n"
    text += f"🔢 کل: <b>{sym_data.get('total', 0)}</b>\n"
    text += f"🟢 برد: <b>{sym_data.get('wins', 0)}</b>\n"
    text += f"🔴 باخت: <b>{sym_data.get('losses', 0)}</b>\n"
    text += f"💰 P&L: <b>{sym_data.get('total_pnl', 0):+.2f}</b>\n"
    text += f"📈 Win Rate: <b>{sym_data.get('win_rate', 0):.1f}%</b>\n"

    await event.edit(
        text,
        buttons=trade_list_keyboard(filtered[:5], 0, len(filtered)),
        parse_mode="html"
    )


async def _handle_search_date(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trades = await db.get_user_trades(uid)

    if not trades:
        await event.edit(
            "🔍 <b>هیچ معامله‌ای یافت نشد</b>",
            buttons=[[Button.inline(f"🔙 {Labels.BACK}", b"tj_search")]],
            parse_mode="html"
        )
        return

    await event.edit(
        f"🔍 <b>آخرین معاملات ({len(trades[:5])})</b>",
        buttons=trade_list_keyboard(trades[:5], 0, len(trades)),
        parse_mode="html"
    )


async def _handle_search_pnl(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    rows = [
        [Button.inline("🟢 فقط بردها", b"tj_search_pnl_type:win")],
        [Button.inline("🔴 فقط باخت‌ها", b"tj_search_pnl_type:loss")],
        [Button.inline("💰 پرسودترین‌ها", b"tj_search_pnl_type:top")],
        [Button.inline("💥 پرضررترین‌ها", b"tj_search_pnl_type:bottom")],
        [Button.inline(f"🔙 {Labels.BACK}", b"tj_search")],
    ]

    await event.edit(
        "🔍 <b>جستجو بر اساس سود/زیان</b>",
        buttons=rows,
        parse_mode="html"
    )


async def _handle_search_pnl_type(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    pnl_type = parse_callback_str(event.data)

    trades = await db.get_user_trades(uid)

    if pnl_type == "win":
        filtered = [t for t in trades if (t.get("pnl") or 0) > 0]
        title = "🟢 بردها"
    elif pnl_type == "loss":
        filtered = [t for t in trades if (t.get("pnl") or 0) < 0]
        title = "🔴 باخت‌ها"
    elif pnl_type == "top":
        filtered = sorted(trades, key=lambda x: x.get("pnl") or 0, reverse=True)[:10]
        title = "💰 پرسودترین‌ها"
    elif pnl_type == "bottom":
        filtered = sorted(trades, key=lambda x: x.get("pnl") or 0)[:10]
        title = "💥 پرضررترین‌ها"
    else:
        filtered = trades
        title = "همه معاملات"

    if not filtered:
        await event.edit(
            f"🔍 <b>{title}: هیچ نتیجه‌ای یافت نشد</b>",
            buttons=[[Button.inline(f"🔙 {Labels.BACK}", b"tj_search")]],
            parse_mode="html"
        )
        return

    await event.edit(
        f"🔍 <b>{title} ({len(filtered[:5])})</b>",
        buttons=trade_list_keyboard(filtered[:5], 0, len(filtered)),
        parse_mode="html"
    )


async def _handle_search_tag(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trades = await db.get_user_trades(uid)
    if not trades:
        await event.edit(
            "🔍 <b>هیچ معامله‌ای یافت نشد</b>",
            buttons=[[Button.inline(f"🔙 {Labels.BACK}", b"tj_search")]],
            parse_mode="html"
        )
        return

    await event.edit(
        f"🔍 <b>آخرین معاملات ({len(trades[:5])})</b>",
        buttons=trade_list_keyboard(trades[:5], 0, len(trades)),
        parse_mode="html"
    )

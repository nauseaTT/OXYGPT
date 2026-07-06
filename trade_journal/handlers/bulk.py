import logging
from typing import Any, List, Dict, Set

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
# The compat layer aliases `TelegramClient` to v2's `Client`, provides the
# v2 `events` module, `filters`, and the `data_regex` helper that reproduces
# v1's `events.CallbackQuery(pattern=...)` regex-matching semantics.
from telethon_compat import TelegramClient, events, Button, filters, data_regex

from .. import database as db
from ..states import set_state, get_state_str, get_state_data, clear_state, update_state_data
from ..ui.keyboards import trade_list_keyboard
from ..utils import parse_callback_int

logger = logging.getLogger(__name__)

_user_selections: Dict[int, Set[int]] = {}


def register_bulk_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_mode"))(wrap(_handle_bulk_mode))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_select_toggle"))(wrap(_handle_bulk_select_toggle))
    client.on(events.ButtonCallback, data_regex(r"tj_bulk_toggle:"))(wrap(_handle_bulk_toggle))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_delete"))(wrap(_handle_bulk_delete))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_tag"))(wrap(_handle_bulk_tag))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_export"))(wrap(_handle_bulk_export))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_select_all"))(wrap(_handle_bulk_select_all))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_deselect_all"))(wrap(_handle_bulk_deselect_all))
    client.on(events.ButtonCallback, filters.Data(b"tj_bulk_confirm_delete"))(wrap(_handle_bulk_confirm_delete))


def _get_user_selection(uid: int) -> Set[int]:
    if uid not in _user_selections:
        _user_selections[uid] = set()
    return _user_selections[uid]


async def _handle_bulk_mode(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    trades = await db.get_user_trades(uid)
    if not trades:
        await event.edit(
            "🔍 <b>هیچ معامله‌ای ثبت نشده</b>",
            buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
            parse_mode="html"
        )
        return

    _user_selections[uid] = set()
    await _show_bulk_list(event, uid, trades)


async def _show_bulk_list(event: Any, uid: int, trades: List[Dict]) -> None:
    selection = _get_user_selection(uid)

    rows = []
    for t in trades:
        is_selected = t["id"] in selection
        mark = "✅" if is_selected else "⬜"
        direction = "📈" if t.get("direction") == "LONG" else "📉"
        pnl = t.get("pnl")
        pnl_icon = "🟢" if (pnl or 0) >= 0 else "🔴"

        btn_text = f"{mark} {direction} {t['symbol']} | {pnl_icon}"
        rows.append([Button.inline(
            btn_text,
            f"tj_bulk_toggle:{t['id']}".encode()
        )])

    rows.append([
        Button.inline("✅ انتخاب همه", b"tj_bulk_select_all"),
        Button.inline("❌ لغو همه", b"tj_bulk_deselect_all"),
    ])

    selected_count = len(selection)
    if selected_count > 0:
        rows.append([
            Button.inline(f"🗑 حذف ({selected_count})", b"tj_bulk_delete", style="danger"),
            Button.inline(f"📤 خروجی ({selected_count})", b"tj_bulk_export"),
        ])

    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])

    text = f"📋 <b>حالت انتخاب گروهی</b>\n\n"
    text += f"تعداد انتخاب شده: <b>{selected_count}</b>"

    await event.edit(text, buttons=rows, parse_mode="html")


async def _handle_bulk_toggle(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    trade_id = parse_callback_int(event.data)

    if trade_id is None:
        return

    selection = _get_user_selection(uid)
    if trade_id in selection:
        selection.remove(trade_id)
    else:
        selection.add(trade_id)

    trades = await db.get_user_trades(uid)
    await _show_bulk_list(event, uid, trades)


async def _handle_bulk_select_toggle(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    selection = _get_user_selection(uid)
    trades = await db.get_user_trades(uid)

    all_selected = all(t["id"] in selection for t in trades)
    if all_selected:
        _user_selections[uid] = set()
    else:
        _user_selections[uid] = {t["id"] for t in trades}

    await _show_bulk_list(event, uid, trades)


async def _handle_bulk_select_all(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    trades = await db.get_user_trades(uid)
    _user_selections[uid] = {t["id"] for t in trades}

    await _show_bulk_list(event, uid, trades)


async def _handle_bulk_deselect_all(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    _user_selections[uid] = set()

    trades = await db.get_user_trades(uid)
    await _show_bulk_list(event, uid, trades)


async def _handle_bulk_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    selection = _get_user_selection(uid)
    if not selection:
        await event.answer("هیچ معامله‌ای انتخاب نشده!", alert=True)
        return

    text = f"⚠️ <b>حذف گروهی</b>\n\n"
    text += f"آیا از حذف <b>{len(selection)}</b> معامله اطمینان دارید?\n\n"
    text += "⚠️ این عمل قابل بازگشت نیست!"

    await event.edit(
        text,
        buttons=[
            [Button.inline("✅ بله، حذف شود", b"tj_bulk_confirm_delete", style="danger"),
             Button.inline("❌ انصراف", b"tj_bulk_mode")],
        ],
        parse_mode="html"
    )


async def _handle_bulk_confirm_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    selection = _get_user_selection(uid)
    if not selection:
        await event.answer("هیچ معامله‌ای انتخاب نشده!", alert=True)
        return

    deleted_count = 0
    for trade_id in selection:
        deleted = await db.delete_trade(trade_id, uid)
        if deleted:
            deleted_count += 1

    _user_selections[uid] = set()

    await event.edit(
        f"✅ <b>{deleted_count} معامله حذف شد</b>",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_back_journal")]],
        parse_mode="html"
    )


async def _handle_bulk_export(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    selection = _get_user_selection(uid)
    if not selection:
        await event.answer("هیچ معامله‌ای انتخاب نشده!", alert=True)
        return

    trades = await db.get_user_trades(uid)
    selected_trades = [t for t in trades if t["id"] in selection]

    import json
    data = {"trades": selected_trades}
    json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)

    if len(json_str) > 4000:
        json_str = json_str[:4000] + "\n... (truncated)"

    await event.edit(
        f"<b>📤 خروجی انتخاب شده ({len(selected_trades)} معامله)</b>\n\n"
        f"<code>{json_str}</code>",
        buttons=[[Button.inline("🔙 بازگشت", b"tj_bulk_mode")]],
        parse_mode="html"
    )


async def _handle_bulk_tag(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await event.answer("قابلیت تگ به زودی اضافه می‌شود", alert=True)

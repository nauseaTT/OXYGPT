import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom import Button

from .. import database as db
from ..states import (
    get_state, get_state_str, get_state_data, set_state, update_state_data,
    clear_state, FILLING_FORM, AWAIT_TEXT, AWAIT_PHOTO, AWAIT_SYMBOL_NAME, IDLE
)
from ..services.template_service import get_custom_fields, MANDATORY_KEYS
from ..services.trade_service import compute_trade_calculations, validate_mandatory_fields, validate_trade_values
from ..ui.keyboards import (
    live_panel_keyboard, direction_keyboard, date_keyboard,
    post_confirm_keyboard, symbol_select_keyboard, journal_reply_keyboard,
    exit_reply_keyboard, channel_setup_keyboard, symbol_select_keyboard_for_form,
    volume_keyboard, format_trade_date, scoring_keyboard, choice_options_keyboard
)
from ..ui.formatter import build_status_text, generate_journal_text, MANDATORY_FIELDS
from ..services.channel_service import post_text_to_channel, forward_photos_then_reply_text, edit_channel_message
from ..services.trade_service import get_journal_hashtags
from ..utils import is_valid_number, parse_number, parse_callback_str, parse_callback_int, get_callback_parts

logger = logging.getLogger(__name__)


def register_live_form_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.CallbackQuery(pattern=r"tj_field:"))(wrap(_handle_field_click))
    client.on(events.CallbackQuery(pattern=r"tj_dir:"))(wrap(_handle_direction))
    client.on(events.CallbackQuery(pattern=r"tj_date:"))(wrap(_handle_date))
    client.on(events.CallbackQuery(data=b"tj_add_photo"))(wrap(_handle_add_photo))
    client.on(events.CallbackQuery(data=b"tj_preview"))(wrap(_handle_preview))
    client.on(events.CallbackQuery(data=b"tj_finalize"))(wrap(_handle_finalize))
    client.on(events.CallbackQuery(data=b"tj_confirm_post"))(wrap(_handle_confirm_post))
    client.on(events.CallbackQuery(data=b"tj_continue_edit"))(wrap(_handle_continue_edit))
    client.on(events.CallbackQuery(data=b"tj_cancel_trade"))(wrap(_handle_cancel_trade))
    client.on(events.CallbackQuery(pattern=r"tj_toggle:"))(wrap(_handle_toggle_checkbox))
    client.on(events.CallbackQuery(pattern=r"tj_text_field:"))(wrap(_handle_text_field_click))
    client.on(events.CallbackQuery(data=b"tj_done_photo"))(wrap(_handle_done_photo))
    client.on(events.CallbackQuery(pattern=r"tj_sym_form:"))(wrap(_handle_symbol_form_select))
    client.on(events.CallbackQuery(data=b"tj_sym_add_form"))(wrap(_handle_symbol_add_form))
    client.on(events.CallbackQuery(data=b"tj_cancel_field_edit"))(wrap(_handle_cancel_field_edit))
    client.on(events.CallbackQuery(pattern=r"tj_vol:"))(wrap(_handle_volume_select))
    client.on(events.CallbackQuery(data=b"tj_vol_custom"))(wrap(_handle_volume_custom))
    client.on(events.CallbackQuery(data=b"tj_skip_scoring"))(wrap(_handle_skip_scoring))
    client.on(events.CallbackQuery(pattern=r"tj_score:"))(wrap(_handle_score_set))
    client.on(events.CallbackQuery(pattern=r"tj_choice:"))(wrap(_handle_choice_select))
    client.on(events.CallbackQuery(pattern=r"tj_photo_replace:"))(wrap(_handle_photo_replace))
    client.on(events.CallbackQuery(pattern=r"tj_photo_delete:"))(wrap(_handle_photo_delete))


async def _handle_field_click(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده. معامله جدیدی ثبت کنید.", alert=True)
        return

    field_key = parse_callback_str(event.data)
    if not field_key:
        await event.answer("❌ داده نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)

    if field_key == "direction":
        await event.answer()
        await event.edit("<b>Direction:</b>", buttons=direction_keyboard(), parse_mode="html")
        return

    if field_key == "trade_date":
        await event.answer()
        await event.edit("<b>Date:</b>", buttons=date_keyboard(), parse_mode="html")
        return

    if field_key == "symbol":
        await event.answer()
        symbols = await db.get_symbols(uid)
        kb = symbol_select_keyboard_for_form(symbols)
        await event.edit("<b>Symbol:</b>", buttons=kb, parse_mode="html")
        return

    if field_key == "volume":
        await event.answer()
        await event.edit("<b>Volume:</b>", buttons=volume_keyboard(), parse_mode="html")
        return

    set_state(uid, AWAIT_TEXT, {
        **state_data,
        "editing_field": field_key,
        "await_type": "number" if field_key in ("entry_price", "stoploss", "exit_price") else "text",
    })
    label = MANDATORY_FIELDS.get(field_key, field_key)
    await event.answer()
    hint = "(decimal)" if field_key in ("entry_price", "stoploss", "exit_price") else ""
    await event.edit(f"<b>{label}</b> {hint}:\n\nSend value:", parse_mode="html")


async def _handle_symbol_form_select(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    symbol = parse_callback_str(event.data)
    if not symbol:
        await event.answer("❌ نماد نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)
    state_data["filled"]["symbol"] = symbol
    update_state_data(uid, filled=state_data["filled"])
    await _rerender_panel(event, uid)


async def _handle_symbol_add_form(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    set_state(uid, AWAIT_SYMBOL_NAME, {
        **state_data,
        "_return_to_form": True,
    })
    await event.answer()
    await event.edit("<b>Send new symbol:</b>\n(e.g. EURUSD)", parse_mode="html")


async def _handle_volume_select(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    vol = parse_callback_str(event.data)
    state_data = get_state_data(uid)
    state_data["filled"]["volume"] = parse_number(vol) if vol is not None else None
    update_state_data(uid, filled=state_data["filled"])
    await event.answer()
    await _rerender_panel(event, uid)


async def _handle_volume_custom(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    set_state(uid, AWAIT_TEXT, {
        **state_data,
        "editing_field": "volume",
        "await_type": "number",
    })
    await event.answer()
    await event.edit("<b>Volume:</b> (decimal)\n\nSend value:", parse_mode="html")


async def _handle_direction(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    direction = parse_callback_str(event.data)
    if not direction:
        await event.answer("❌ جهت نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)
    state_data["filled"]["direction"] = direction
    update_state_data(uid, filled=state_data["filled"])
    await _rerender_panel(event, uid)


async def _handle_date(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    choice = parse_callback_str(event.data)
    if not choice:
        await event.answer("❌ انتخاب نامعتبر.", alert=True)
        return
    if choice == "now":
        state_data = get_state_data(uid)
        state_data["filled"]["trade_date"] = format_trade_date()
        update_state_data(uid, filled=state_data["filled"])
    await _rerender_panel(event, uid)


async def _handle_toggle_checkbox(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    field_key = parse_callback_str(event.data)
    if not field_key:
        await event.answer("❌ داده نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)
    current = state_data["filled"].get(field_key, False)
    state_data["filled"][field_key] = not current
    update_state_data(uid, filled=state_data["filled"])
    await _rerender_panel(event, uid)


async def _handle_text_field_click(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    field_key = parse_callback_str(event.data)
    if not field_key:
        await event.answer("❌ داده نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)
    custom_fields = await get_custom_fields(state_data.get("template_id", 0))
    cf = next((c for c in custom_fields if c["field_key"] == field_key), None)

    if cf and cf.get("field_type") == "choice" and cf.get("choice_options"):
        options = cf["choice_options"].split(",") if isinstance(cf["choice_options"], str) else []
        options = [o.strip() for o in options if o.strip()]
        if options:
            await event.answer()
            await event.edit(
                f"<b>{cf['field_label']}:</b>\n\nSelect option:",
                buttons=choice_options_keyboard(options, field_key),
                parse_mode="html"
            )
            return

    set_state(uid, AWAIT_TEXT, {
        **state_data,
        "editing_field": field_key,
        "await_type": "text",
    })

    await event.answer()
    label = field_key
    for cf in custom_fields:
        if cf["field_key"] == field_key:
            label = cf["field_label"]
            break
    await event.edit(f"<b>{label}:</b>\n\nSend text:", parse_mode="html")


async def _handle_add_photo(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    photos = state_data.get("photos", [])
    photo_mode = state_data.get("photo_mode", False)

    if photo_mode:
        state_data["photo_mode"] = False
        update_state_data(uid, photo_mode=False)
        await event.answer("حالت عکس غیرفعال شد", alert=False)
        await _rerender_panel(event, uid)
    else:
        if len(photos) >= 3:
            await event.answer("حداکثر 3 عکس مجاز است.", alert=True)
            return
        state_data["photo_mode"] = True
        set_state(uid, AWAIT_PHOTO, state_data)
        await event.answer()
        try:
            await event.edit(
                "<b>Photo Mode</b>\n\n"
                "Send your photos.\n"
                "When done, click <b>Back</b> or send /done.",
                buttons=[[Button.inline("Back", b"tj_done_photo", style="success")]],
                parse_mode="html"
            )
        except Exception:
            await event.respond(
                "<b>Photo Mode</b>\n\n"
                "Send your photos.\n"
                "When done, click <b>Back</b> or send /done.",
                buttons=[[Button.inline("Back", b"tj_done_photo", style="success")]],
                parse_mode="html"
            )


async def _handle_done_photo(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str not in (AWAIT_PHOTO, FILLING_FORM):
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    replace_idx = state_data.get("replace_idx")

    if replace_idx is not None and isinstance(replace_idx, int):
        state_data.pop("replace_idx", None)

    state_data["photo_mode"] = False
    set_state(uid, FILLING_FORM, state_data)

    photo_count = len(state_data.get("photos", []))
    await event.answer(f"✅ {photo_count} photos ready", alert=False)
    await _rerender_panel(event, uid)


async def _handle_done_photo_msg(event: Any, uid: int) -> None:
    state_data = get_state_data(uid)
    state_data["photo_mode"] = False
    set_state(uid, FILLING_FORM, state_data)
    photo_count = len(state_data.get("photos", []))
    await event.reply(f"✅ {photo_count} photos ready. Returning to form.")
    await _rerender_panel_msg(event, uid)


async def _handle_preview(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    filled = state_data.get("filled", {})
    template_id = state_data.get("template_id")

    pnl, risk, rr, pip_diff = compute_trade_calculations(filled)
    custom_fields = await get_custom_fields(template_id) if template_id else []
    journal_number = await db.get_journal_number(uid)

    mr_str = filled.get("multiple_risk")
    multiple_risk = parse_number(str(mr_str)) if mr_str else None

    journal_text = generate_journal_text(filled, pnl, risk, rr, pip_diff, multiple_risk, custom_fields, journal_number)

    await event.answer()
    preview_text = f"<b>Preview:</b>\n\n{journal_text}"
    await event.edit(
        preview_text,
        buttons=post_confirm_keyboard(),
        parse_mode="html",
        link_preview=False
    )


async def _handle_finalize(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    state_data = get_state_data(uid)
    filled = state_data.get("filled", {})
    missing = validate_mandatory_fields(filled)

    if missing:
        missing_list = "\n".join(f"  • {m}" for m in missing)
        await event.answer("فیلدهای الزامی خالی هستند!", alert=True)
        await event.edit(
            f"<b>Missing fields:</b>\n\n{missing_list}\n\n"
            "Please fill all mandatory fields.",
            buttons=[[Button.inline("Back to form", b"tj_continue_edit")]],
            parse_mode="html"
        )
        return

    errors = validate_trade_values(filled)
    if errors:
        error_list = "\n".join(f"  • {e}" for e in errors)
        await event.answer("مقدارها نامعتبر هستند!", alert=True)
        await event.edit(
            f"<b>Invalid values:</b>\n\n{error_list}\n\n"
            "Please correct the values.",
            buttons=[[Button.inline("Back to form", b"tj_continue_edit")]],
            parse_mode="html"
        )
        return

    await _handle_preview(event)


async def _handle_confirm_post(event: Any) -> None:
    uid = event.sender_id
    state_data = get_state_data(uid)
    state_str = get_state_str(uid)

    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return

    filled = state_data.get("filled", {})
    template_id = state_data.get("template_id")
    photos = state_data.get("photos", [])
    edit_trade_id = state_data.get("edit_trade_id")

    user = await db.get_user(uid)
    if not user or not user.get("channel_id"):
        await event.answer("⚠️ کانال تنظیم نشده! به تنظیمات بروید.", alert=True)
        return

    channel_id = user.get("channel_id")
    template_info = await db.get_template(template_id) if template_id else None
    if template_info and template_info.get("channel_id"):
        channel_id = template_info["channel_id"]

    if not channel_id:
        await event.answer("⚠️ کانالی برای این قالب تنظیم نشده.", alert=True)
        return

    pnl, risk, rr, pip_diff = compute_trade_calculations(filled)

    mr_str = filled.get("multiple_risk")
    multiple_risk = parse_number(str(mr_str)) if mr_str else None

    custom_fields = await get_custom_fields(template_id) if template_id else []
    journal_number = await db.get_journal_number(uid)

    journal_text = generate_journal_text(filled, pnl, risk, rr, pip_diff, multiple_risk, custom_fields, journal_number)
    hashtags = get_journal_hashtags(filled, pnl)
    full_text = f"{journal_text}\n\n{hashtags}"

    await event.answer("در حال انتشار...", alert=False)

    existing_channel_msg_id = None
    existing_channel_chat_id = None
    if edit_trade_id:
        existing_trade = await db.get_trade(edit_trade_id, uid)
        if existing_trade:
            existing_channel_msg_id = existing_trade.get("channel_message_id")
            existing_channel_chat_id = existing_trade.get("channel_chat_id")

    msg_id = None
    if edit_trade_id and existing_channel_msg_id and existing_channel_chat_id:
        success = await edit_channel_message(event.client, existing_channel_chat_id, existing_channel_msg_id, full_text)
        if success:
            msg_id = existing_channel_msg_id
        else:
            await event.answer("⚠️ خطا در به‌روزرسانی پیام کانال.", alert=True)
            return
    elif photos:
        msg_id = await forward_photos_then_reply_text(event.client, channel_id, photos, full_text)
    else:
        msg_id = await post_text_to_channel(event.client, channel_id, full_text)

    if msg_id:
        custom_data = {k: v for k, v in filled.items() if k not in MANDATORY_KEYS}

        if edit_trade_id:
            await db.update_trade(
                edit_trade_id, uid,
                template_id=template_id,
                symbol=filled.get("symbol", ""),
                direction=filled.get("direction", ""),
                volume=filled.get("volume"),
                entry_price=filled.get("entry_price"),
                stoploss_price=filled.get("stoploss"),
                exit_price=filled.get("exit_price"),
                trade_date=filled.get("trade_date"),
                pnl=pnl,
                pip_diff=pip_diff,
                dollar_pnl=pnl,
                multiple_risk=multiple_risk,
                risk_reward_ratio=rr,
                custom_data=custom_data,
                channel_message_id=msg_id,
                channel_chat_id=channel_id,
            )
            pending_id = edit_trade_id
        else:
            pending_id = await db.save_trade(
                user_id=uid,
                template_id=template_id,
                symbol=filled.get("symbol", ""),
                direction=filled.get("direction", ""),
                volume=filled.get("volume"),
                entry_price=filled.get("entry_price"),
                stoploss_price=filled.get("stoploss"),
                exit_price=filled.get("exit_price"),
                trade_date=filled.get("trade_date"),
                pnl=pnl,
                pip_diff=pip_diff,
                dollar_pnl=pnl,
                multiple_risk=multiple_risk,
                risk_reward_ratio=rr,
                custom_data=custom_data,
                channel_message_id=msg_id,
                channel_chat_id=channel_id,
            )

        update_state_data(uid, pending_trade_id=pending_id)

        from ..ui.keyboards import channel_post_keyboard
        direction_emoji = "📈" if filled.get("direction") == "LONG" else "📉"
        pnl_text = f"P&L: <code>{pnl:.2f}</code>" if pnl is not None else ""

        sc_text = (
            f"<b>✅ Trade saved successfully!</b>\n\n"
            f"  {direction_emoji} <b>{filled.get('symbol', 'N/A')}</b>\n"
            f"  {pnl_text}\n\n"
            f"------------------------------------\n"
            f"⭐ <b>Rate your trade</b>\n\n"
            f"Rate this trade from 1 to 6 based on strategy adherence:"
        )
        try:
            await event.edit(sc_text, buttons=scoring_keyboard(), parse_mode="html")
        except Exception:
            await event.respond(sc_text, buttons=scoring_keyboard(), parse_mode="html")
    else:
        await event.edit(
            "<b>❌ Error posting</b>\n\n"
            "Please check bot permissions in the channel.",
            buttons=[[Button.inline("Back to form", b"tj_continue_edit")]],
            parse_mode="html"
        )
        return

    clear_state(uid)


async def _handle_continue_edit(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return
    await event.answer()
    await _rerender_panel(event, uid)


async def _handle_cancel_trade(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    state = get_state(uid)
    msg_ids = state.get("data", {}).get("_message_ids", [])
    clear_state(uid)
    try:
        await event.edit("❌ Trade cancelled.", parse_mode="html")
    except Exception:
        pass
    for mid in msg_ids:
        try:
            await event.client.delete_messages(uid, mid)
        except Exception:
            pass


async def _handle_cancel_field_edit(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return
    await event.answer()
    await _rerender_panel(event, uid)


MAX_TEXT_LENGTH = 500


async def handle_text_input(event: Any, uid: int, text: str) -> None:
    state_data = get_state_data(uid)
    raw = text.strip()

    if raw.lower() == "/done" and state_data.get("photo_mode"):
        state_data["photo_mode"] = False
        set_state(uid, FILLING_FORM, state_data)
        photo_count = len(state_data.get("photos", []))
        await event.reply(f"✅ {photo_count} photos ready. Returning to form.")
        await _rerender_panel_msg(event, uid)
        return

    editing_field = state_data.get("editing_field")
    await_type = state_data.get("await_type", "text")

    if not editing_field:
        clear_state(uid)
        return

    if await_type == "number" and not is_valid_number(raw):
        await event.reply("❌ Please enter a valid number. Try again:")
        return

    if await_type == "number":
        val = parse_number(raw)
        if val is not None and val <= 0:
            await event.reply("❌ Value must be greater than zero. Try again:")
            return
        state_data["filled"][editing_field] = val
    else:
        if len(raw) > MAX_TEXT_LENGTH:
            await event.reply(f"❌ Text too long (max {MAX_TEXT_LENGTH} chars). Try again:")
            return
        state_data["filled"][editing_field] = raw

    set_state(uid, FILLING_FORM, {
        **state_data,
        "editing_field": None,
        "await_type": None,
    })

    await _rerender_panel_msg(event, uid)


async def _rerender_panel(event: Any, uid: int) -> None:
    state_data = get_state_data(uid)
    template_id = state_data.get("template_id")
    filled = state_data.get("filled", {})
    panel_msg_id = state_data.get("panel_msg_id")
    custom_fields = await get_custom_fields(template_id) if template_id else []

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    try:
        await event.edit(status_text, buttons=keyboard, parse_mode="html")
    except Exception:
        if panel_msg_id:
            try:
                await event.client.edit_message(uid, panel_msg_id, status_text,
                                                buttons=keyboard, parse_mode="html")
            except Exception as e:
                logger.error(f"Error rerendering panel: {e}")
                try:
                    msg = await event.reply(status_text, buttons=keyboard, parse_mode="html")
                    update_state_data(uid, panel_msg_id=msg.id)
                except Exception:
                    pass
        else:
            try:
                msg = await event.reply(status_text, buttons=keyboard, parse_mode="html")
                update_state_data(uid, panel_msg_id=msg.id)
            except Exception:
                pass


async def _handle_choice_select(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return
    parts = get_callback_parts(event.data)
    field_key = parts[1] if len(parts) > 1 else None
    value = parts[2] if len(parts) > 2 else None
    if not field_key or not value:
        await event.answer("انتخاب نامعتبر.", alert=True)
        return
    state_data = get_state_data(uid)
    state_data["filled"][field_key] = value
    update_state_data(uid, filled=state_data["filled"])
    await _rerender_panel(event, uid)


async def _handle_skip_scoring(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    state = get_state(uid)
    msg_ids = state.get("data", {}).get("_message_ids", [])
    clear_state(uid)
    try:
        await event.edit("✅ Trade saved.", parse_mode="html")
    except Exception:
        pass
    for mid in msg_ids:
        try:
            await event.client.delete_messages(uid, mid)
        except Exception:
            pass


async def _handle_score_set(event: Any) -> None:
    uid = event.sender_id
    from ..utils import parse_callback_int
    score = parse_callback_int(event.data)
    if score is None or score < 1 or score > 6:
        await event.answer("امتیاز نامعتبر.", alert=True)
        return

    state_data = get_state_data(uid)
    pending_trade_id = state_data.get("pending_trade_id")
    if pending_trade_id:
        try:
            await db.update_trade(pending_trade_id, uid, trade_score=score)
        except Exception as e:
            logger.error(f"Error saving score: {e}")

    clear_state(uid)
    stars = "⭐" * score
    await event.edit(
        f"<b>✅ Trade saved!</b>\n\n"
        f"Adherence score: {stars} ({score}/6)\n\n"
        "Use the button below to return to the main menu:",
        buttons=[[Button.inline("Journal Menu", b"tj_back_journal")]],
        parse_mode="html"
    )


async def _rerender_panel_msg(event: Any, uid: int) -> None:
    state_data = get_state_data(uid)
    template_id = state_data.get("template_id")
    filled = state_data.get("filled", {})
    panel_msg_id = state_data.get("panel_msg_id")
    custom_fields = await get_custom_fields(template_id) if template_id else []

    status_text = build_status_text(filled, custom_fields)
    keyboard = live_panel_keyboard(filled, custom_fields)

    if panel_msg_id:
        try:
            await event.client.edit_message(uid, panel_msg_id, status_text,
                                            buttons=keyboard, parse_mode="html")
        except Exception as e:
            logger.error(f"Error rerendering panel via msg: {e}")
            msg = await event.reply(status_text, buttons=keyboard, parse_mode="html")
            update_state_data(uid, panel_msg_id=msg.id)
    else:
        msg = await event.reply(status_text, buttons=keyboard, parse_mode="html")
        update_state_data(uid, panel_msg_id=msg.id)


async def _handle_photo_replace(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return
    parts = get_callback_parts(event.data)
    idx = int(parts[1]) if len(parts) > 1 else 0
    state_data = get_state_data(uid)
    photos = state_data.get("photos", [])
    if 0 <= idx < len(photos):
        state_data["photo_mode"] = True
        state_data["replace_idx"] = idx
        set_state(uid, AWAIT_PHOTO, state_data)
        await event.answer()
        try:
            await event.edit(
                f"<b>Replace Photo #{idx+1}</b>\n\n"
                "Send the new photo.",
                buttons=[[Button.inline("Cancel", b"tj_done_photo", style="danger")]],
                parse_mode="html"
            )
        except Exception:
            pass


async def _handle_photo_delete(event: Any) -> None:
    uid = event.sender_id
    state_str = get_state_str(uid)
    if state_str != FILLING_FORM:
        await event.answer("جلسه منقضی شده.", alert=True)
        return
    parts = get_callback_parts(event.data)
    idx = int(parts[1]) if len(parts) > 1 else 0
    state_data = get_state_data(uid)
    photos = state_data.get("photos", [])
    if 0 <= idx < len(photos):
        photos.pop(idx)
        update_state_data(uid, photos=photos)
        await event.answer(f"✅ Photo #{idx+1} deleted", alert=False)
        await _rerender_panel(event, uid)

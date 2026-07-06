import logging
from typing import Any

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
from telethon_compat import TelegramClient, events, filters, data_regex

from .. import database as db
from ..states import (
    set_state, get_state_str, get_state_data, clear_state, update_state_data,
    IDLE, AWAIT_TEMPLATE_NAME, AWAIT_FIELD_LABEL
)
from ..services.template_service import (
    add_custom_field, get_custom_fields, MANDATORY_KEYS,
    generate_field_key, format_template_preview
)
from ..ui.keyboards import (
    template_list_keyboard, template_edit_keyboard, template_glass_panel,
    template_field_list_keyboard, field_type_keyboard,
    field_section_keyboard, confirm_delete_keyboard, field_required_keyboard
)
from telethon_compat import Button  # v2: buttons via compat (v1 telethon.tl.custom.Button)
from ..utils import get_callback_parts, parse_callback_int, parse_callback_str

logger = logging.getLogger(__name__)

MAX_TEMPLATES = 5
MAX_FIELD_LABEL_LENGTH = 25


def register_template_handlers(client: TelegramClient) -> None:
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_edit:"))(_handle_tpl_edit)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_delete:"))(_handle_tpl_delete)
    client.on(events.ButtonCallback, data_regex(r"tj_confirm_delete_template:"))(_handle_tpl_confirm_delete)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_copy:"))(_handle_tpl_copy)
    client.on(events.ButtonCallback, filters.Data(b"tj_tpl_create"))(_handle_tpl_create)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_add:"))(_handle_field_add)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_type:"))(_handle_field_type)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_del:"))(_handle_field_del)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_del_list:"))(_handle_field_del_list)
    client.on(events.ButtonCallback, filters.Data(b"tj_back_templates"))(_handle_back_templates)
    client.on(events.ButtonCallback, filters.Data(b"tj_cancel_field_add"))(_handle_cancel_field_add)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_info:"))(_handle_field_info)
    client.on(events.ButtonCallback, data_regex(r"tj_template_select:"))(_handle_template_select)
    client.on(events.ButtonCallback, data_regex(r"tj_set_default:"))(_handle_set_default)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_section:"))(_handle_field_section)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_channel:"))(_handle_tpl_channel)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_channel_set:"))(_handle_tpl_channel_set)
    client.on(events.ButtonCallback, data_regex(r"tj_quick_add_field:"))(_handle_quick_add_field)
    client.on(events.ButtonCallback, data_regex(r"tj_tpl_field_required:"))(_handle_field_required)


async def _show_template_preview(event: Any, uid: int, template_id: int) -> None:
    preview = await format_template_preview(template_id)
    fields = await db.get_template_fields(template_id)
    custom = [f for f in fields if f["field_key"] not in MANDATORY_KEYS]
    template = await db.get_template(template_id)
    is_default = bool(template.get("is_default"))
    try:
        await event.edit(
            preview,
            buttons=template_glass_panel(template_id, has_fields=bool(custom), is_default=is_default),
            parse_mode="html"
        )
    except Exception:
        pass


async def _edit_preview(event: Any, uid: int, text: str, buttons=None) -> None:
    try:
        await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        try:
            msg = await event.respond(text, buttons=buttons, parse_mode="html")
            if msg and hasattr(msg, "id"):
                update_state_data(uid, _prompt_msg_id=msg.id)
        except Exception:
            pass


async def _handle_template_select(event: Any) -> None:
    uid = event.sender_id
    template_id = parse_callback_int(event.data)
    await event.answer()
    from .entry import _start_trade_with_template
    await _start_trade_with_template(event, uid, template_id)


async def _handle_set_default(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    await db.clear_all_defaults(uid)
    await db.set_template_default(template_id, True)
    await _show_template_preview(event, uid, template_id)


async def _handle_tpl_edit(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        await event.answer("قالب یافت نشد.", alert=True)
        return
    clear_state(uid)
    await _show_template_preview(event, uid, template_id)


async def _handle_tpl_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    await _edit_preview(event, uid,
        f"⚠️ حذف قالب <b>{template['name']}</b>؟",
        confirm_delete_keyboard("template", template_id)
    )


async def _handle_tpl_confirm_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    await db.delete_template(template_id)
    templates = await db.get_templates(uid)
    clear_state(uid)
    try:
        if templates:
            await event.edit(
                "✅ قالب حذف شد.",
                buttons=template_list_keyboard(templates),
                parse_mode="html"
            )
        else:
            await event.edit(
                "✅ قالب حذف شد.\n\nهیچ قالبی ندارید.",
                buttons=[[Button.inline("➕ ایجاد قالب", b"tj_tpl_create")],
                         [Button.inline("🔙 بازگشت", b"tj_back_journal")]],
                parse_mode="html"
            )
    except Exception:
        pass


async def _handle_tpl_copy(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    templates = await db.get_templates(uid)
    if len(templates) >= MAX_TEMPLATES:
        await event.answer(f"🚫 حداکثر {MAX_TEMPLATES} قالب مجاز است.", alert=True)
        return
    source_id = parse_callback_int(event.data)
    new_id = await db.copy_template(source_id, uid)
    if new_id:
        await event.answer("✅ قالب کپی شد!", alert=True)
        templates = await db.get_templates(uid)
        try:
            await event.edit(
                "<b>📋 قالب‌های شما:</b>",
                buttons=template_list_keyboard(templates),
                parse_mode="html"
            )
        except Exception:
            pass
    else:
        await event.answer("❌ خطا در کپی قالب.", alert=True)


async def _handle_tpl_create(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    templates = await db.get_templates(uid)
    if len(templates) >= MAX_TEMPLATES:
        await event.answer(f"🚫 حداکثر {MAX_TEMPLATES} قالب مجاز است.", alert=True)
        return
    await db.ensure_user(uid)
    set_state(uid, AWAIT_TEMPLATE_NAME, {
        "step": "name",
        "_bot_chat": event.chat_id,
        "_bot_msg": event.message_id
    })
    await _edit_preview(event, uid,
        "📝 <b>قالب جدید</b>\n\nنام قالب را ارسال کنید:",
        [[Button.inline("❌ لغو", b"tj_back_templates")]]
    )


async def _handle_field_add(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    preview = await format_template_preview(template_id)
    set_state(uid, AWAIT_FIELD_LABEL, {
        "template_id": template_id,
        "step": "label",
        "_bot_chat": event.chat_id,
        "_bot_msg": event.message_id
    })
    await event.edit(
        preview + "\n\n📝 <b>افزودن فیلد سفارشی</b>\n\nعنوان فیلد را ارسال کنید:",
        buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
        parse_mode="html"
    )


async def _handle_field_type(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    field_type = parts[1] if len(parts) > 1 else None
    state_data = get_state_data(uid)
    template_id = state_data.get("template_id")
    field_label = state_data.get("field_label")
    if not template_id or not field_label:
        await event.answer("جلسه منقضی شده.", alert=True)
        clear_state(uid)
        return
    update_state_data(uid, field_type=field_type)
    preview = await format_template_preview(template_id)
    if field_type == "choice":
        set_state(uid, AWAIT_FIELD_LABEL, {
            "template_id": template_id,
            "field_label": field_label,
            "field_type": field_type,
            "step": "choice_options",
            "_bot_chat": state_data.get("_bot_chat"),
            "_bot_msg": state_data.get("_bot_msg")
        })
        await event.edit(
            preview + f"\n\n🎲 <b>{field_label}</b>\n\nگزینه‌های انتخاب را با کاما از هم جدا کنید (، یا ,):\n<code>گزینه 1, گزینه 2, گزینه 3</code>",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    type_name = "چک‌باکس" if field_type == "checkbox" else "متن"
    await event.edit(
        preview + f"\n\n📝 <b>{field_label}</b> | نوع: {type_name}\n\nآیا این فیلد اجباری است یا اختیاری؟",
        buttons=field_required_keyboard(template_id, field_type, field_label),
        parse_mode="html"
    )


async def _handle_field_section(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    template_id = parse_callback_int(event.data)
    field_section = parts[2] if len(parts) > 2 else None
    field_type = parts[3] if len(parts) > 3 else None
    field_label = parts[4] if len(parts) > 4 else "field"
    field_key = generate_field_key(field_label)
    state_data = get_state_data(uid)
    choice_options = state_data.get("choice_options")
    is_required = state_data.get("is_required", 0)
    return_section = state_data.get("_return_section")
    await add_custom_field(template_id, field_key, field_label, field_type, field_section, choice_options, is_required)
    clear_state(uid)

    if return_section:
        from ..services.template_service import build_initial_filled, get_custom_fields as gcf
        from ..ui.keyboards import live_panel_keyboard
        from ..ui.formatter import build_status_text
        filled = await build_initial_filled(template_id)
        filled["_template_id"] = template_id
        custom_fields = await gcf(template_id)
        status_text = build_status_text(filled, custom_fields)
        keyboard = live_panel_keyboard(filled, custom_fields)
        set_state(uid, "FILLING_FORM", {
            "template_id": template_id,
            "filled": filled,
            "photos": [],
            "photo_mode": False,
            "panel_msg_id": state_data.get("panel_msg_id"),
            "editing_field": None,
            "edit_trade_id": None,
        })
        try:
            await event.edit(status_text, buttons=keyboard, parse_mode="html")
            update_state_data(uid, panel_msg_id=event.message_id)
        except Exception:
            try:
                msg = await event.respond(status_text, buttons=keyboard, parse_mode="html")
                update_state_data(uid, panel_msg_id=msg.id)
            except Exception:
                pass
    else:
        await _show_template_preview(event, uid, template_id)


async def _handle_field_del(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    template_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    field_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    if template_id is None or field_id is None:
        return
    await db.delete_template_field(field_id)
    template = await db.get_template(template_id)
    if template and template["user_id"] == uid:
        await _show_template_preview(event, uid, template_id)
    else:
        templates = await db.get_templates(uid)
        clear_state(uid)
        try:
            if templates:
                await event.edit(
                    "✅ فیلد حذف شد.",
                    buttons=template_list_keyboard(templates),
                    parse_mode="html"
                )
            else:
                await event.edit("✅ فیلد حذف شد.", parse_mode="html")
        except Exception:
            pass


async def _handle_field_del_list(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    fields = await db.get_template_fields(template_id)
    custom = [f for f in fields if f["field_key"] not in MANDATORY_KEYS]
    if not custom:
        await event.answer("هیچ فیلدی برای حذف وجود ندارد.", alert=True)
        return
    try:
        await event.edit(
            "🗑 <b>حذف فیلد</b>\n\nروی فیلد مورد نظر کلیک کنید:",
            buttons=template_field_list_keyboard(template_id, custom),
            parse_mode="html"
        )
    except Exception:
        pass


async def _handle_field_info(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    field_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    conn = await db._get_conn()
    cursor = await conn.execute("SELECT * FROM template_fields WHERE id = ?", (field_id,))
    row = await cursor.fetchone()
    if not row:
        await event.answer("فیلد یافت نشد.", alert=True)
        return
    f = dict(row)
    type_names = {"checkbox": "چک‌باکس", "text": "متن", "choice": "انتخابی"}
    type_name = type_names.get(f["field_type"], "سایر")
    from ..ui.formatter import SECTION_HEADERS
    section_name = SECTION_HEADERS.get(f.get("field_section", "custom"), "سایر")
    await event.answer(
        f"📝 {f['field_label']}\nنوع: {type_name}\nبخش: {section_name}",
        alert=True
    )


async def _handle_back_templates(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    clear_state(uid)
    templates = await db.get_templates(uid)
    try:
        await event.edit(
            "<b>📋 قالب‌های شما:</b>",
            buttons=template_list_keyboard(templates),
            parse_mode="html"
        )
    except Exception:
        pass


async def _handle_cancel_field_add(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    state_data = get_state_data(uid)
    template_id = state_data.get("template_id")
    clear_state(uid)
    if template_id:
        await _show_template_preview(event, uid, template_id)
    else:
        templates = await db.get_templates(uid)
        try:
            await event.edit(
                "<b>📋 قالب‌های شما:</b>",
                buttons=template_list_keyboard(templates),
                parse_mode="html"
            )
        except Exception:
            pass


async def handle_template_name_input(event: Any, uid: int, text: str) -> None:
    state_data = get_state_data(uid)
    step = state_data.get("step")
    if step != "name":
        return

    name = text.strip()
    if not name:
        try:
            await event.delete()
        except Exception:
            pass
        await event.respond(
            "❌ نام نمی‌تواند خالی باشد. دوباره وارد کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_back_templates")]],
            parse_mode="html"
        )
        return

    templates = await db.get_templates(uid)
    if len(templates) >= MAX_TEMPLATES:
        clear_state(uid)
        try:
            await event.delete()
        except Exception:
            pass
        await event.respond(f"❌ حداکثر {MAX_TEMPLATES} قالب مجاز است.")
        return

    try:
        await event.delete()
    except Exception:
        pass

    bot_chat = state_data.get("_bot_chat")
    bot_msg = state_data.get("_bot_msg")
    template_id = await db.create_template(uid, name)
    clear_state(uid)

    preview = await format_template_preview(template_id)
    if bot_chat and bot_msg:
        try:
            await event.client.edit_message(
                bot_chat, bot_msg,
                preview,
                buttons=template_glass_panel(template_id),
                parse_mode="html"
            )
            return
        except Exception:
            pass
    try:
        msg = await event.respond(
            preview,
            buttons=template_glass_panel(template_id),
            parse_mode="html"
        )
        if msg and hasattr(msg, "id"):
            update_state_data(uid, _prompt_msg_id=msg.id)
    except Exception:
        pass


async def handle_field_label_input(event: Any, uid: int, text: str) -> None:
    state_data = get_state_data(uid)
    step = state_data.get("step")
    template_id = state_data.get("template_id")
    bot_chat = state_data.get("_bot_chat")
    bot_msg = state_data.get("_bot_msg")
    if not template_id:
        return

    try:
        await event.delete()
    except Exception:
        pass

    if step == "choice_options":
        options_text = text.strip().replace("،", ",")
        if not options_text:
            preview = await format_template_preview(template_id)
            await event.respond(
                preview + "\n\n🎲 گزینه‌ها نمی‌توانند خالی باشند. دوباره وارد کنید:",
                buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
                parse_mode="html"
            )
            return
        options = [o.strip() for o in options_text.split(",") if o.strip()]
        if len(options) < 2:
            await event.respond(
                preview + "\n\n❌ حداقل 2 گزینه وارد کنید. دوباره تلاش کنید:",
                buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
                parse_mode="html"
            )
            return
        if len(options) > 8:
            options = options[:8]
        update_state_data(uid, choice_options=",".join(options), step="section")
        field_label = state_data.get("field_label")
        field_type = state_data.get("field_type", "choice")
        preview = await format_template_preview(template_id)
        section_text = preview + f"\n\n🎲 <b>{field_label}</b> | گزینه‌ها: {', '.join(options)}\n\nاین فیلد در کدام بخش قرار گیرد؟"
        if bot_chat and bot_msg:
            try:
                await event.client.edit_message(bot_chat, bot_msg, section_text, buttons=field_section_keyboard(template_id, field_type, field_label), parse_mode="html")
                return
            except Exception:
                pass
        await event.respond(section_text, buttons=field_section_keyboard(template_id, field_type, field_label), parse_mode="html")
        return

    label = text.strip()
    if not label:
        preview = await format_template_preview(template_id)
        await event.respond(
            preview + "\n\n❌ عنوان نمی‌تواند خالی باشد. دوباره وارد کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    if ":" in label:
        preview = await format_template_preview(template_id)
        await event.respond(
            preview + "\n\n❌ عنوان فیلد نمی‌تواند شامل کاراکتر «:» باشد. لطفاً اصلاح کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    if len(label) > MAX_FIELD_LABEL_LENGTH:
        preview = await format_template_preview(template_id)
        await event.respond(
            preview + f"\n\n❌ عنوان فیلد نمی‌تواند بیشتر از {MAX_FIELD_LABEL_LENGTH} کاراکتر باشد. لطفاً کوتاه‌تر بنویسید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    if len(label.split()) > 4:
        preview = await format_template_preview(template_id)
        await event.respond(
            preview + "\n\n❌ نام فیلد باید کمتر از 4 کلمه باشد. لطفاً کوتاه‌تر بنویسید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    if label.lower() in [k.lower() for k in MANDATORY_KEYS]:
        preview = await format_template_preview(template_id)
        await event.respond(
            preview + "\n\n❌ این عنوان با یک فیلد الزامی تداخل دارد. نام دیگری انتخاب کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
            parse_mode="html"
        )
        return
    update_state_data(uid, field_label=label, step="type")
    preview = await format_template_preview(template_id)
    type_text = preview + f"\n\n📝 <b>{label}</b>\n\nنوع فیلد را انتخاب کنید:"
    if bot_chat and bot_msg:
        try:
            await event.client.edit_message(bot_chat, bot_msg, type_text, buttons=field_type_keyboard(), parse_mode="html")
            return
        except Exception:
            pass
    await event.respond(type_text, buttons=field_type_keyboard(), parse_mode="html")


async def _handle_tpl_channel(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    template_id = parse_callback_int(event.data)
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    channels = await db.get_user_channels(uid)
    if not channels:
        preview = await format_template_preview(template_id)
        try:
            await event.edit(
                preview + "\n\n📺 هیچ کانالی ثبت نشده. ابتدا از تنظیمات یک کانال اضافه کنید.",
                buttons=[
                    [Button.inline("📺 افزودن کانال", b"tj_settings_channel")],
                    [Button.inline("🔙 بازگشت", f"tj_tpl_edit:{template_id}".encode())],
                ],
                parse_mode="html"
            )
        except Exception:
            pass
        return
    rows = []
    for ch in channels:
        is_selected = ch["channel_id"] == template.get("channel_id")
        mark = " ✅" if is_selected else ""
        rows.append([Button.inline(
            f"📺 {ch['channel_title']}{mark}",
            f"tj_tpl_channel_set:{template_id}:{ch['channel_id']}".encode()
        )])
    if template.get("channel_id"):
        rows.append([Button.inline("❌ جدا کردن کانال", f"tj_tpl_channel_set:{template_id}:none".encode())])
    rows.append([Button.inline("🔙 بازگشت", f"tj_tpl_edit:{template_id}".encode())])
    preview = await format_template_preview(template_id)
    try:
        await event.edit(
            preview + "\n\n📺 <b>انتخاب کانال</b>",
            buttons=rows,
            parse_mode="html"
        )
    except Exception:
        pass


async def _handle_tpl_channel_set(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    if len(parts) < 3:
        return
    try:
        template_id = int(parts[1])
    except (ValueError, IndexError):
        return
    value = parts[2]
    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    if value == "default":
        user = await db.get_user(uid)
        ch_id = user.get("channel_id")
        ch_title = user.get("channel_title")
        await db.set_template_channel(template_id, ch_id, ch_title)
    elif value == "none":
        await db.set_template_channel(template_id, None, None)
    else:
        try:
            channel_id = int(value)
            channels = await db.get_user_channels(uid)
            for ch in channels:
                if ch["channel_id"] == channel_id:
                    await db.set_template_channel(template_id, channel_id, ch.get("channel_title"))
                    break
        except (ValueError, IndexError):
            pass
    await _show_template_preview(event, uid, template_id)


async def _create_default_and_prompt(event: Any, uid: int) -> None:
    from ..services.template_service import create_default_template
    template_id = await create_default_template(uid)
    from .entry import _start_trade_with_template
    await _start_trade_with_template(event, uid, template_id)


async def _create_default_and_prompt_msg(event: Any, uid: int) -> None:
    from ..services.template_service import create_default_template
    template_id = await create_default_template(uid)
    from .entry import _start_trade_with_template_msg
    await _start_trade_with_template_msg(event, uid, template_id)


async def _handle_field_required(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    if len(parts) < 5:
        return
    try:
        template_id = int(parts[1])
    except (ValueError, IndexError):
        return
    field_type = parts[2]
    field_label = parts[3]
    is_required = int(parts[4])

    state_data = get_state_data(uid)
    update_state_data(uid, is_required=is_required, step="section")

    preview = await format_template_preview(template_id)
    req_text = "اجباری" if is_required else "اختیاری"
    await event.edit(
        preview + f"\n\n📝 <b>{field_label}</b> | نوع: {field_type} | {req_text}\n\nاین فیلد در کدام بخش قرار گیرد؟",
        buttons=field_section_keyboard(template_id, field_type, field_label),
        parse_mode="html"
    )


async def _handle_quick_add_field(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    parts = get_callback_parts(event.data)
    if len(parts) < 3:
        return
    try:
        template_id = int(parts[1])
    except (ValueError, IndexError):
        return
    section = parts[2] if len(parts) > 2 else "custom"

    template = await db.get_template(template_id)
    if not template or template["user_id"] != uid:
        return
    set_state(uid, AWAIT_FIELD_LABEL, {
        "template_id": template_id,
        "step": "label",
        "_return_section": section,
        "_bot_chat": event.chat_id,
        "_bot_msg": event.message_id
    })
    await event.edit(
        "📝 <b>افزودن فیلد سفارشی</b>\n\n"
        "عنوان فیلد را ارسال کنید:",
        buttons=[[Button.inline("❌ لغو", b"tj_cancel_field_add")]],
        parse_mode="html"
    )

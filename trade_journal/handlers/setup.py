import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom import Button

from .. import database as db
from ..states import set_state, get_state_data, clear_state, AWAIT_CHANNEL_FORWARD, AWAIT_MARGIN, IDLE
from ..services.channel_service import (
    verify_bot_admin, extract_channel_from_forward, resolve_channel_input
)
from ..utils import parse_number

logger = logging.getLogger(__name__)


def register_setup_handlers(client: TelegramClient) -> None:
    client.on(events.CallbackQuery(data=b"tj_settings_channel"))(_handle_setup_channel)
    client.on(events.CallbackQuery(data=b"tj_cancel_setup"))(_handle_cancel_setup)
    client.on(events.CallbackQuery(data=b"tj_settings_margin"))(_handle_settings_margin)


async def _handle_setup_channel(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await db.ensure_user(uid)
    set_state(uid, AWAIT_CHANNEL_FORWARD)
    try:
        await event.edit(
            "<b>📺 تنظیم کانال</b>\n\n"
            "برای ارسال ژورنال به کانال خود، یکی از روش‌های زیر را انتخاب کنید:\n\n"
            "<b>روش 1:</b> یک پیام از کانال خود را فوروارد کنید\n"
            "<b>روش 2:</b> آیدی عددی کانال را ارسال کنید (مثال: <code>-1001234567890</code>)\n"
            "<b>روش 3:</b> لینک کانال را ارسال کنید (مثال: <code>t.me/username</code>)\n\n"
            "⏳ منتظر ورودی شما...",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_setup")]],
            parse_mode="html"
        )
    except Exception:
        await event.respond(
            "<b>📺 تنظیم کانال</b>\n\n"
            "برای ارسال ژورنال به کانال خود، یکی از روش‌های زیر را انتخاب کنید:\n\n"
            "<b>روش 1:</b> یک پیام از کانال خود را فوروارد کنید\n"
            "<b>روش 2:</b> آیدی عددی کانال را ارسال کنید\n"
            "<b>روش 3:</b> لینک کانال را ارسال کنید\n\n"
            "⏳ منتظر ورودی شما...",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_setup")]],
            parse_mode="html"
        )


async def _handle_settings_margin(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await db.ensure_user(uid)
    set_state(uid, AWAIT_MARGIN)
    user = await db.get_user(uid)
    current = user.get("account_margin") if user else 0
    current_text = f"\nمارجین فعلی: <b>{current:.2f}</b>" if current else ""
    try:
        await event.edit(
            "<b>💰 ثبت مارجین حساب</b>\n\n"
            "لطفاً مارجین کل حساب معاملاتی خود را وارد کنید.\n"
            "این مقدار برای محاسبه دقیق‌تر ROI و تحلیل‌ها استفاده می‌شود."
            f"{current_text}\n\n"
            "مقدار را به عدد ارسال کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_setup")]],
            parse_mode="html"
        )
    except Exception:
        await event.respond(
            "<b>💰 ثبت مارجین حساب</b>\n\n"
            "لطفاً مارجین کل حساب معاملاتی خود را وارد کنید.\n"
            f"{current_text}\n\n"
            "مقدار را به عدد ارسال کنید:",
            buttons=[[Button.inline("❌ لغو", b"tj_cancel_setup")]],
            parse_mode="html"
        )


async def _handle_cancel_setup(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    clear_state(uid)
    try:
        await event.edit("✅ عملیات لغو شد.", parse_mode="html")
    except Exception:
        pass


async def handle_margin_input(event: Any, uid: int, text: str) -> None:
    margin = parse_number(text)
    if margin is None or margin <= 0:
        await event.reply("❌ لطفاً یک عدد معتبر وارد کنید (بزرگتر از صفر). دوباره تلاش کنید:")
        return

    await db.update_user(uid, account_margin=margin)
    clear_state(uid)

    from ..ui.keyboards import settings_keyboard
    user = await db.get_user(uid)
    has_channel = bool(user and user.get("channel_id"))

    await event.reply(
        f"✅ مارجین حساب با موفقیت ثبت شد: <b>{margin:.2f}</b>\n\n"
        "این مقدار در تحلیل‌های برآیند و محاسبه ROI استفاده خواهد شد.",
        buttons=[[Button.inline("⚙️ بازگشت به تنظیمات", b"tj_settings")]],
        parse_mode="html"
    )


async def handle_channel_input(event: Any, uid: int) -> None:
    text = (event.text or "").strip()

    if text.startswith('/'):
        clear_state(uid)
        try:
            await event.delete()
        except Exception:
            pass
        return

    channel_info = None

    if event.forward:
        channel_info = await extract_channel_from_forward(event)

    if channel_info is None and text and not text.startswith('/'):
        channel_info = await resolve_channel_input(event.client, text)

    if channel_info is None:
        kb = [[Button.inline("❌ لغو", b"tj_cancel_setup")]]
        await event.reply(
            "❌ <b>خطا در شناسایی کانال</b>\n\n"
            "ورودی شما قابل شناسایی نیست.\n\n"
            "لطفاً یکی از موارد زیر را ارسال کنید:\n"
            "• فوروارد پیام از کانال\n"
            "• آیدی عددی کانال\n"
            "• لینک کانال (t.me/username)\n\n"
            "یا دکمه لغو را بزنید.",
            buttons=kb,
            parse_mode="html"
        )
        return

    channel_id, channel_title = channel_info

    is_admin, msg = await verify_bot_admin(event.client, channel_id)
    if not is_admin:
        kb = [[Button.inline("🔄 تلاش مجدید", b"tj_settings_channel")],
              [Button.inline("❌ لغو", b"tj_cancel_setup")]]
        await event.reply(
            f"❌ <b>خطا در دسترسی</b>\n\n{msg}\n\n"
            "لطفاً مراحل زیر را انجام دهید:\n"
            "1⃣ ربات را به کانال اضافه کنید\n"
            "2⃣ به ربات دسترسی ادمین با قابلیت ارسال پیام بدهید\n"
            "3⃣ دوباره تلاش کنید",
            buttons=kb,
            parse_mode="html"
        )
        return

    await db.ensure_user(uid)
    await db.set_user_channel(uid, channel_id, channel_title)
    clear_state(uid)

    from ..ui.keyboards import template_list_keyboard
    templates = await db.get_templates(uid)

    buttons = []
    if templates:
        buttons.append([Button.inline("📋 رفتن به قالب‌ها", b"tj_templates")])
    else:
        buttons.append([Button.inline("➕ ایجاد قالب پیش‌فرض", b"tj_tpl_create")])
    buttons.append([Button.inline("📝 شروع معامله جدید", b"tj_new_trade")])
    buttons.append([Button.inline("💰 ثبت مارجین حساب", b"tj_settings_margin")])
    buttons.append([Button.inline("⚙️ تنظیمات", b"tj_settings")])

    await event.reply(
        f"✅ <b>کانال متصل شد!</b>\n\n"
        f"📺 <b>{channel_title}</b> (<code>{channel_id}</code>)\n\n"
        "ژورنال‌های شما به این کانال ارسال خواهند شد.\n\n"
        "💡 <b>پیشنهاد:</b> حالا یک قالب معاملاتی ایجاد کنید و مارجین حساب خود را وارد کنید!",
        buttons=buttons,
        parse_mode="html"
    )

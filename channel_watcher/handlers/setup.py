"""Setup flow handler — register a new channel monitor.

State machine transitions are managed by ``channel_watcher.states``.
The flow is:

    IDLE → AWAITING_CHANNEL_LINK → AWAITING_DELIVERY_CHOICE
         → (optional) AWAITING_GROUP_LINK → AWAITING_CONFIRM → IDLE

Module-level references ``_client`` and ``_bot`` are populated once
during ``register_setup_handlers()`` so that the exported handler
functions (``cw_handle_channel_link``, ``cw_handle_group_link``) can
be called from the main bot's ``pending_message_handler`` without
needing a bot or client argument.
"""

import logging
from typing import Any, Optional

from telethon import TelegramClient, events, Button
from telethon.tl.functions.channels import JoinChannelRequest

from ..database import (
    create_monitor,
    get_monitor_count,
    get_user_monitors,
)
from ..services.fetcher import ChannelFetcher, extract_username
from ..states import UserState, get_state_manager
from ..ui.keyboards import (
    back_keyboard,
    confirm_keyboard,
    delivery_choice_keyboard,
    saved_monitor_keyboard,
    empty_monitors_keyboard,
)
from ..ui.formatter import format_monitor_list_item
from ..ui.safe_edit import safe_edit

logger = logging.getLogger(__name__)

# Module-level references set during registration
_client: Optional[TelegramClient] = None
_bot: Any = None
_user_client: Optional[TelegramClient] = None
_state_mgr = get_state_manager()


async def _get_max_monitors(uid: int) -> int:
    sub = _bot.get_user_subscription(uid)
    return 5 if sub == "paid" else 1


async def _check_monitor_limit(uid: int, event) -> bool:
    count = await get_monitor_count(uid)
    limit = await _get_max_monitors(uid)
    if count >= limit:
        await event.answer(
            f"شما به حداکثر تعداد مجاز مونیتور ({limit} عدد) رسیده‌اید.",
            alert=True,
        )
        return True
    return False


# ── Exported handlers (callable from pending_message_handler) ────────


async def cw_handle_channel_link(event) -> None:
    """Handle channel link text from user in AWAITING_CHANNEL_LINK state.

    Called from ``pending_message_handler`` in ``telegram/handlers/ai.py``
    when the user is in ``AWAITING_CHANNEL_LINK`` state.
    """
    global _client, _bot, _user_client
    uid = event.sender_id
    text = (event.text or "").strip()

    fetcher = ChannelFetcher(_user_client)
    info = await fetcher.validate_channel_access(text)

    if info is None:
        await event.reply(
            "❌ لینک نامعتبر است. لطفاً یک لینک معتبر از یک کانال عمومی ارسال کنید.\n\n"
            "مثال: <code>https://t.me/channel_name</code>",
            buttons=back_keyboard("cw_back_main"),
            parse_mode="html",
        )
        return

    await _state_mgr.set_state(
        uid,
        UserState.AWAITING_DELIVERY_CHOICE,
        {
            "chat_id": info["chat_id"],
            "channel_username": info["username"],
            "channel_title": info["title"],
        },
    )
    await event.reply(
        f"✅ کانال <b>{info['title']}</b> با موفقیت شناسایی شد!\n"
        f"└ @{info['username']}\n\n"
        "حالا انتخاب کنید نتایج تحلیل کجا ارسال شود:",
        buttons=delivery_choice_keyboard(),
        parse_mode="html",
    )


async def cw_handle_group_link(event) -> None:
    """Handle group link text from user in AWAITING_GROUP_LINK state."""
    global _client, _bot, _user_client
    uid = event.sender_id
    text = (event.text or "").strip()

    fetcher = ChannelFetcher(_user_client)
    info = await fetcher.validate_group_access(text)

    if info is None:
        await event.reply(
            "❌ لینک گروه نامعتبر است یا ربات نمی‌تواند به گروه بپیوندد.\n"
            "لطفاً مطمئن شوید گروه عمومی است و ربات را به گروه اضافه کرده‌اید.",
            buttons=back_keyboard("cw_back_main"),
            parse_mode="html",
        )
        return

    # Also ensure bot is in the group so it can send messages
    try:
        bot_entity = await _client.get_entity(info["chat_id"])
        await _client(JoinChannelRequest(bot_entity))
    except Exception as exc:
        logger.warning("Bot could not join group %s: %s", info.get("username"), exc)

    _, data = await _state_mgr.get_state(uid)
    if data is None:
        return
    data["delivery_method"] = "group"
    data["group_chat_id"] = info["chat_id"]
    data["group_username"] = info["username"]
    await _state_mgr.set_state(uid, UserState.AWAITING_CONFIRM, data)
    await _show_confirmation(event, data)


# ── Internal helpers ──────────────────────────────────────────────────


async def _show_confirmation(event, data: dict) -> None:
    channel_title = data.get("channel_title", "")
    delivery = "📨 پیوی" if data.get("delivery_method") == "pm" else "👥 گروه"
    text = (
        "📋 <b>خلاصه تنظیمات</b>\n\n"
        f"📡 کانال: {channel_title}\n"
        f"📨 تحویل: {delivery}\n"
        f"🕒 بازه تحلیل: هر ۴ ساعت\n"
        f"🔥 فیلتر اهمیت: فقط پیام‌های مهم\n\n"
        "✅ برای تایید و شروع پایش، دکمه زیر را بزنید:"
    )
    await safe_edit(event, text, buttons=confirm_keyboard(), parse_mode="html")


# ── Handler registration ──────────────────────────────────────────────


def register_setup_handlers(client: TelegramClient, bot_instance: Any, user_client: Optional[TelegramClient] = None) -> None:
    """Register all setup-flow callback query handlers on the Telethon client."""
    global _client, _bot, _user_client
    _client = client
    _bot = bot_instance
    _user_client = user_client

    # Step 0: Start
    @client.on(events.CallbackQuery(data=b"cw_new_monitor"))
    async def cw_new_monitor_start(event):
        uid = event.sender_id
        if await _check_monitor_limit(uid, event):
            return
        await _state_mgr.set_state(uid, UserState.AWAITING_CHANNEL_LINK)
        await safe_edit(
            event,
            "🌐 <b>لینک کانال مورد نظر را ارسال کنید</b>\n\n"
            "مثال: <code>https://t.me/channel_name</code>\n"
            "یا: <code>@channel_name</code>\n\n"
            "📌 <i>فقط کانال‌های عمومی پشتیبانی می‌شوند</i>",
            buttons=back_keyboard("cw_back_main"),
            parse_mode="html",
        )

    # Step 2: Delivery choice
    @client.on(events.CallbackQuery(data=b"cw_delivery_pm"))
    async def cw_delivery_pm(event):
        uid = event.sender_id
        state, data = await _state_mgr.get_state(uid)
        if state is None:
            await event.answer("مهلت انتخاب به پایان رسیده, دوباره تلاش کنید.", alert=True)
            return
        data["delivery_method"] = "pm"
        data["group_chat_id"] = None
        data["group_username"] = None
        await _state_mgr.set_state(uid, UserState.AWAITING_CONFIRM, data)
        await _show_confirmation(event, data)

    @client.on(events.CallbackQuery(data=b"cw_delivery_group"))
    async def cw_delivery_group(event):
        uid = event.sender_id
        state, data = await _state_mgr.get_state(uid)
        if state is None:
            await event.answer("مهلت انتخاب به پایان رسیده.", alert=True)
            return
        await _state_mgr.set_state(uid, UserState.AWAITING_GROUP_LINK, data)
        await safe_edit(
            event,
            "👥 <b>لینک گروه را ارسال کنید</b>\n\n"
            "ربات باید عضو گروه باشد و دسترسی ارسال پیام داشته باشد.\n\n"
            "مثال: <code>https://t.me/group_name</code>\n"
            "یا: <code>@group_name</code>",
            buttons=back_keyboard("cw_back_main"),
            parse_mode="html",
        )

    # Step 4: Confirm
    @client.on(events.CallbackQuery(data=b"cw_confirm_monitor"))
    async def cw_confirm_monitor(event):
        uid = event.sender_id
        state, data = await _state_mgr.get_state(uid)
        if state is None or data is None:
            await event.answer("مهلت تایید به پایان رسیده.", alert=True)
            return

        # Lock-in point: seed last_message_id to the newest post already in
        # the channel right now, so counting starts from *this* moment —
        # posts published before the lock are never fetched or analysed.
        last_message_id = 0
        if _user_client:
            try:
                lock_fetcher = ChannelFetcher(_user_client)
                last_message_id = await lock_fetcher.get_latest_message_id(
                    data["channel_username"]
                )
            except Exception:
                logger.exception(
                    "Failed to resolve lock-in point for channel %s",
                    data.get("channel_username"),
                )

        try:
            monitor_id = await create_monitor(
                user_id=uid,
                chat_id=data["chat_id"],
                channel_username=data["channel_username"],
                channel_title=data.get("channel_title", ""),
                delivery_method=data.get("delivery_method", "pm"),
                group_chat_id=data.get("group_chat_id"),
                group_username=data.get("group_username"),
                last_message_id=last_message_id,
            )
        except Exception as exc:
            logger.exception("create_monitor failed for user %s", uid)
            await safe_edit(
                event,
                "❌ خطا در ذخیره مونیتور. لطفاً دوباره تلاش کنید.",
                buttons=back_keyboard("cw_back_main"),
            )
            await _state_mgr.clear_state(uid)
            return

        await _state_mgr.clear_state(uid)
        await safe_edit(
            event,
            "🎉 <b>پایش کانال فعال شد!</b>\n\n"
            "ربات از این به بعد هر ۴ ساعت آخرین پیام‌های کانال را بررسی "
            "و خلاصه‌ای از آن ارسال می‌کند.\n\n"
            "⚙️ می‌توانید از بخش تنظیمات، بازه تحلیل و سایر گزینه‌ها را تغییر دهید.",
            buttons=saved_monitor_keyboard(monitor_id),
            parse_mode="html",
        )

        from ..__init__ import start_monitor_task
        start_monitor_task(monitor_id, _client, _user_client, _bot)

    # List monitors
    @client.on(events.CallbackQuery(data=b"cw_list_monitors"))
    async def cw_list_monitors(event):
        try:
            uid = event.sender_id
            monitors = await get_user_monitors(uid)
            limit = await _get_max_monitors(uid)

            if not monitors:
                text = (
                    "📋 <b>لیست مونیتورهای شما</b>\n\n"
                    "شما هنوز هیچ کانالی را برای پایش ثبت نکرده‌اید.\n"
                    "برای شروع، دکمه زیر را بزنید:"
                )
                await safe_edit(event, text, buttons=empty_monitors_keyboard(), parse_mode="html")
                return

            lines = [f"📋 <b>لیست مونیتورهای شما ({len(monitors)}/{limit})</b>\n"]
            buttons = []
            for m in monitors:
                lines.append(format_monitor_list_item(m))
                buttons.append([
                    Button.inline(f"⚙️ {m.get('channel_title', '?')}", f"cw_settings_{m['id']}")
                ])

            if len(monitors) < limit:
                buttons.append([Button.inline("➕ کانال جدید", "cw_new_monitor")])
            buttons.append([Button.inline("🔙 منوی اصلی", "back_to_main")])

            await safe_edit(event, "\n".join(lines), buttons=buttons, parse_mode="html")
        except Exception as exc:
            logger.exception("cw_list_monitors failed for user %s: %s", uid, exc)
            try:
                await event.answer("خطایی رخ داد. لطفاً دوباره تلاش کنید.", alert=True)
            except Exception:
                pass

    # Back to main
    @client.on(events.CallbackQuery(data=b"cw_back_main"))
    async def cw_back_main(event):
        uid = event.sender_id
        await _state_mgr.clear_state(uid)
        from telegram.handlers.menu import back_to_main_cb
        await back_to_main_cb(_bot, event)

    # Back to edit
    @client.on(events.CallbackQuery(data=b"cw_back_edit"))
    async def cw_back_edit(event):
        uid = event.sender_id
        _, data = await _state_mgr.get_state(uid)
        if data:
            await _state_mgr.set_state(uid, UserState.AWAITING_CHANNEL_LINK, data)
        await safe_edit(
            event,
            "🌐 <b>لینک جدید کانال را ارسال کنید</b>",
            buttons=back_keyboard("cw_back_main"),
            parse_mode="html",
        )

    # Upgrade prompt
    @client.on(events.CallbackQuery(data=b"cw_upgrade_required"))
    async def cw_upgrade_required(event):
        await event.answer(
            "این قابلیت نیاز به اشتراک پولی دارد. با پشتیبانی تماس بگیرید.",
            alert=True,
        )

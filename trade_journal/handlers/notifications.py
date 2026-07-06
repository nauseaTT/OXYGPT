import logging
from typing import Any

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
# The compat layer aliases `TelegramClient` to v2's `Client`, provides the
# v2 `events` module, `filters`, and the `data_regex` helper that reproduces
# v1's `events.CallbackQuery(pattern=...)` regex-matching semantics.
from telethon_compat import TelegramClient, events, Button, filters, data_regex

from .. import database as db
from ..services.notification_service import (
    check_loss_streak, check_daily_goal, check_trade_frequency,
    generate_daily_summary
)

logger = logging.getLogger(__name__)


def register_notification_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.ButtonCallback, filters.Data(b"tj_notifications"))(wrap(_handle_notifications))
    client.on(events.ButtonCallback, filters.Data(b"tj_daily_summary"))(wrap(_handle_daily_summary))


async def _handle_notifications(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    notifications = []

    loss_msg = await check_loss_streak(uid)
    if loss_msg:
        notifications.append(loss_msg)

    goal_msg = await check_daily_goal(uid)
    if goal_msg:
        notifications.append(goal_msg)

    freq_msg = await check_trade_frequency(uid)
    if freq_msg:
        notifications.append(freq_msg)

    if not notifications:
        await event.edit(
            "🔔 <b>اعلان‌ها</b>\n\n"
            "هیچ اعلان جدیدی نیست.",
            buttons=[
                [Button.inline("📊 خلاصه روز", b"tj_daily_summary")],
                [Button.inline("🔙 بازگشت", b"tj_back_journal")],
            ],
            parse_mode="html"
        )
        return

    text = "🔔 <b>اعلان‌های شما</b>\n\n"
    text += "\n\n".join(notifications)

    await event.edit(
        text,
        buttons=[
            [Button.inline("📊 خلاصه روز", b"tj_daily_summary")],
            [Button.inline("🔙 بازگشت", b"tj_back_journal")],
        ],
        parse_mode="html"
    )


async def _handle_daily_summary(event: Any) -> None:
    uid = event.sender_id
    await event.answer()

    summary = await generate_daily_summary(uid)

    await event.edit(
        summary,
        buttons=[
            [Button.inline("🔔 اعلان‌ها", b"tj_notifications")],
            [Button.inline("🔙 بازگشت", b"tj_back_journal")],
        ],
        parse_mode="html"
    )

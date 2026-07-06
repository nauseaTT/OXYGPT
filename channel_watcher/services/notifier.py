"""Send analysis results to users or groups.

Handles PM delivery, group delivery, error notifications, and
batch-sending with rate-limit-aware delays between messages.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

# v2: `from telethon import TelegramClient, Button` -> compat re-exports.
# TelegramClient is aliased to v2's `Client`; `Button` is the compat shim that
# builds v2 `telethon.types.buttons.*` objects. The `send_message`/`send_file`
# calls in this class route through the compat wrappers (int peer -> Ref,
# `parse_mode="html"` -> `html=`, `buttons=` -> `keyboard=`).
from telethon_compat import TelegramClient, Button

from ..ui.formatter import format_analysis_card
from ..ui.keyboards import analysis_card_keyboard_with_link

logger = logging.getLogger(__name__)


class ChannelNotifier:
    """Send analysis cards and notifications to users."""

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    async def send_analysis(
        self,
        user_id: int,
        monitor: Dict[str, Any],
        analysis: Dict[str, Any],
        db,
        index: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        """Send a single analysis card to the configured delivery target."""
        text = format_analysis_card(
            monitor_title=monitor.get("channel_title", ""),
            channel_username=monitor.get("channel_username", ""),
            analysis=analysis,
            index=index,
            total=total,
            post_id=analysis.get("channel_post_id"),
        )

        mon_id = monitor["id"]
        msg_id = analysis.get("id", 0)
        channel_username = monitor.get("channel_username", "")
        post_id = analysis.get("channel_post_id", 0)

        kb = analysis_card_keyboard_with_link(mon_id, msg_id, channel_username, post_id)

        target = monitor.get("group_chat_id") or user_id
        try:
            await self.client.send_message(target, text, buttons=kb, parse_mode="html")
        except Exception as exc:
            logger.warning("send_analysis to %s failed: %s", target, exc)

    async def send_batch_analysis(
        self,
        user_id: int,
        monitor: Dict[str, Any],
        analyses: List[Dict[str, Any]],
        db,
    ) -> None:
        """Send multiple analysis cards with a 1-second delay between them."""
        total = len(analyses)
        for i, analysis in enumerate(analyses, 1):
            await self.send_analysis(user_id, monitor, analysis, db, i, total)
            if i < total:
                await asyncio.sleep(1)

    async def send_error_notification(
        self,
        user_id: int,
        monitor_id: int,
        error_message: str,
    ) -> None:
        """Notify a user that their monitor encountered an error."""
        try:
            await self.client.send_message(
                user_id,
                f"⚠️ <b>خطا در پایش کانال</b>\n\n{error_message}\n\n"
                f"می‌توانید از طریق تنظیمات مونیتور وضعیت را بررسی کنید.",
                buttons=[[Button.inline("⚙️ تنظیمات", f"cw_settings_{monitor_id}")]],
                parse_mode="html",
            )
        except Exception as exc:
            logger.warning("send_error_notification to %s failed: %s", user_id, exc)

    async def send_upgrade_prompt(
        self,
        user_id: int,
        feature_name: str,
    ) -> None:
        """Suggest the user upgrade to access a paid feature."""
        try:
            await self.client.send_message(
                user_id,
                f"🔒 <b>این قابلیت نیاز به اشتراک پولی دارد</b>\n\n"
                f"{feature_name} فقط برای کاربران دارای اشتراک ویژه در دسترس است.\n"
                f"برای خرید اشتراک با پشتیبانی تماس بگیرید.",
                parse_mode="html",
            )
        except Exception as exc:
            logger.warning("send_upgrade_prompt to %s failed: %s", user_id, exc)

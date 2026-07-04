"""Fetch messages from Telegram channels via Telethon.

Channel/group resolution uses Telethon's ``get_entity``.  Message
retrieval uses a **user session** (``_user_client``) — a real Telegram
account rather than the bot account — so ``iter_messages`` works on any
public channel the account can see, with no admin requirement.

Incremental fetching is driven by the monitor's ``last_message_id``
high-water mark: each cycle only pulls messages *newer* than the last one
seen.  This guarantees every post is counted in the activity stats
exactly once (no double counting) and that busy channels don't silently
drop posts beyond a fixed window.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Channel, Chat

logger = logging.getLogger(__name__)

LINK_REGEX = re.compile(r"^(?:https?://t\.me/|@)?(\w{5,32})$")

# Fallback only: legacy monitors created before ``last_message_id`` was
# seeded at creation time may still have ``last_message_id == 0`` on their
# very first check. In that case there is no lock-in point to anchor on,
# so we fall back to the most recent few posts instead of the channel's
# entire backlog. New monitors seed ``last_message_id`` at creation
# (see ``ChannelFetcher.get_latest_message_id`` / ``create_monitor``), so
# this branch should not be hit for them.
BASELINE_LIMIT = 10
# Hard ceiling on posts pulled in a single cycle, protects against a
# burst of messages (e.g. an admin dumping 50 posts at once) flooding the
# analyzer / the user.
MAX_PER_CYCLE = 10


def extract_username(text: str) -> Optional[str]:
    m = LINK_REGEX.match(text.strip())
    return m.group(1) if m else None


class ChannelFetcher:
    """High-level channel operations over a Telethon client."""

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    # ── Validation (setup flow) ──────────────────────────────────────

    async def validate_channel_access(self, channel_link: str) -> Optional[Dict[str, Any]]:
        username = extract_username(channel_link)
        if not username:
            return None
        try:
            entity = await self.client.get_entity(username)
        except (UsernameNotOccupiedError, ValueError, ChannelPrivateError):
            return None
        except Exception as exc:
            logger.warning("get_entity(%s) failed: %s", username, exc)
            return None
        if not isinstance(entity, Channel) or getattr(entity, "username", None) is None:
            return None
        return {
            "chat_id": entity.id,
            "username": entity.username,
            "title": getattr(entity, "title", "") or entity.username,
        }

    async def validate_group_access(self, group_link: str) -> Optional[Dict[str, Any]]:
        username = extract_username(group_link)
        if not username:
            return None
        try:
            entity = await self.client.get_entity(username)
        except Exception:
            return None
        # Accept both legacy groups (Chat) and supergroups (megagroup Channel).
        is_group = isinstance(entity, Chat) or (
            isinstance(entity, Channel) and getattr(entity, "megagroup", False)
        )
        if not is_group:
            return None
        try:
            await self.client(JoinChannelRequest(entity))
        except Exception as exc:
            logger.warning("JoinChannelRequest(%s) failed: %s", username, exc)
            return None
        return {
            "chat_id": entity.id,
            "username": getattr(entity, "username", None) or "",
            "title": getattr(entity, "title", "") or getattr(entity, "username", "") or "",
        }

    # ── Lock-in point (setup flow) ────────────────────────────────────

    async def get_latest_message_id(self, username_or_chat_id: Any) -> int:
        """Return the id of the newest post currently in the channel.

        Used at monitor-creation time to seed ``last_message_id`` so the
        "lock" moment becomes the counting boundary: only posts published
        *after* this id are ever fetched/analysed. Returns ``0`` if the
        channel is empty or can't be resolved/read (the caller then falls
        back to the baseline-fetch behaviour on the first cycle).
        """
        try:
            entity = await self.client.get_entity(username_or_chat_id)
        except Exception as exc:
            logger.warning("get_latest_message_id: get_entity(%r) failed: %s", username_or_chat_id, exc)
            return 0
        try:
            async for msg in self.client.iter_messages(entity, limit=1):
                if msg and msg.id:
                    return msg.id
        except Exception as exc:
            logger.warning("get_latest_message_id: iter_messages(%r) failed: %s", username_or_chat_id, exc)
        return 0

    # ── Message fetching (scheduler) ─────────────────────────────────

    async def _resolve_entity(self, monitor: Dict[str, Any]) -> Optional[Any]:
        """Resolve the channel entity, preferring the stable username."""
        username = monitor.get("channel_username")
        chat_id = monitor.get("chat_id")
        for ref in (username, chat_id):
            if not ref:
                continue
            try:
                return await self.client.get_entity(ref)
            except Exception as exc:
                logger.debug("get_entity(%r) failed: %s", ref, exc)
        logger.warning(
            "Monitor %s: cannot resolve channel %s/%s",
            monitor.get("id"), username, chat_id,
        )
        return None

    async def fetch_new_messages(
        self, monitor: Dict[str, Any], db
    ) -> List[Dict[str, Any]]:
        """Fetch posts newer than the monitor's ``last_message_id``.

        Returns ``[{id, message, text, sender_id, date}, ...]`` in
        chronological (oldest-first) order.  Updates the activity stats
        and advances ``last_message_id`` as a side effect.  Returns an
        empty list when nothing new is available or the channel can't be
        read.
        """
        monitor_id = monitor["id"]
        last_id = monitor.get("last_message_id") or 0

        entity = await self._resolve_entity(monitor)
        if entity is None:
            return []

        # First-ever check: establish a baseline from recent posts only.
        if last_id <= 0:
            iter_kwargs = {"limit": BASELINE_LIMIT}
        else:
            iter_kwargs = {"limit": MAX_PER_CYCLE, "min_id": last_id}

        collected: List[Dict[str, Any]] = []
        max_seen = last_id
        try:
            async for msg in self.client.iter_messages(entity, **iter_kwargs):
                if not msg or not msg.id:
                    continue
                if msg.id > max_seen:
                    max_seen = msg.id

                # NOTE: we deliberately do NOT skip ``msg.out`` here.
                # ``out`` means "sent by the account this ``_user_client``
                # session is logged in as" — it has nothing to do with
                # whether a post belongs to the monitored channel. If the
                # user monitors a channel they themselves own/administer
                # (a very normal setup, not just testing), every post
                # they publish is marked ``out=True`` from their own
                # session's point of view and would be silently dropped
                # here, making the monitor look broken ("no new messages"
                # forever) with no error anywhere. A channel's posts are
                # channel content regardless of who authored them.

                # Count every real post in the activity stats (once).
                post_date, post_hour = self._msg_date_parts(msg)
                await db.increment_post_count(monitor_id, post_date, post_hour)

                text = (msg.text or msg.message or "").strip()
                if not text:
                    continue  # media-only / non-text post — stat only
                if await db.is_message_analyzed(monitor_id, msg.id):
                    continue

                collected.append({
                    "id": msg.id,
                    "message": text,
                    "text": text,
                    "sender_id": self._sender_id(msg),
                    "date": msg.date.isoformat() if msg.date else "",
                })
        except (UsernameNotOccupiedError, ChannelPrivateError) as exc:
            logger.warning("Monitor %s: channel became inaccessible: %s", monitor_id, exc)
            return []
        except Exception as exc:
            logger.warning("Monitor %s: iter_messages failed: %s", monitor_id, exc)
            return []

        # Advance the high-water mark so these posts are never re-counted.
        if max_seen > last_id:
            await db.set_last_message_id(monitor_id, max_seen)

        collected.reverse()  # chronological
        return collected

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _sender_id(msg: Any) -> Optional[int]:
        sender = msg.sender_id
        if not sender:
            return None
        return getattr(sender, "user_id", sender)

    @staticmethod
    def _msg_date_parts(msg: Any):
        """Return ``(YYYY-MM-DD, hour)`` for a message in Tehran time."""
        from datetime import datetime, timezone, timedelta
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Tehran")
        except ImportError:  # pragma: no cover
            tz = timezone(timedelta(hours=3, minutes=30))
        dt = msg.date
        if dt is None:
            dt = datetime.now(tz)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
        else:
            dt = dt.astimezone(tz)
        return dt.strftime("%Y-%m-%d"), dt.hour

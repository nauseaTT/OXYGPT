"""Fetch messages from Telegram channels via Telethon.

Channel/group resolution uses Telethon v2's ``resolve_username`` (for
``@name``/``t.me/name`` links) or a ``ChannelRef`` built from a stored id.
Message retrieval uses a **user session** (``_user_client``) — a real
Telegram account rather than the bot account — so ``get_messages`` works
on any public channel the account can see, with no admin requirement.

Incremental fetching is driven by the monitor's ``last_message_id``
high-water mark: each cycle only pulls messages *newer* than the last one
seen.  This guarantees every post is counted in the activity stats
exactly once (no double counting) and that busy channels don't silently
drop posts beyond a fixed window.

v2 migration notes for this file:
- ``client.get_entity(...)`` was removed. Usernames resolve via
  ``client.resolve_username(name)``; stored numeric ids are wrapped in a
  ``ChannelRef`` (no entity cache, no ``-100`` marked ids).
- ``client.iter_messages(entity, limit=, min_id=)`` -> ``client.get_messages(
  peer, limit)`` (an awaitable async-iterable). v2's ``get_messages`` has NO
  ``min_id`` parameter, so the "only newer than last_id" boundary is enforced
  by filtering ``msg.id > last_id`` in Python (behavior preserved).
- ``client(JoinChannelRequest(entity))`` -> ``client(
  tl.functions.channels.join_channel(channel=<InputChannel>))``.
- v2 high-level peer types: ``Channel`` (broadcast) and ``Group`` (legacy
  groups *and* supergroups, distinguished by ``.is_megagroup``); there is no
  separate ``Chat`` type, and ``.title`` is now ``.name``.
"""

import logging
import re
from typing import Any, Dict, List, Optional

# v2: `from telethon import TelegramClient` -> compat alias to v2 `Client`.
# v2: `telethon.errors` is a factory; the compat layer re-exports the concrete
#     error classes. v2: raw API is private+snake_case, exposed as `tl`.
# v2: high-level peer types `Channel`/`Group` come from `telethon.types`.
from telethon_compat import (
    TelegramClient,
    tl,
    channel_ref_from_stored_id,
    ChannelPrivateError,
    UsernameNotOccupiedError,
    # v2: high-level peer types re-exported by the compat layer (was
    # `from telethon.types import Channel, Group` — routed through compat so
    # every Telethon symbol flows through one place).
    Channel,
    Group,
)

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
            # v2: `get_entity(username)` -> `resolve_username(username)`, which
            #     returns a high-level `Channel`/`Group`/`User` peer.
            entity = await self.client.resolve_username(username)
        except (UsernameNotOccupiedError, ValueError, ChannelPrivateError):
            return None
        except Exception as exc:
            logger.warning("resolve_username(%s) failed: %s", username, exc)
            return None
        # v2: a broadcast channel is `Channel`; `.title` is now `.name`.
        if not isinstance(entity, Channel) or getattr(entity, "username", None) is None:
            return None
        return {
            "chat_id": entity.id,
            "username": entity.username,
            "title": getattr(entity, "name", "") or entity.username,
        }

    async def validate_group_access(self, group_link: str) -> Optional[Dict[str, Any]]:
        username = extract_username(group_link)
        if not username:
            return None
        try:
            # v2: `get_entity(username)` -> `resolve_username(username)`.
            entity = await self.client.resolve_username(username)
        except Exception:
            return None
        # v2: legacy groups AND supergroups are both the high-level `Group` type
        #     (distinguished by `.is_megagroup`); there is no separate `Chat`.
        is_group = isinstance(entity, Group)
        if not is_group:
            return None
        try:
            # v2: `client(JoinChannelRequest(entity))` ->
            #     `client(tl.functions.channels.join_channel(channel=<InputChannel>))`.
            #     `join_channel` only applies to supergroups/channels (a `Group`
            #     whose `.ref` is a `ChannelRef` with `_to_input_channel()`).
            #     Public `t.me/...` links are always supergroups, so this covers
            #     the real-world path. A legacy basic group (`GroupRef`, joined
            #     by invite only) has no `_to_input_channel`; we skip the join —
            #     the account must already be a member, matching v1's fallback
            #     where a failed join was tolerated.
            ref = entity.ref
            if hasattr(ref, "_to_input_channel"):
                await self.client(tl.functions.channels.join_channel(
                    channel=ref._to_input_channel()
                ))
        except Exception as exc:
            logger.warning("join_channel(%s) failed: %s", username, exc)
            return None
        return {
            "chat_id": entity.id,
            "username": getattr(entity, "username", None) or "",
            "title": getattr(entity, "name", "") or getattr(entity, "username", "") or "",
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
            # v2: resolve to a peer without the removed `get_entity` (see
            #     `_resolve_ref` which handles both username and stored id).
            peer = await self._resolve_ref(username_or_chat_id)
        except Exception as exc:
            logger.warning("get_latest_message_id: resolve(%r) failed: %s", username_or_chat_id, exc)
            return 0
        if peer is None:
            return 0
        try:
            # v2: `iter_messages(entity, limit=1)` -> `get_messages(peer, 1)`,
            #     an awaitable async-iterable. The newest message comes first.
            async for msg in self.client.get_messages(peer, 1):
                if msg and msg.id:
                    return msg.id
        except Exception as exc:
            logger.warning("get_latest_message_id: get_messages(%r) failed: %s", username_or_chat_id, exc)
        return 0

    async def _resolve_ref(self, ref: Any) -> Optional[Any]:
        """Resolve a username string or a stored channel id into a v2 peer/ref.

        v2: `get_entity` is gone. A `str` (with/without `@`) resolves via
        `resolve_username`; an int (stored channel id, possibly `-100`-marked)
        becomes a `ChannelRef` directly, which every `get_messages` call accepts.
        """
        if ref is None:
            return None
        if isinstance(ref, str):
            name = extract_username(ref) or ref.lstrip("@")
            return await self.client.resolve_username(name)
        if isinstance(ref, int):
            return channel_ref_from_stored_id(ref)
        # Already a peer/ref.
        return ref

    # ── Message fetching (scheduler) ─────────────────────────────────

    async def _resolve_entity(self, monitor: Dict[str, Any]) -> Optional[Any]:
        """Resolve the channel peer, preferring the stable username."""
        username = monitor.get("channel_username")
        chat_id = monitor.get("chat_id")
        for ref in (username, chat_id):
            if not ref:
                continue
            try:
                # v2: `get_entity(ref)` -> `_resolve_ref(ref)` (username via
                #     resolve_username, stored id via ChannelRef).
                peer = await self._resolve_ref(ref)
                if peer is not None:
                    return peer
            except Exception as exc:
                logger.debug("resolve(%r) failed: %s", ref, exc)
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
        # v2: `iter_messages(entity, limit=, min_id=last_id)` -> `get_messages(
        #     peer, limit)`. v2's `get_messages` has NO `min_id`, so we request
        #     a bounded number of the most-recent posts and enforce the
        #     "only newer than last_id" boundary ourselves via `msg.id > last_id`
        #     below (behaviour preserved). On the first check (last_id<=0) there
        #     is no boundary, so every returned post is accepted.
        limit = BASELINE_LIMIT if last_id <= 0 else MAX_PER_CYCLE

        collected: List[Dict[str, Any]] = []
        max_seen = last_id
        try:
            async for msg in self.client.get_messages(entity, limit):
                if not msg or not msg.id:
                    continue
                # v2: manual replacement for the removed `min_id` filter.
                if last_id > 0 and msg.id <= last_id:
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

                # v2: v1 exposed both `msg.text` (formatted) and `msg.message`
                #     (raw). v2 unified these into `msg.text`; the compat layer
                #     still provides a `.message` alias, so this keeps working,
                #     but `.text` alone is now the canonical source.
                text = (msg.text or "").strip()
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
        # v2: v1 `msg.sender_id` was removed; the compat layer re-adds it as a
        #     property returning `msg.sender.id` (an int) or None. The
        #     `getattr(sender, "user_id", sender)` below is now effectively a
        #     no-op for the int result but is kept for defensive compatibility.
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

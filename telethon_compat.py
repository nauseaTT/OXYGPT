"""Telethon v1 -> v2 compatibility layer for OXYGPT.

Architecture role
-----------------
This module sits *between* the OXYGPT application code and the Telethon v2
library. Telethon v2 is a full rewrite with a radically different public API;
migrating ~575 message-producing call sites and ~297 keyboard sites by hand,
one argument at a time, would be error-prone and unreviewable. Instead, this
single, thoroughly-documented module adapts the small set of *argument shapes*
the application relies on so that existing v1-style calls execute correctly
against v2 objects.

It is NOT a way to "keep v1 around". Every Telethon object that flows through
the app is a genuine v2 object; this layer only:

  1. Re-exports the v2 names the code imports (`TelegramClient`, `events`,
     `Button`, `errors`, `types`, `tl`).
  2. Provides a `Button` shim whose classmethods (`inline`, `url`,
     `switch_inline`, `text`) build the corresponding v2 button instances
     (`types.Button.Callback`, `.Url`, `.SwitchInline`, `.Text`).
  3. Monkeypatches the v2 `Message` reply/respond/edit methods and the v2
     `Client` send/edit methods so they accept the v1 keyword arguments
     `parse_mode=` and `buttons=` (translating them to v2's `html=`/`markdown=`
     /`text=` and `keyboard=`).
  4. Re-adds the convenience accessors that v2 dropped from the callback event
     (`ButtonCallback.edit`, `.respond`, `.delete`, `.sender_id`, `.chat_id`,
     `.query`), which the handlers use heavily.
  5. Adds a `data_regex(...)` event filter that reproduces v1's
     `events.CallbackQuery(pattern=...)` `re.match` semantics (including the
     negative-lookahead prefix-collision cases used in `bot.py`).
  6. Provides peer-construction helpers (`user_ref`, `channel_ref_from_stored_id`)
     because v2 removed the on-disk entity cache and the `-100`/`-` marked-id
     convention.
  7. Provides a `typing_action(...)` async context manager to replace the
     removed `client.action(...)`.

Everything here is documented at the point of definition with a
`# v2:` note explaining the v1 -> v2 mapping.

Migrated to Telethon v2 (2.0.0a0). See MIGRATION_NOTES.md.
"""

from __future__ import annotations

import re
import asyncio
import logging
from typing import Any, Optional, Sequence, Union, Callable, List

import telethon
from telethon import Client as _V2Client
from telethon import events as _events
from telethon import types as _types
from telethon import errors as _errors
from telethon import _tl as tl  # v2: raw API is now private and snake_case
from telethon.types import (
    Message as _Message,
    InlineKeyboard as _InlineKeyboard,
    Keyboard as _Keyboard,
    UserRef as _UserRef,
    ChannelRef as _ChannelRef,
    GroupRef as _GroupRef,
    PeerRef as _PeerRef,
)
# v2: the concrete button classes live in the public `telethon.types.buttons`
# submodule (Callback / Url / SwitchInline / Text), not as attributes of the
# `Button` base class.
from telethon.types import buttons as _v2buttons

# v2 keyboard-delivery workaround: the installed Telethon v2 alpha
# (git commit 583cfa6b, the current head of the upstream `v2` branch) does NOT
# actually deliver an inline keyboard to Telegram when it is passed through the
# high-level `keyboard=` argument of `Client.send_message` /
# `Message.reply` / `Message.respond` / `Client.edit_message`. The request that
# reaches the wire serialises identically, yet the server renders no buttons.
# Building the *same* `messages.sendMessage` / `messages.editMessage` request by
# hand and invoking it directly (`await client(request)`) delivers the buttons
# correctly. This was proven with side-by-side live sends (high-level path =
# no buttons; raw-function path = buttons render). To keep every caller in the
# codebase unchanged, the compat layer detects a keyboard and routes those
# sends/edits through the raw functions below. Text-only messages keep using
# the untouched high-level methods.
from telethon._impl.tl import functions as _tlfns
from telethon._impl.tl import types as _tltypes
from telethon._impl.client.types import (
    parse_message as _parse_message,
    generate_random_id as _generate_random_id,
)

_logger = logging.getLogger("telethon_compat")

# ---------------------------------------------------------------------------
# 1. Public name re-exports
# ---------------------------------------------------------------------------

# v2: `telethon.TelegramClient` was renamed to `telethon.Client`. We keep the
# old, descriptive name as an alias so the ~61 references and every
# `client: TelegramClient` type hint continue to resolve, now to the v2 class.
TelegramClient = _V2Client
Client = _V2Client

# v2: `telethon.events` still exists but events are separate from filters.
events = _events

# v2: filters are standalone, combinable predicates in `telethon.events.filters`
# (combine with `&`, `|`, `~`). Re-exported so call sites can use
# `filters.Incoming()`, `filters.Data(b"..")`, etc.
from telethon.events import filters  # noqa: E402

# v2: `telethon.types` holds all type-hint targets and concrete types.
types = _types

# v2: `telethon.errors` is a *factory* object (not a module). Accessing any
# attribute returns/creates the corresponding error class on demand, so new
# server-side errors can be caught without upgrading the library. We re-export
# it and also expose the specific names the code catches (see section 5).
errors = _errors

# v2: the raw/scheme API moved to the private `telethon._tl` namespace and uses
# snake_case function names. Re-exported here so call sites can `from
# telethon_compat import tl` and use `tl.functions.*` / `tl.types.*` / `tl.abcs.*`.
__all__ = [
    "TelegramClient", "Client", "events", "types", "errors", "tl", "Button",
    "InlineKeyboard", "data_regex", "user_ref", "channel_ref_from_stored_id",
    "peer_ref_from_stored_id", "strip_channel_mark", "typing_action",
    "text_regex", "filters",
    # error aliases:
    "FloodWaitError", "MessageNotModifiedError", "MessageIdInvalidError",
    "MessageEmptyError", "BadRequestError", "UserNotParticipantError",
    "ChatAdminRequiredError", "ChannelPrivateError", "UsernameNotOccupiedError",
    "photo_dedup_key",
    # high-level peer types:
    "Channel", "Group", "User",
]

InlineKeyboard = _InlineKeyboard

# v2: high-level peer types. `get_entity`/marked-id peers are gone; `resolve_*`
# now returns concrete `Channel` (broadcast), `Group` (legacy + supergroup,
# exposes `.is_megagroup`), or `User` peers. There is no `Chat` type in v2.
# Re-exported here so call sites `from telethon_compat import Channel, Group`
# instead of reaching into `telethon.types` directly (keeps the golden rule:
# all Telethon symbols flow through the compat layer).
Channel = _types.Channel
Group = _types.Group
User = _types.User


# ---------------------------------------------------------------------------
# 5. Error aliases (defined early; used by other modules' `except` clauses)
# ---------------------------------------------------------------------------
# v2: error classes are produced on demand by the `errors` factory. Both the
# suffixed and unsuffixed spellings resolve to the same class (the factory
# normalizes the name), so `errors.FloodWait` and `errors.FloodWaitError` are
# equivalent. We expose the `*Error` spellings the v1 code used.
#
# IMPORTANT v2 field rename: `FloodWaitError.seconds` no longer exists; the
# wait time in seconds is now `FloodWaitError.value`. Call sites that read the
# delay must use `.value` (see animator.py and channel_watcher/ui/safe_edit.py).
FloodWaitError = errors.FloodWaitError
MessageNotModifiedError = errors.MessageNotModifiedError
MessageIdInvalidError = errors.MessageIdInvalidError
MessageEmptyError = errors.MessageEmptyError
BadRequestError = errors.BadRequestError
UserNotParticipantError = errors.UserNotParticipantError
ChatAdminRequiredError = errors.ChatAdminRequiredError
ChannelPrivateError = errors.ChannelPrivateError
UsernameNotOccupiedError = errors.UsernameNotOccupiedError


# ---------------------------------------------------------------------------
# 2. Button shim
# ---------------------------------------------------------------------------
class Button:
    """v1-compatible facade over the v2 button classes.

    v2: v1 exposed factory *methods* on a single `Button` type
    (`Button.inline(text, data)`, `Button.url(text, url)`,
    `Button.switch_inline(text, query)`, `Button.text(text)`). v2 replaces
    these with dedicated subclasses under `telethon.types.Button`
    (`Callback`, `Url`, `SwitchInline`, `Text`). This shim keeps the old
    call syntax and returns the appropriate v2 instance, so the ~297 existing
    `Button.inline(...)` sites need no change.
    """

    @staticmethod
    def inline(text: str, data: Optional[Union[str, bytes]] = None, **_ignored: Any) -> Any:
        """v2: `Button.inline(text, data)` -> `types.Button.Callback(text, data)`.

        Callback payloads in v2 are `bytes`. v1 accepted either `str` or
        `bytes`; we encode `str` to UTF-8 to match v1's implicit behavior.
        `**_ignored` swallows v1-only kwargs such as `style="danger"` which
        have no v2 equivalent (inline buttons never rendered a color anyway).
        """
        if data is None:
            data = text
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        return _v2buttons.Callback(text, data)

    @staticmethod
    def url(text: str, url: Optional[str] = None, **_ignored: Any) -> Any:
        """v2: `Button.url(text, url)` -> `types.Button.Url(text, url)`."""
        return _v2buttons.Url(text, url)

    @staticmethod
    def switch_inline(text: str, query: str = "", **_ignored: Any) -> Any:
        """v2: `Button.switch_inline(...)` -> `types.Button.SwitchInline(text, query)`."""
        return _v2buttons.SwitchInline(text, query)

    @staticmethod
    def text(text: str, **_ignored: Any) -> Any:
        """v2: `Button.text(text)` -> `types.Button.Text(text)` (reply keyboard button)."""
        return _v2buttons.Text(text)


# ---------------------------------------------------------------------------
# Keyboard normalization
# ---------------------------------------------------------------------------
def _normalize_keyboard(buttons: Any) -> Optional[Any]:
    """Turn a v1-style `buttons=` value into a v2 keyboard object.

    v2: v1 accepted `buttons=` as a single button, a single row (list of
    buttons), or a list of rows (list of lists). It then built the right
    reply-markup automatically. v2 requires an explicit keyboard object
    (`InlineKeyboard` for inline callback/url buttons). This helper reproduces
    v1's flexible acceptance and wraps everything into `InlineKeyboard`, which
    is what every keyboard in this codebase is (all buttons are
    `Callback`/`Url`/`SwitchInline`).
    """
    if buttons is None:
        return None
    # Already a v2 keyboard object -> pass through unchanged.
    if isinstance(buttons, (_Keyboard, _InlineKeyboard)):
        return buttons

    # Normalize to a list-of-rows shape.
    if not isinstance(buttons, (list, tuple)):
        # single button
        rows = [[buttons]]
    elif len(buttons) == 0:
        return None
    elif isinstance(buttons[0], (list, tuple)):
        # already list of rows
        rows = [list(r) for r in buttons]
    else:
        # single row of buttons
        rows = [list(buttons)]

    # v2: InlineKeyboard takes a list of rows of inline buttons. All buttons in
    # this project are inline (Callback/Url/SwitchInline), so InlineKeyboard is
    # always correct here.
    return _InlineKeyboard(rows)


def _resolve_parse_mode(text: Any, parse_mode: Any) -> dict:
    """Translate a v1 `parse_mode=` into the correct v2 message keyword.

    v2: there is no global `client.parse_mode`. Each send/edit call must state
    how its text is interpreted via one of `text=` (plain), `html=` or
    `markdown=`. This function inspects the v1 `parse_mode` argument and returns
    the single keyword dict to splat into the v2 call, preserving HTML-vs-plain
    semantics exactly:

        parse_mode in {"html","HTML"}          -> {"html": text}
        parse_mode in {"md","markdown","MARKDOWN"} -> {"markdown": text}
        parse_mode is None / absent            -> {"text": text}   (plain)
    """
    if text is None:
        return {}
    if parse_mode is None:
        return {"text": text}
    pm = str(parse_mode).lower()
    if pm in ("html",):
        return {"html": text}
    if pm in ("md", "markdown"):
        return {"markdown": text}
    # Unknown value: fall back to plain text (matches v1's "no parser" behavior).
    return {"text": text}


def _resolve_caption_parse_mode(caption: Any, parse_mode: Any) -> dict:
    """Same as `_resolve_parse_mode` but for `send_file` caption keywords.

    v2: files use `caption=` (plain), `caption_html=` or `caption_markdown=`.
    """
    if caption is None:
        return {}
    if parse_mode is None:
        return {"caption": caption}
    pm = str(parse_mode).lower()
    if pm in ("html",):
        return {"caption_html": caption}
    if pm in ("md", "markdown"):
        return {"caption_markdown": caption}
    return {"caption": caption}


# ---------------------------------------------------------------------------
# 2b. Raw-function keyboard delivery (workaround for the v2 alpha bug)
# ---------------------------------------------------------------------------
# Why this exists: in the installed Telethon v2 alpha the high-level
# `keyboard=` argument builds a `messages.sendMessage`/`messages.editMessage`
# request whose `reply_markup` flag bit (0x4) is NEVER set during
# serialisation, so the button payload is dropped and the server renders no
# buttons. This was proven on the wire: the high-level request serialised to
# `flags=0x2` with no markup bytes, while a hand-built raw request with the
# same keyboard serialised to `flags=0x6` and carried the full
# `ReplyInlineMarkup`. The raw request delivers buttons correctly.
#
# These helpers rebuild the request by hand and invoke it directly
# (`await client(request)`), which is the only reliable way to attach an
# inline keyboard with this Telethon build. They are used ONLY when a keyboard
# is present; text-only sends/edits keep using the untouched high-level path.


def _peer_input_and_ref(peer: Any):
    """Return `(input_peer, peer_ref)` for a coerced peer.

    `_coerce_peer` yields either a `*Ref` (which exposes `_to_input_peer()` and
    `_ref`) or a concrete `Peer` (User/Group/Channel, which exposes `.ref`).
    Both `messages.sendMessage` (needs an `InputPeer`) and
    `Client._build_message_map` (needs a `PeerRef`) are fed from here.
    """
    ref = peer
    if hasattr(peer, "ref") and isinstance(getattr(peer, "ref"), _PeerRef):
        ref = peer.ref
    input_peer = ref._to_input_peer()
    return input_peer, ref


def _entities_for(text_kw: dict):
    """Turn a resolved `{text|html|markdown: ...}` dict into `(message, entities)`.

    Mirrors what the high-level path does internally via `parse_message`, so the
    raw request carries identical formatting entities.
    """
    text = text_kw.get("text")
    html = text_kw.get("html")
    markdown = text_kw.get("markdown")
    if text is None and html is None and markdown is None:
        return "", None
    message, entities = _parse_message(
        text=text, markdown=markdown, html=html, allow_empty=True
    )
    return message, entities


async def _raw_send_message(client: Any, peer: Any, text_kw: dict, keyboard: Any,
                            *, reply_to: Any = None, link_preview: bool = False):
    """Send a message with an inline keyboard via the raw `messages.sendMessage`.

    This bypasses the broken high-level `keyboard=` serialisation. `keyboard` is
    a v2 keyboard object exposing `._raw` (a `ReplyMarkup`). Returns the same
    kind of value the high-level `send_message` returns (a `Message`).
    """
    input_peer, ref = _peer_input_and_ref(peer)
    message, entities = _entities_for(text_kw)
    reply_to_obj = None
    if reply_to is not None:
        reply_to_obj = _tltypes.InputReplyToMessage(
            reply_to_msg_id=int(reply_to), top_msg_id=None
        )
    random_id = _generate_random_id()
    request = _tlfns.messages.send_message(
        no_webpage=not link_preview,
        silent=False,
        background=False,
        clear_draft=False,
        noforwards=False,
        update_stickersets_order=False,
        peer=input_peer,
        reply_to=reply_to_obj,
        message=message,
        random_id=random_id,
        reply_markup=keyboard._raw,
        entities=entities,
        schedule_date=None,
        send_as=None,
    )
    result = await client(request)
    # Mirror the high-level `send_message` return handling exactly. Private
    # chats answer with `UpdateShortSentMessage` (which carries the new id but
    # is not covered by `_build_message_map`), everything else goes through the
    # message map keyed by our random id.
    if isinstance(result, _tltypes.UpdateShortSentMessage):
        from telethon.types import Message as _Msg
        return _Msg._from_defaults(
            client,
            {},
            out=result.out,
            id=result.id,
            from_id=(
                _tltypes.PeerUser(user_id=client._session.user.id)
                if client._session.user
                else None
            ),
            peer_id=ref._to_peer(),
            reply_to=(
                _tltypes.MessageReplyHeader(
                    reply_to_scheduled=False,
                    forum_topic=False,
                    reply_to_msg_id=int(reply_to),
                    reply_to_peer_id=None,
                    reply_to_top_id=None,
                )
                if reply_to
                else None
            ),
            date=result.date,
            message=message,
            media=result.media,
            entities=result.entities,
            ttl_period=result.ttl_period,
        )
    return client._build_message_map(result, ref).with_random_id(random_id)


async def _raw_edit_message(client: Any, peer: Any, message_id: int, text_kw: dict,
                            keyboard: Any, *, link_preview: bool = False):
    """Edit a message's inline keyboard via the raw `messages.editMessage`.

    Bypasses the broken high-level `keyboard=` serialisation. When `text_kw`
    carries no new body the existing text is left untouched (`message=None`).
    """
    input_peer, ref = _peer_input_and_ref(peer)
    has_text = any(k in text_kw for k in ("text", "html", "markdown"))
    if has_text:
        message, entities = _entities_for(text_kw)
    else:
        message, entities = None, None
    request = _tlfns.messages.edit_message(
        no_webpage=not link_preview,
        peer=input_peer,
        id=int(message_id),
        message=message,
        media=None,
        reply_markup=keyboard._raw,
        entities=entities,
        schedule_date=None,
    )
    result = await client(request)
    return client._build_message_map(result, ref)


# ---------------------------------------------------------------------------
# 3. Monkeypatch Message.reply / .respond / .edit to accept v1 kwargs
# ---------------------------------------------------------------------------
# We keep references to the pristine v2 methods and wrap them. The wrappers
# translate `parse_mode=`/`buttons=` and pass through everything else (`html=`,
# `markdown=`, `text=`, `keyboard=`, `link_preview=`, `reply_to=`).

_orig_msg_reply = _Message.reply
_orig_msg_respond = _Message.respond
_orig_msg_edit = _Message.edit
_orig_msg_delete = _Message.delete


def _wrap_text_method(orig: Callable, positional_text: bool = True) -> Callable:
    """Build a wrapper for a v2 text-producing Message method.

    The wrapper accepts the historical v1 signature
    `(text=None, *, parse_mode=None, buttons=None, link_preview=None, **kw)`
    and forwards to the v2 method with `html=/markdown=/text=` + `keyboard=`.
    """

    async def wrapper(self, text: Any = None, *, parse_mode: Any = None,
                      buttons: Any = None, link_preview: Any = None,
                      file: Any = None, **kw: Any):
        # If the caller already used v2 keywords (html/markdown/text), respect them.
        already_v2 = any(k in kw for k in ("html", "markdown", "text"))
        call_kw: dict = {}
        if not already_v2:
            call_kw.update(_resolve_parse_mode(text, parse_mode))
        else:
            # Caller passed v2 text keyword directly; keep as-is.
            for k in ("html", "markdown", "text"):
                if k in kw:
                    call_kw[k] = kw.pop(k)
        kb = _normalize_keyboard(buttons)
        if kb is not None:
            call_kw["keyboard"] = kb
        # v2: link_preview is a bool (default False). v1 sometimes passed
        # link_preview=False/True; map None -> omit (v2 default False).
        if link_preview is not None:
            call_kw["link_preview"] = bool(link_preview)
        # Files on reply/respond are not supported by the v2 Message methods;
        # route through the client.send_file with the target chat if used.
        if file is not None:
            return await self._client.send_file(  # type: ignore[attr-defined]
                self.chat.ref, file=file,
                caption=call_kw.get("text"),
                caption_html=call_kw.get("html"),
                caption_markdown=call_kw.get("markdown"),
                keyboard=call_kw.get("keyboard"),
                reply_to=self.id,
            )
        # A keyboard cannot be delivered through the high-level path on this
        # Telethon build (see the 2b workaround above): rebuild the send by
        # hand so the inline buttons actually reach the server. `reply` is a
        # reply to this message; `respond` sends a fresh message to the chat.
        if kb is not None:
            text_kw = {k: v for k, v in call_kw.items()
                       if k in ("text", "html", "markdown")}
            is_reply = getattr(orig, "__name__", "") == "reply"
            return await _raw_send_message(
                self._client, _coerce_peer(self), text_kw, kb,
                reply_to=self.id if is_reply else None,
                link_preview=bool(call_kw.get("link_preview", False)),
            )
        # Drop any leftover unknown kwargs quietly (v1 tolerated extras).
        return await orig(self, **call_kw)

    wrapper.__name__ = getattr(orig, "__name__", "wrapper")
    wrapper.__doc__ = (
        "v2-compat wrapper: accepts v1 `parse_mode=`/`buttons=`/`link_preview=` "
        "and forwards to the native v2 method using `html=`/`markdown=`/`text=` "
        "and `keyboard=`."
    )
    return wrapper


# Install the wrappers. These run on every reply/respond/edit in the app.
_Message.reply = _wrap_text_method(_orig_msg_reply)      # type: ignore[assignment]
_Message.respond = _wrap_text_method(_orig_msg_respond)  # type: ignore[assignment]


async def _msg_edit_wrapper(self, text: Any = None, *, parse_mode: Any = None,
                            buttons: Any = None, link_preview: Any = None, **kw: Any):
    """v2-compat wrapper for `Message.edit`.

    v2: `Message.edit(text=None, markdown=None, html=None, link_preview=False,
    keyboard=None)`. We translate v1 `parse_mode=`/`buttons=` the same way as
    reply/respond.
    """
    already_v2 = any(k in kw for k in ("html", "markdown", "text"))
    call_kw: dict = {}
    if not already_v2:
        call_kw.update(_resolve_parse_mode(text, parse_mode))
    else:
        for k in ("html", "markdown", "text"):
            if k in kw:
                call_kw[k] = kw.pop(k)
    kb = _normalize_keyboard(buttons)
    if kb is not None:
        call_kw["keyboard"] = kb
    if link_preview is not None:
        call_kw["link_preview"] = bool(link_preview)
    # Editing in an inline keyboard requires the raw path on this Telethon
    # build (the high-level `keyboard=` drops the markup); see workaround 2b.
    if kb is not None:
        text_kw = {k: v for k, v in call_kw.items()
                   if k in ("text", "html", "markdown")}
        return await _raw_edit_message(
            self._client, _coerce_peer(self), self.id, text_kw, kb,
            link_preview=bool(call_kw.get("link_preview", False)),
        )
    return await _orig_msg_edit(self, **call_kw)


_Message.edit = _msg_edit_wrapper  # type: ignore[assignment]


def _msg_is_private(self) -> bool:
    """v2: v1 `event.is_private` on a message event.

    v1 exposed `is_private`/`is_group`/`is_channel` booleans. v2 events always
    have a `.chat` which is a concrete `User`/`Group`/`Channel` peer, so
    "private" is simply "the chat is a `User`".
    """
    chat = getattr(self, "chat", None)
    return isinstance(chat, _types.User)


def _msg_is_group(self) -> bool:
    """v2: `is_group` -> the chat is a `Group` (basic group / small supergroup)."""
    return isinstance(getattr(self, "chat", None), _types.Group)


def _msg_is_channel(self) -> bool:
    """v2: `is_channel` -> the chat is a broadcast `Channel`."""
    return isinstance(getattr(self, "chat", None), _types.Channel)


# NewMessage events ARE `Message` instances in v2, so these properties cover
# both `event.is_private` and `message.is_private`.
_Message.is_private = property(_msg_is_private)   # type: ignore[attr-defined]
_Message.is_group = property(_msg_is_group)       # type: ignore[attr-defined]
_Message.is_channel = property(_msg_is_channel)   # type: ignore[attr-defined]


def _msg_sender_id(self):
    """v2: v1 `message.sender_id` -> `message.sender.id`.

    v2 streamlined `sender`/`input_sender`/`sender_id` into a single `.sender`
    peer (which has at least an `.id`). This shim restores the flat
    `sender_id` int the app reads in ~231 places.

    IMPORTANT (the reason the bot ignored every private message): in v2,
    ``Message.sender`` is derived purely from the raw ``from_id`` field, and
    for an *incoming private message* Telegram leaves ``from_id`` unset — the
    sender is implied by ``peer_id`` (the other side of a 1:1 chat). So
    ``sender`` (and therefore the old ``sender_id``) came back ``None`` for
    every DM, which made the whole message router bail out with ``uid=None``
    and the bot never replied. We fall back to the raw ``from_id`` and then to
    the private-chat ``peer_id`` (a ``PeerUser`` whose ``user_id`` IS the
    sender) so DMs resolve to a real user id. Group/channel messages still
    carry ``from_id`` and keep working unchanged. Returns ``None`` only for a
    genuinely anonymous sender.
    (see migration guide: "Streamlined chat, input_chat and chat_id")
    """
    sender = getattr(self, "sender", None)
    if sender is not None:
        return sender.id

    raw = getattr(self, "_raw", None)
    # Prefer the explicit sender when present (groups/channels).
    from_id = getattr(raw, "from_id", None) if raw is not None else None
    uid = getattr(from_id, "user_id", None)
    if uid is not None:
        return uid

    # Incoming DM: the sender is the private-chat peer. Only trust a PeerUser
    # here — a PeerChat/PeerChannel peer_id would be the group, not a user.
    peer = getattr(raw, "peer_id", None) if raw is not None else None
    peer_uid = getattr(peer, "user_id", None)
    if peer_uid is not None:
        return peer_uid

    # Last resort: the resolved chat, which in a 1:1 chat is the user itself.
    chat = getattr(self, "chat", None)
    return chat.id if chat is not None else None


def _marked_chat_id(chat):
    """Return the v1-style *marked* chat id for a v2 chat peer.

    v2 flattened chat ids: ``Group.id`` / ``Channel.id`` now return the bare
    positive internal id (``self._raw.id``), whereas v1 exposed the *marked*
    form — negative for anything that is not a 1:1 user chat:

        * basic group        -> ``-id``
        * channel/supergroup  -> ``-100<id>`` (i.e. ``-(1_000_000_000_000 + id)``)
        * private (User)      ->  ``id`` (unchanged, positive)

    The entire codebase (and the admin-entered group-block ids, which are
    copied from Telegram in ``-100…`` form) relies on ``chat_id < 0`` to mean
    "this is a group/channel". Returning the bare positive id silently broke
    every one of those checks — group detection, group-block enforcement, the
    ``pending_message`` continuation flow and group-only collapse all stopped
    firing, so the bot appeared to ignore groups entirely. Reconstructing the
    marked id here restores v1 semantics fleet-wide with zero handler changes.
    """
    if chat is None:
        return None
    cid = chat.id
    if isinstance(chat, _types.Channel):
        return -(1_000_000_000_000 + cid)
    if isinstance(chat, _types.Group):
        return -cid
    # Private (User) chats keep their plain positive id.
    return cid


def _msg_chat_id(self):
    """v2: v1 `message.chat_id` -> a *marked* chat id (negative for groups).

    See :func:`_marked_chat_id` — v2's ``chat.id`` is the bare positive id, but
    the app expects the v1 marked convention where groups/channels are
    negative. We reconstruct that here so all ``chat_id < 0`` group checks and
    the ``-100…`` group-block lookups keep working exactly as before.
    """
    return _marked_chat_id(getattr(self, "chat", None))


_Message.sender_id = property(_msg_sender_id)  # type: ignore[attr-defined]
_Message.chat_id = property(_msg_chat_id)      # type: ignore[attr-defined]


def _msg_is_reply(self) -> bool:
    """v2: v1 `message.is_reply` -> `replied_message_id is not None`.

    v2 collapsed `is_reply` / `reply_to_msg_id` / `reply_to` into a single
    `replied_message_id` (int or None). (see migration guide: message props)
    """
    return getattr(self, "replied_message_id", None) is not None


def _msg_reply_to_msg_id(self):
    """v2: v1 `message.reply_to_msg_id` -> `replied_message_id`."""
    return getattr(self, "replied_message_id", None)


async def _msg_get_reply_message(self):
    """v2: v1 `message.get_reply_message()` -> `get_replied_message()`.

    Renamed to make it clear it returns the message THIS one replied to, not a
    reply to this message. (see migration guide: message methods)
    """
    return await self.get_replied_message()


def _msg_message_alias(self):
    """v2: v1 `message.message` (and `raw_text`) -> `message.text`.

    v1 had both `.message` and `.raw_text`; v2 unifies on `.text` (plus
    `.text_html` / `.text_markdown`). This alias covers the couple of remaining
    `.message` reads (e.g. building history text).
    """
    return self.text


class _BytesSink:
    """Minimal in-memory ``OutFileLike`` that accumulates written chunks.

    v2's ``Client.download`` writes into a file path or a file-like object with
    a ``write`` method (which may be sync or async). To emulate v1's
    ``download_media(bytes)`` -> ``bytes`` we hand it one of these sinks and
    return the joined buffer.
    """

    __slots__ = ("_chunks",)

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self._chunks.append(bytes(data))

    def getvalue(self) -> bytes:
        return b"".join(self._chunks)


async def _msg_download_media(self, file: Any = None, *, thumb: Any = None, **_ignored: Any):
    """v2: v1 `message.download_media(file)` -> `client.download(media, out)`.

    v2 removed ``Message.download_media`` in favour of
    ``Client.download(media, file)`` (where ``media`` is a v2 ``File`` such as
    ``message.file``/``message.photo``/``message.video`` and ``file`` is a path
    or file-like object, and the call returns ``None``).

    This shim restores the v1 call the app still uses:

      * ``download_media(bytes)`` — the app's only form — downloads into an
        in-memory buffer and returns the raw ``bytes`` (or ``None`` when the
        message carries no downloadable media).
      * ``download_media("/path")`` / a file-like object — downloads there and
        returns the given target (v1 returned the path).

    (see migration guide: "download_media moved to Client.download")
    """
    media = getattr(self, "file", None) or getattr(self, "photo", None) or getattr(self, "video", None)
    if media is None:
        return None
    client = getattr(self, "_client", None)
    if client is None:  # pragma: no cover - defensive; every real event has one
        raise RuntimeError("message has no attached client for download")

    # v1 `download_media(bytes)`: return the bytes in-memory.
    if file is bytes or file is None:
        sink = _BytesSink()
        await client.download(media, sink)
        return sink.getvalue()

    # A path or a caller-supplied file-like object: download there, return target.
    await client.download(media, file)
    return file


# v1 accessors re-added so the ~17 is_reply / get_reply_message / reply_to sites
# and the `.message` reads keep working unchanged.
_Message.is_reply = property(_msg_is_reply)                # type: ignore[attr-defined]
_Message.reply_to_msg_id = property(_msg_reply_to_msg_id)  # type: ignore[attr-defined]
_Message.get_reply_message = _msg_get_reply_message        # type: ignore[attr-defined]
if not hasattr(_Message, "download_media"):
    _Message.download_media = _msg_download_media          # type: ignore[attr-defined]
# NOTE: `.message` as a text alias is only added if v2 doesn't already define it
# (it does not, since v2 renamed it to `.text`).
if not hasattr(_Message, "message"):
    _Message.message = property(_msg_message_alias)        # type: ignore[attr-defined]


# v2: v1 code did `isinstance(event, events.CallbackQuery.Event)` to branch on
# "is this a button-callback event?". v2 has no nested `.Event`; the event type
# itself is `events.ButtonCallback`. We expose a compatibility namespace so the
# old `events.CallbackQuery.Event` isinstance checks resolve to the v2 class.
class _CallbackQueryCompat:
    """v1 `events.CallbackQuery` facade for `isinstance` checks only.

    `events.CallbackQuery.Event` -> `events.ButtonCallback`.
    """
    Event = _events.ButtonCallback


# Expose it on the re-exported `events` object so existing
# `events.CallbackQuery.Event` references keep type-checking correctly.
events.CallbackQuery = _CallbackQueryCompat  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4. Client.send_message / send_file / edit_message v1-kwarg wrappers
# ---------------------------------------------------------------------------
_orig_client_send_message = _V2Client.send_message
_orig_client_send_file = _V2Client.send_file
_orig_client_edit_message = _V2Client.edit_message


def _coerce_peer(chat: Any) -> Any:
    """Coerce a v1-style chat argument to something v2 accepts as a peer.

    v2: `send_message`/`send_file`/`edit_message` take a `Peer` or `PeerRef` as
    the first positional argument. v1 frequently passed a bare integer id, an
    `event`, a `Message`, or a marked (`-100...`) channel id. This helper:

      * passes through `Peer`/`PeerRef` unchanged;
      * unwraps a `Message`/event to its `.chat.ref`;
      * converts a bare int id into the right `*Ref` (user vs channel) using the
        legacy marked-id sign convention as a hint, stripping `-100`/`-`.
    """
    if chat is None:
        return chat
    if isinstance(chat, (_PeerRef, _UserRef, _ChannelRef, _GroupRef)):
        return chat
    # A v2 Peer (User/Group/Channel) exposes `.ref`.
    if hasattr(chat, "ref") and isinstance(getattr(chat, "ref"), _PeerRef):
        return chat.ref
    # A Message or event with a `.chat` -> use its ref.
    if hasattr(chat, "chat") and chat.chat is not None and hasattr(chat.chat, "ref"):
        return chat.chat.ref
    if isinstance(chat, int):
        return peer_ref_from_stored_id(chat)
    return chat


async def _client_send_message_wrapper(self, chat: Any, message: Any = None, *,
                                        parse_mode: Any = None, buttons: Any = None,
                                        link_preview: Any = None, reply_to: Any = None,
                                        **kw: Any):
    """v2-compat wrapper for `Client.send_message`.

    v2: `send_message(chat, text=None, *, markdown=, html=, link_preview=False,
    reply_to=None, keyboard=None)`. v1 called it as
    `send_message(chat, message, parse_mode="html", buttons=..., link_preview=...)`.
    """
    peer = _coerce_peer(chat)
    call_kw: dict = {}
    already_v2 = any(k in kw for k in ("html", "markdown", "text"))
    if not already_v2:
        call_kw.update(_resolve_parse_mode(message, parse_mode))
    else:
        for k in ("html", "markdown", "text"):
            if k in kw:
                call_kw[k] = kw.pop(k)
    kb = _normalize_keyboard(buttons)
    if kb is not None:
        call_kw["keyboard"] = kb
    if link_preview is not None:
        call_kw["link_preview"] = bool(link_preview)
    if reply_to is not None:
        call_kw["reply_to"] = reply_to
    # A keyboard cannot ride the high-level path on this Telethon build (the
    # `reply_markup` flag is dropped during serialisation); rebuild the request
    # by hand so the inline buttons reach the server. See workaround 2b.
    if kb is not None:
        text_kw = {k: v for k, v in call_kw.items()
                   if k in ("text", "html", "markdown")}
        return await _raw_send_message(
            self, peer, text_kw, kb,
            reply_to=reply_to,
            link_preview=bool(call_kw.get("link_preview", False)),
        )
    return await _orig_client_send_message(self, peer, **call_kw)


async def _client_edit_message_wrapper(self, chat: Any, message_id: Any = None, *,
                                       parse_mode: Any = None, buttons: Any = None,
                                       link_preview: Any = None, text: Any = None,
                                       **kw: Any):
    """v2-compat wrapper for `Client.edit_message`.

    v2: `edit_message(chat, message_id, *, text=, markdown=, html=,
    link_preview=False, keyboard=None)`.
    """
    peer = _coerce_peer(chat)
    call_kw: dict = {}
    already_v2 = any(k in kw for k in ("html", "markdown")) or text is not None
    if text is not None:
        # `text` here is the new message body; route through parse_mode.
        call_kw.update(_resolve_parse_mode(text, parse_mode))
    else:
        for k in ("html", "markdown"):
            if k in kw:
                call_kw[k] = kw.pop(k)
    kb = _normalize_keyboard(buttons)
    if kb is not None:
        call_kw["keyboard"] = kb
    if link_preview is not None:
        call_kw["link_preview"] = bool(link_preview)
    # Editing in an inline keyboard requires the raw path on this Telethon
    # build (the high-level `keyboard=` drops the markup); see workaround 2b.
    if kb is not None:
        text_kw = {k: v for k, v in call_kw.items()
                   if k in ("text", "html", "markdown")}
        return await _raw_edit_message(
            self, peer, message_id, text_kw, kb,
            link_preview=bool(call_kw.get("link_preview", False)),
        )
    return await _orig_client_edit_message(self, peer, message_id, **call_kw)


async def _client_send_file_wrapper(self, chat: Any, file: Any = None, *,
                                    caption: Any = None, parse_mode: Any = None,
                                    buttons: Any = None, **kw: Any):
    """v2-compat wrapper for `Client.send_file`.

    v2: `send_file(chat, file, *, caption=, caption_html=, caption_markdown=,
    keyboard=, reply_to=, ...)`. v1 called it with `caption=`/`parse_mode=`/
    `buttons=`.
    """
    peer = _coerce_peer(chat)
    call_kw: dict = dict(kw)
    call_kw.update(_resolve_caption_parse_mode(caption, parse_mode))
    kb = _normalize_keyboard(buttons)
    if kb is not None:
        call_kw["keyboard"] = kb
    return await _orig_client_send_file(self, peer, file=file, **call_kw)


_orig_client_delete_messages = _V2Client.delete_messages


async def _client_delete_messages_wrapper(self, chat: Any, message_ids: Any = None,
                                          **kw: Any):
    """v2-compat wrapper for `Client.delete_messages`.

    v2: `delete_messages(chat, message_ids: list[int], *, revoke=True)`. v1
    accepted a bare int chat id and either a single id or a list. We coerce the
    chat to a `PeerRef` and normalise a single id into a one-element list so
    older call sites (`delete_messages(chat, msg_id)`) keep working.
    """
    peer = _coerce_peer(chat)
    if isinstance(message_ids, int):
        message_ids = [message_ids]
    return await _orig_client_delete_messages(self, peer, message_ids, **kw)


_orig_client_get_messages = _V2Client.get_messages


async def _get_messages_by_ids(client: Any, peer: Any, ids: Any):
    """Fetch specific messages by id, returning a single Message or a list.

    Mirrors v1 ``get_messages(chat, ids=...)`` semantics: a scalar id yields a
    single ``Message`` (or ``None``); a list of ids yields a list.
    """
    scalar = isinstance(ids, int)
    id_list = [ids] if scalar else list(ids)
    results = []
    async for msg in client.get_messages_with_ids(peer, id_list):
        results.append(msg)
    if scalar:
        return results[0] if results else None
    return results


def _client_get_messages_wrapper(self, chat: Any, limit: Any = None, *,
                                 ids: Any = None, **kw: Any):
    """v2-compat wrapper for `Client.get_messages`.

    v2 split the v1 overloaded ``get_messages`` into two methods:

      * ``get_messages(chat, limit, *, offset_id=, offset_date=)`` — history
        pagination, returns an async-iterable ``AsyncList[Message]``.
      * ``get_messages_with_ids(chat, message_ids: list[int])`` — fetch specific
        messages by id.

    v1 code fetched a single message with ``get_messages(chat, ids=msg_id)`` and
    ``await``-ed it, while history/pagination call sites iterate the result with
    ``async for``. To keep BOTH shapes working this wrapper is deliberately a
    *plain* function:

      * with ``ids`` -> returns a coroutine (awaitable) resolving to a single
        ``Message``/``None`` (scalar id) or a list (id list);
      * without ``ids`` -> returns v2's native ``AsyncList`` unchanged, so the
        channel-watcher fetcher's ``async for msg in get_messages(peer, 1)``
        keeps working.
    (see migration guide: "get_messages split into get_messages / _with_ids")
    """
    peer = _coerce_peer(chat)
    if ids is not None:
        return _get_messages_by_ids(self, peer, ids)
    if limit is not None:
        return _orig_client_get_messages(self, peer, limit, **kw)
    return _orig_client_get_messages(self, peer, **kw)


_V2Client.send_message = _client_send_message_wrapper  # type: ignore[assignment]
_V2Client.edit_message = _client_edit_message_wrapper  # type: ignore[assignment]
_V2Client.send_file = _client_send_file_wrapper        # type: ignore[assignment]
_V2Client.delete_messages = _client_delete_messages_wrapper  # type: ignore[assignment]
_V2Client.get_messages = _client_get_messages_wrapper  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 4b. Callback event (ButtonCallback) convenience accessors
# ---------------------------------------------------------------------------
# v2: `events.ButtonCallback` exposes only `data`, `client`, `answer` and
# `get_message`. The v1 `events.CallbackQuery.Event` also offered `.edit`,
# `.respond`, `.delete`, `.sender_id`, `.chat_id`, `.query`, all of which the
# handlers use. We re-add them here as documented shims that delegate to the
# raw `UpdateBotCallbackQuery` (`_raw`) and the v2 client.

_ButtonCallback = _events.ButtonCallback


def _bc_sender_id(self) -> int:
    """v2: v1 `event.sender_id` on a callback -> `_raw.user_id` (the presser)."""
    return self._raw.user_id


def _bc_chat_id(self) -> Optional[int]:
    """v2: v1 `event.chat_id` on a callback -> *marked* id from `_raw.peer`.

    Telethon's ``peer_id`` returns the bare positive id (``chat_id`` /
    ``channel_id``), but the app expects the v1 marked convention (negative for
    groups/channels — see :func:`_marked_chat_id`). We reconstruct it directly
    from the raw peer so callback ``chat_id < 0`` group checks match the
    message-side value and the group-block lookups stay consistent.
    """
    try:
        peer = self._raw.peer
        if isinstance(peer, _tltypes.PeerUser):
            return peer.user_id
        if isinstance(peer, _tltypes.PeerChat):
            return -peer.chat_id
        if isinstance(peer, _tltypes.PeerChannel):
            return -(1_000_000_000_000 + peer.channel_id)
    except Exception:
        pass
    return None


def _bc_message_id(self) -> int:
    """The id of the message that carried the clicked button."""
    return self._raw.msg_id


class _QueryView:
    """Minimal stand-in for v1 `event.query`.

    v1 code occasionally read `event.query.data` / `event.query.id`. v2 has no
    `query` object, so we expose a tiny view backed by the raw update.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def data(self) -> bytes:
        return self._raw.data

    @property
    def id(self) -> int:
        return self._raw.query_id

    @property
    def user_id(self) -> int:
        return self._raw.user_id


def _bc_query(self) -> _QueryView:
    return _QueryView(self._raw)


async def _bc_edit(self, text: Any = None, *, parse_mode: Any = None,
                   buttons: Any = None, link_preview: Any = None, **kw: Any):
    """v2: v1 `event.edit(...)` on a callback edited the button's message.

    v2's `ButtonCallback` has no `.edit`. We fetch the underlying peer + message
    id from the raw update and call `client.edit_message`, translating the v1
    `parse_mode=`/`buttons=` kwargs (the client wrapper above handles that).
    """
    from telethon._impl.client.types.peer import peer_id
    pid = None
    try:
        pid = peer_id(self._raw.peer)
    except Exception:
        pid = None
    peer = peer_ref_from_stored_id(pid) if pid is not None else self._raw.peer
    return await self._client.edit_message(
        peer, self._raw.msg_id, text=text, parse_mode=parse_mode,
        buttons=buttons, link_preview=link_preview, **kw,
    )


async def _bc_respond(self, text: Any = None, *, parse_mode: Any = None,
                      buttons: Any = None, link_preview: Any = None, **kw: Any):
    """v2: v1 `event.respond(...)` on a callback sent a NEW message to the chat.

    We resolve the peer from the raw update and call `client.send_message`.
    """
    from telethon._impl.client.types.peer import peer_id
    pid = None
    try:
        pid = peer_id(self._raw.peer)
    except Exception:
        pid = None
    peer = peer_ref_from_stored_id(pid) if pid is not None else self._raw.peer
    return await self._client.send_message(
        peer, text, parse_mode=parse_mode, buttons=buttons,
        link_preview=link_preview, **kw,
    )


async def _bc_delete(self, *args: Any, **kw: Any):
    """v2: v1 `event.delete()` on a callback deleted the button's message."""
    msg = await self.get_message()
    if msg is not None:
        return await msg.delete()
    return None


def _bc_is_private(self) -> bool:
    """v2: v1 `event.is_private` on a callback -> True if the button lived in a
    private (user) chat. Derived from the raw peer type."""
    return isinstance(self._raw.peer, tl.types.PeerUser)


# Attach the shims as properties/methods on the v2 ButtonCallback class.
_ButtonCallback.is_private = property(_bc_is_private)  # type: ignore[attr-defined]
_ButtonCallback.sender_id = property(_bc_sender_id)   # type: ignore[attr-defined]
_ButtonCallback.chat_id = property(_bc_chat_id)       # type: ignore[attr-defined]
_ButtonCallback.message_id = property(_bc_message_id) # type: ignore[attr-defined]
_ButtonCallback.query = property(_bc_query)           # type: ignore[attr-defined]
_ButtonCallback.edit = _bc_edit                       # type: ignore[attr-defined]
_ButtonCallback.respond = _bc_respond                 # type: ignore[attr-defined]
_ButtonCallback.reply = _bc_respond                   # callbacks have no true "reply target"
_ButtonCallback.delete = _bc_delete                   # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4c. InlineQuery event shims (v2 event is minimal in 2.0.0a0)
# ---------------------------------------------------------------------------
# v2: `events.InlineQuery` currently only exposes `client` and the raw update
# (`_raw` = `UpdateBotInlineQuery` with query_id/user_id/query/offset/...). The
# convenient v1 helpers (`event.text`, `event.sender_id`, `event.builder.article`
# and `event.answer(results)`) are not implemented yet. We re-add them here using
# the private raw API `messages.set_inline_bot_results`, so the existing inline
# handler keeps working unchanged.
_InlineQuery = _events.InlineQuery


def _iq_sender_id(self) -> int:
    """v2: v1 `inline_event.sender_id` -> raw `user_id` (who typed the query)."""
    return self._raw.user_id


def _iq_text(self) -> str:
    """v2: v1 `inline_event.text` -> raw `query` (the typed inline text)."""
    return self._raw.query


class _InlineBuilder:
    """Re-creates v1's `event.builder.article(...)`.

    v1 built inline results with `event.builder.article(title=, text=,
    description=)`. v2 has no builder yet, so we assemble the raw
    `InputBotInlineResult` with an `InputBotInlineMessageText` payload directly.
    """

    _counter = 0

    def article(self, title: str = "", text: str = "", description: Optional[str] = None,
                **_ignored: Any) -> Any:
        _InlineBuilder._counter += 1
        # A stable-ish unique id per result is required by Telegram.
        rid = f"r{_InlineBuilder._counter}"
        return tl.types.InputBotInlineResult(
            id=rid,
            type="article",
            title=title,
            description=description,
            url=None,
            thumb=None,
            content=None,
            send_message=tl.types.InputBotInlineMessageText(
                no_webpage=True,
                message=text,
                entities=None,
                reply_markup=None,
            ),
        )


def _iq_builder(self) -> _InlineBuilder:
    return _InlineBuilder()


async def _iq_answer(self, results: Sequence[Any], *, cache_time: int = 0,
                     gallery: bool = False, private: bool = False,
                     next_offset: Optional[str] = None, **_ignored: Any) -> Any:
    """v2: v1 `inline_event.answer(results)` -> raw
    `messages.set_inline_bot_results(query_id=..., results=[...])`.

    Reproduces v1's default behavior (personal, non-gallery results with no
    caching) while exposing the common knobs.
    """
    return await self._client(
        tl.functions.messages.set_inline_bot_results(
            gallery=gallery,
            private=private,
            query_id=self._raw.query_id,
            results=list(results),
            cache_time=cache_time,
            next_offset=next_offset,
            switch_pm=None,
            switch_webview=None,
        )
    )


_InlineQuery.sender_id = property(_iq_sender_id)  # type: ignore[attr-defined]
_InlineQuery.text = property(_iq_text)            # type: ignore[attr-defined]
_InlineQuery.builder = property(_iq_builder)      # type: ignore[attr-defined]
_InlineQuery.answer = _iq_answer                  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 6. Peer helpers (v2 removed the entity cache & marked IDs)
# ---------------------------------------------------------------------------
def strip_channel_mark(cid: int) -> int:
    """Convert a legacy marked channel id into the bare id v2 expects.

    v2: v1 used `-100<id>` for channels/supergroups and `-<id>` for basic
    groups. v2 has no marked ids; `ChannelRef`/`GroupRef` take the bare positive
    id. This helper strips the `-100` prefix (channels/supergroups) or a leading
    `-` (basic groups) if present, and returns the absolute id otherwise.
    """
    if cid is None:
        return cid
    s = str(cid)
    if s.startswith("-100"):
        return int(s[4:])
    if s.startswith("-"):
        return int(s[1:])
    return int(cid)


def photo_dedup_key(file_obj: Any) -> Any:
    """Return a stable identity key for a v2 photo `File`.

    v2: v1 `Message.photo` returned a `Photo` object exposing a numeric `.id`,
    which the Trade Journal used to de-duplicate photos within an album
    (`photo.id not in [p.id for p in photos]`). v2's `Message.photo` returns a
    high-level `File` object that has *no* `.id` attribute. Its underlying
    `_input_media` (an `InputMediaPhoto` wrapping the photo id + access hash) is
    unique per photo, so its string form is a safe de-dup key. Falls back to
    Python object identity if the internal field is ever unavailable.
    """
    im = getattr(file_obj, "_input_media", None)
    if im is not None:
        return repr(im)
    return id(file_obj)


def user_ref(uid: int) -> _UserRef:
    """Wrap a bare stored user id into a v2 `UserRef`.

    v2: methods no longer resolve a bare id from a cache; you must pass a
    `PeerRef`. Stored user ids in the DB are already bare positive ints.
    """
    return _UserRef(int(uid))


def channel_ref_from_stored_id(cid: int) -> _ChannelRef:
    """Wrap a stored (possibly `-100`-marked) channel id into a v2 `ChannelRef`."""
    return _ChannelRef(strip_channel_mark(cid))


def peer_ref_from_stored_id(pid: int) -> _PeerRef:
    """Best-effort conversion of a stored id (marked or bare) into a `PeerRef`.

    v2: We use the legacy sign convention as the only available hint about the
    peer *type*:
      * `-100<id>` or `-<id>`  -> channel/supergroup -> `ChannelRef`
      * positive id            -> user               -> `UserRef`
    This mirrors how v1's marked ids encoded the peer type. Bots overwhelmingly
    interact with users in private chats (positive ids) and with channels for
    membership locks / publishing (marked ids), so this classification is
    correct for every peer this bot touches. Where the type is genuinely known
    (e.g. a stored channel id), prefer `channel_ref_from_stored_id`/`user_ref`.
    """
    if pid is None:
        return pid  # type: ignore[return-value]
    s = str(pid)
    if s.startswith("-100") or s.startswith("-"):
        return _ChannelRef(strip_channel_mark(pid))
    return _UserRef(int(pid))


# ---------------------------------------------------------------------------
# 5b. `data_regex` event filter (v1 `CallbackQuery(pattern=...)` replacement)
# ---------------------------------------------------------------------------
def data_regex(pattern: Union[str, "re.Pattern[str]"]) -> Callable[[Any], bool]:
    """Return a v2 event filter matching callback data against a regex.

    v2: v1 `events.CallbackQuery(pattern=r"prefix:")` matched using `re.match`
    (anchored at the start) against the callback data decoded as UTF-8.
    v2's built-in `filters.Data(b"...")` only does an *exact* bytes match, which
    cannot express prefixes or the negative-lookahead collision cases used in
    `bot.py` (e.g. `^service_delete:(?!confirm)`).

    This filter reproduces v1 exactly: it decodes the event's callback `data`
    to UTF-8 and applies `re.match(pattern, decoded)`. It only accepts events
    that actually carry callback `data` (i.e. `ButtonCallback`), returning
    `False` for anything else, so it can be combined safely.
    """
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern

    def _filter(event: Any) -> bool:
        data = getattr(event, "data", None)
        if not isinstance(data, (bytes, bytearray)):
            return False
        try:
            decoded = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return False
        return compiled.match(decoded) is not None

    _filter.__name__ = f"data_regex({getattr(compiled, 'pattern', pattern)!r})"
    return _filter


def text_regex(pattern: Union[str, "re.Pattern[str]"]) -> Callable[[Any], bool]:
    """Return a v2 event filter matching a message's text like v1's `pattern=`.

    v2: v1 `events.NewMessage(pattern=r"/start|اکسی")` matched with
    **`re.match`** (anchored at the START of the text). v2's built-in
    `filters.Text(regexp)` uses **`re.search`** (matches anywhere) instead, so
    it is NOT a drop-in replacement — `filters.Text("/w")` would also match a
    message whose body merely contains "/w" in the middle.

    This filter reproduces v1 exactly by applying `re.match(pattern, text)` to
    the event's `.text`, so command routing keeps its original start-anchored
    semantics (including the non-ASCII `اکسی` trigger and the shortcut commands
    like `/w`, `/sw`). Returns `False` when the event has no text.
    """
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern

    def _filter(event: Any) -> bool:
        text = getattr(event, "text", None)
        if not isinstance(text, str):
            return False
        return compiled.match(text) is not None

    _filter.__name__ = f"text_regex({getattr(compiled, 'pattern', pattern)!r})"
    return _filter


# ---------------------------------------------------------------------------
# 7. Typing indicator context manager (v2 removed client.action())
# ---------------------------------------------------------------------------
class typing_action:
    """Async context manager reproducing v1's `client.action(peer, "typing")`.

    v2: `client.action(...)` was removed. Telegram's "typing..." status must be
    refreshed periodically (it auto-expires after ~5s server-side). This context
    manager sends `messages.set_typing` on entry and every few seconds until it
    exits, using the private raw API (there is no public v2 typing helper).

    Usage:
        async with typing_action(client, peer):
            ...  # long work; "typing..." shows the whole time
    """

    def __init__(self, client: Any, peer: Any, interval: float = 4.0) -> None:
        self._client = client
        self._peer = _coerce_peer(peer)
        self._interval = interval
        self._task: Optional[asyncio.Task] = None

    async def _send_once(self) -> None:
        # v2: raw request is private + snake_case; there is no public typing API.
        try:
            input_peer = self._peer._to_input_peer() if hasattr(self._peer, "_to_input_peer") else self._peer
            await self._client(
                tl.functions.messages.set_typing(
                    peer=input_peer,
                    top_msg_id=None,
                    action=tl.types.SendMessageTypingAction(),
                )
            )
        except Exception as exc:  # never let a typing ping crash the caller
            _logger.debug("typing_action set_typing failed: %s", exc)

    async def _loop(self) -> None:
        try:
            while True:
                await self._send_once()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def __aenter__(self) -> "typing_action":
        await self._send_once()
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

import re
import logging
from typing import Any, Optional, Tuple, List

# v2: `from telethon import TelegramClient` -> compat re-export (aliased to
#     v2's `Client`).
# v2: raw API is now private + snake_case; `from telethon.tl.functions.channels
#     import GetParticipantRequest` -> `tl.functions.channels.get_participant`,
#     and `from telethon.tl.types import ...` -> `tl.types.*`. The compat layer
#     re-exports the private `_tl` module as `tl` for exactly this.
# v2: `telethon.errors` became a factory that resolves any `*Error` name; the
#     compat layer re-exports the specific error classes this module catches.
from telethon_compat import (
    TelegramClient,
    tl,
    channel_ref_from_stored_id,
    strip_channel_mark,
    UserNotParticipantError,
    ChatAdminRequiredError,
)

logger = logging.getLogger(__name__)


async def extract_channel_from_forward(event: Any) -> Optional[Tuple[int, str]]:
    """Extract the source channel id/title from a forwarded message.

    v2: v1 exposed a friendly `event.forward` (a `custom.Forward` object with
    `.from_id`/`.post_from`). v2's high-level `Message` dropped that wrapper
    (`forward_info` is not implemented in this alpha), so we read the *raw*
    forward header off `event._raw.fwd_from` instead. The raw header's
    `from_id` is a `tl.types.PeerChannel` when the original sender was a
    channel, exactly as in v1's raw layer — so the field logic is unchanged,
    only the access path moved from `.forward` to `._raw.fwd_from`.
    """
    channel_id = None

    # v2: reach the raw MessageFwdHeader via the underlying `_raw` message.
    raw_msg = getattr(event, "_raw", None) or getattr(event, "message", None)
    fwd_from = getattr(raw_msg, "fwd_from", None) if raw_msg is not None else None

    if fwd_from is not None:
        # v2: `PeerChannel` now lives in the private `tl.types` module.
        from_id = getattr(fwd_from, "from_id", None)
        if from_id is not None:
            if isinstance(from_id, tl.types.PeerChannel):
                channel_id = from_id.channel_id
            elif hasattr(from_id, "channel_id"):
                channel_id = from_id.channel_id

    if channel_id is None:
        return None

    # v2: `get_entity` was removed (no entity cache). We can no longer cheaply
    # look up a channel *title* from a bare id without a network round-trip that
    # v2 intentionally does not expose here, so we fall back to the id string.
    # Behavior is preserved for the common case (v1 also returned the id string
    # whenever the entity lookup failed).
    return channel_id, str(channel_id)


def _photo_to_input_media(photo, caption=None, parse_mode=None):
    """Build a v2 raw input-media object from a photo reference.

    v2: raw types are private/snake-cased and their *shape* changed:
      * `InputMediaPhoto(file=, caption=, parse_mode=)` (v1) ->
        `tl.types.InputMediaPhoto(spoiler=, id=<InputPhoto>, ttl_seconds=)`.
        Captions/parse-mode are no longer part of the media object in v2 (they
        are supplied at send time via `caption_html=` etc.), so we drop the
        `caption`/`parse_mode` args here.
      * `InputPhoto(id=, access_hash=, file_reference=)` is unchanged in fields
        but now comes from `tl.types`.
    The `caption`/`parse_mode` parameters are kept in the signature for
    call-site compatibility but are intentionally ignored (see note above).
    """
    if hasattr(photo, 'id') and hasattr(photo, 'access_hash') and hasattr(photo, 'file_reference'):
        input_photo = tl.types.InputPhoto(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
        )
        return tl.types.InputMediaPhoto(
            spoiler=False, id=input_photo, ttl_seconds=None
        )
    if isinstance(photo, str):
        # v2: external photo by URL; caption handled at send time.
        return tl.types.InputMediaPhotoExternal(
            spoiler=False, url=photo, ttl_seconds=None
        )
    # For any other reference (e.g. a file id / path), let the high-level
    # `send_file` handle it directly by returning it unchanged.
    return photo


async def resolve_channel_input(client: TelegramClient, text: str) -> Optional[Tuple[int, str]]:
    text = text.strip()

    # v2: for a numeric id we no longer probe `get_entity` with candidate marked
    # ids. v2 has no `-100` marked ids and no entity cache; the bare id + a
    # `ChannelRef` is enough for every subsequent send/edit operation. We keep
    # the legacy `-100...` handling for backward-compatible input by stripping
    # the mark, but no longer perform a network lookup just to fetch a title.
    if text.lstrip('-').isdigit():
        num = int(text)
        # Store the bare (unmarked) id so future `ChannelRef` construction works.
        cid = strip_channel_mark(num) if str(num).startswith('-') else num
        return cid, str(cid)

    match = re.match(r'(?:https?://)?(?:t\.me|telegram\.me)/(\+?\w+)', text)
    if match:
        username = match.group(1)
        try:
            # v2: `get_entity(username)` -> `resolve_username(username)`, which
            # returns a `Peer` (Channel/Group/User) exposing `.id`/`.title`.
            channel = await client.resolve_username(username)
            title = getattr(channel, 'title', None) or username
            return channel.id, title
        except Exception:
            return None

    if text.startswith('@') and len(text) > 1:
        username = text[1:]
        try:
            # v2: `get_entity('@name')` -> `resolve_username('name')`.
            channel = await client.resolve_username(username)
            title = getattr(channel, 'title', None) or username
            return channel.id, title
        except Exception:
            return None

    return None


async def verify_bot_admin(client: TelegramClient, channel_id: int) -> Tuple[bool, str]:
    try:
        me = await client.get_me()
        # v2: `client(GetParticipantRequest(channel=id, participant=id))` ->
        #   `client(tl.functions.channels.get_participant(channel=<InputChannel>,
        #    participant=<InputPeer>))`. The raw function now lives under the
        #   private `tl.functions` tree and requires proper input-peer objects
        #   built from v2 `ChannelRef`/`UserRef` (there is no id-cache lookup).
        chan_ref = channel_ref_from_stored_id(channel_id)
        participant = await client(tl.functions.channels.get_participant(
            channel=chan_ref._to_input_channel(),
            participant=me.ref._to_input_peer(),
        ))
        p = participant.participant

        # v2: participant type classes moved to the private `tl.types` module
        #     (snake_case module, same class names).
        if isinstance(p, (tl.types.ChannelParticipantCreator,
                          tl.types.ChatParticipantCreator)):
            return True, "OK"

        if isinstance(p, (tl.types.ChannelParticipantAdmin,
                          tl.types.ChatParticipantAdmin)):
            rights = getattr(p, 'admin_rights', None)
            if rights and not rights.post_messages:
                return False, "ربات دسترسی ارسال پیام ندارد. لطفاً دسترسی ادمین را بروزرسانی کنید."
            return True, "OK"

        return False, "ربات ادمین کانال نیست."

    except UserNotParticipantError:
        return False, "ربات عضو کانال نیست. لطفاً ربات را به کانال اضافه کنید."
    except ChatAdminRequiredError:
        return False, "ربات به دسترسی ادمین نیاز دارد."
    except Exception as e:
        logger.error(f"Error verifying bot admin for channel {channel_id}: {type(e).__name__}: {e}")
        # v2: no `get_entity` fallback for a title; report the bare id instead.
        return False, f"خطا در بررسی دسترسی‌ها: {type(e).__name__}: {str(e)}"


async def check_channel_validity(client: TelegramClient, channel_id: int) -> Tuple[bool, str]:
    try:
        # v2: `get_entity(channel_id)` removed. Validity is proven by the admin
        # check below (which performs a real `get_participant` round-trip); we
        # no longer need a separate entity lookup just to fetch a title.
        title = str(channel_id)
        is_admin, msg = await verify_bot_admin(client, channel_id)
        if not is_admin:
            return False, f"❌ کانال <b>{title}</b>:\n{msg}"
        return True, f"✅ کانال <b>{title}</b> فعال است."
    except Exception as e:
        return False, f"❌ کانال یافت نشد یا غیرفعال است:\n{str(e)}"


async def post_text_to_channel(client: TelegramClient, channel_id: int,
                               text: str, parse_mode: str = "html") -> Optional[int]:
    try:
        # v2: the compat `send_message` wrapper coerces the int `channel_id` to a
        #     `ChannelRef` and maps `parse_mode="html"` -> `html=`.
        msg = await client.send_message(channel_id, text, parse_mode=parse_mode)
        return msg.id
    except Exception as e:
        logger.error(f"Error posting to channel: {e}")
        return None


async def edit_channel_message(client: TelegramClient, channel_id: int,
                                message_id: int, text: str,
                                parse_mode: str = "html") -> bool:
    try:
        # v2: `edit_message(chat, message_id, text, parse_mode=)` ->
        #     `edit_message(peer, message_id, *, text=/html=/markdown=)`. v2's
        #     `text` is keyword-only, so we pass it as `text=` and let the compat
        #     wrapper translate `parse_mode="html"` into `html=`.
        await client.edit_message(channel_id, message_id, text=text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.error(f"Error editing channel message {message_id}: {e}")
        return False


async def delete_channel_message(client: TelegramClient, channel_id: int,
                                  message_id: int) -> bool:
    try:
        # v2: `delete_messages(chat, message_id)` -> `delete_messages(peer,
        #     message_ids=[...])`. The second argument must now be a *list* of
        #     ids, and the compat wrapper coerces the int `channel_id` to a
        #     `ChannelRef`.
        await client.delete_messages(channel_id, [message_id])
        return True
    except Exception as e:
        logger.error(f"Error deleting channel message {message_id}: {e}")
        return False


async def _send_photo_album(client: TelegramClient, peer: Any,
                            photos: list, *, caption_html: Optional[str] = None,
                            reply_to: Optional[int] = None) -> Optional[int]:
    """Send one or more photos as a single message/album, returning the first id.

    v2: v1 sent multi-photo albums by passing a `list` of raw `InputMedia*`
    objects to `send_file(file=[...])`. v2's `send_file` takes exactly ONE
    file; albums are built with `client.prepare_album()` ->
    `album.add_photo(file, caption_html=)` -> `album.send(peer, reply_to=)`.
    Both a single `send_file` and `AlbumBuilder.add_photo` accept the v2 `File`
    objects that `event.photo` yields, so no raw `InputMedia` conversion is
    needed anymore.
    """
    if not photos:
        return None

    # Single photo: a plain send_file with an optional HTML caption.
    if len(photos) == 1:
        # v2: caption/parse-mode are send-time kwargs; the compat send_file
        # wrapper maps `parse_mode="html"` -> `caption_html=` for us.
        kwargs: dict = {}
        if caption_html is not None:
            kwargs["caption"] = caption_html
            kwargs["parse_mode"] = "html"
        if reply_to is not None:
            kwargs["reply_to"] = reply_to
        msg = await client.send_file(peer, photos[0], **kwargs)
        return msg.id if msg else None

    # Multiple photos: build a native v2 album.
    from telethon_compat import _coerce_peer  # local import to avoid cycle
    album = client.prepare_album()
    for i, photo in enumerate(photos):
        # Only the first item carries the caption (matches v1 album behavior).
        cap = caption_html if i == 0 else None
        album.add_photo(photo, caption_html=cap)
    msgs = await album.send(_coerce_peer(peer), reply_to=reply_to)
    if isinstance(msgs, list) and msgs:
        return msgs[0].id
    if msgs:
        return getattr(msgs, "id", None)
    return None


async def post_media_group_to_channel(client: TelegramClient, channel_id: int,
                                      photo_file_ids: list, caption: str,
                                      parse_mode: str = "html") -> Optional[int]:
    try:
        # v2: raw InputMedia album -> native `prepare_album()`; see
        #     `_send_photo_album`. The caption travels with the first photo.
        return await _send_photo_album(
            client, channel_id, photo_file_ids, caption_html=caption
        )
    except Exception as e:
        logger.error(f"Error posting media group: {e}")
        try:
            if photo_file_ids:
                msg = await client.send_file(
                    channel_id, photo_file_ids[0],
                    caption=caption, parse_mode=parse_mode,
                )
                return msg.id if msg else None
        except Exception:
            pass
        return None


async def forward_photos_then_reply_text(client: TelegramClient, channel_id: int,
                                          photo_file_ids: list, text: str,
                                          parse_mode: str = "html") -> Optional[int]:
    try:
        if not photo_file_ids:
            msg = await client.send_message(channel_id, text, parse_mode=parse_mode)
            return msg.id

        # v2: post the photo(s) as an album (no caption), then reply the text to
        #     the first message. `_send_photo_album` handles the single vs
        #     multi-photo split using the native v2 album API.
        msg_id = await _send_photo_album(client, channel_id, photo_file_ids)

        if msg_id:
            await client.send_message(channel_id, text, parse_mode=parse_mode,
                                      reply_to=msg_id)
        return msg_id
    except Exception as e:
        logger.error(f"Error forwarding photos and replying: {e}")
        return await post_text_to_channel(client, channel_id, text, parse_mode)

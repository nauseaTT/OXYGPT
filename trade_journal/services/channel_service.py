import re
import logging
from typing import Any, Optional, Tuple, List

from telethon import TelegramClient
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import (
    ChatParticipantAdmin, ChatParticipantCreator,
    ChannelParticipantAdmin, ChannelParticipantCreator,
    PeerChannel
)
from telethon.errors import UserNotParticipantError, ChatAdminRequiredError

logger = logging.getLogger(__name__)


async def extract_channel_from_forward(event: Any) -> Optional[Tuple[int, str]]:
    if not event.forward:
        return None

    fwd = event.forward
    channel_id = None

    if fwd.from_id is not None:
        if isinstance(fwd.from_id, PeerChannel):
            channel_id = fwd.from_id.channel_id
        elif hasattr(fwd.from_id, 'channel_id'):
            channel_id = fwd.from_id.channel_id

    if channel_id is None and hasattr(fwd, 'post_from') and fwd.post_from is not None:
        if isinstance(fwd.post_from, PeerChannel):
            channel_id = fwd.post_from.channel_id
        elif hasattr(fwd.post_from, 'channel_id'):
            channel_id = fwd.post_from.channel_id

    if channel_id is None:
        try:
            raw_msg = event.message
            if hasattr(raw_msg, 'fwd_from') and raw_msg.fwd_from:
                fwd_from = raw_msg.fwd_from
                if hasattr(fwd_from, 'from_id') and fwd_from.from_id:
                    if isinstance(fwd_from.from_id, PeerChannel):
                        channel_id = fwd_from.from_id.channel_id
                    elif hasattr(fwd_from.from_id, 'channel_id'):
                        channel_id = fwd_from.from_id.channel_id
        except Exception:
            pass

    if channel_id is None:
        return None

    try:
        channel = await event.client.get_entity(channel_id)
        title = getattr(channel, 'title', None) or str(channel_id)
        return channel_id, title
    except Exception as e:
        logger.error(f"Error getting channel entity {channel_id}: {e}")
        return channel_id, str(channel_id)


def _photo_to_input_media(photo, caption=None, parse_mode=None):
    from telethon.tl.types import InputMediaPhoto, InputPhoto as InputPhotoType
    if hasattr(photo, 'id') and hasattr(photo, 'access_hash') and hasattr(photo, 'file_reference'):
        input_photo = InputPhotoType(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference
        )
        return InputMediaPhoto(file=input_photo, caption=caption, parse_mode=parse_mode)
    if isinstance(photo, str):
        from telethon.tl.types import InputMediaPhotoExternal
        return InputMediaPhotoExternal(url=photo, caption=caption, parse_mode=parse_mode)
    if isinstance(photo, int):
        from telethon.tl.types import InputMediaPhoto
        return InputMediaPhoto(file=photo, caption=caption, parse_mode=parse_mode)
    return photo


async def resolve_channel_input(client: TelegramClient, text: str) -> Optional[Tuple[int, str]]:
    text = text.strip()

    if text.lstrip('-').isdigit():
        num = int(text)
        candidates = []
        if num > 0 and num < 2000000000:
            candidates.append(-10000000000 - num)
        candidates.append(num)
        for cid in candidates:
            try:
                channel = await client.get_entity(cid)
                title = getattr(channel, 'title', None) or str(cid)
                return cid, title
            except Exception:
                continue
        return None

    match = re.match(r'(?:https?://)?(?:t\.me|telegram\.me)/(\+?\w+)', text)
    if match:
        username = match.group(1)
        try:
            channel = await client.get_entity(username)
            title = getattr(channel, 'title', None) or username
            return channel.id, title
        except Exception:
            return None

    if text.startswith('@') and len(text) > 1:
        username = text[1:]
        try:
            channel = await client.get_entity(username)
            title = getattr(channel, 'title', None) or username
            return channel.id, title
        except Exception:
            return None

    return None


async def verify_bot_admin(client: TelegramClient, channel_id: int) -> Tuple[bool, str]:
    try:
        me = await client.get_me()
        participant = await client(GetParticipantRequest(
            channel=channel_id,
            participant=me.id
        ))
        p = participant.participant

        if isinstance(p, (ChannelParticipantCreator, ChatParticipantCreator)):
            return True, "OK"

        if isinstance(p, (ChannelParticipantAdmin, ChatParticipantAdmin)):
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
        try:
            channel = await client.get_entity(channel_id)
            title = getattr(channel, 'title', None) or str(channel_id)
            return False, f"خطا در بررسی دسترسی‌ها برای کانال <b>{title}</b>: {type(e).__name__}"
        except Exception:
            return False, f"خطا در بررسی دسترسی‌ها: {type(e).__name__}: {str(e)}"


async def check_channel_validity(client: TelegramClient, channel_id: int) -> Tuple[bool, str]:
    try:
        channel = await client.get_entity(channel_id)
        title = getattr(channel, 'title', None) or str(channel_id)
        is_admin, msg = await verify_bot_admin(client, channel_id)
        if not is_admin:
            return False, f"❌ کانال <b>{title}</b>:\n{msg}"
        return True, f"✅ کانال <b>{title}</b> فعال است."
    except Exception as e:
        return False, f"❌ کانال یافت نشد یا غیرفعال است:\n{str(e)}"


async def post_text_to_channel(client: TelegramClient, channel_id: int,
                               text: str, parse_mode: str = "html") -> Optional[int]:
    try:
        msg = await client.send_message(channel_id, text, parse_mode=parse_mode)
        return msg.id
    except Exception as e:
        logger.error(f"Error posting to channel: {e}")
        return None


async def edit_channel_message(client: TelegramClient, channel_id: int,
                                message_id: int, text: str,
                                parse_mode: str = "html") -> bool:
    try:
        await client.edit_message(channel_id, message_id, text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.error(f"Error editing channel message {message_id}: {e}")
        return False


async def delete_channel_message(client: TelegramClient, channel_id: int,
                                  message_id: int) -> bool:
    try:
        await client.delete_messages(channel_id, message_id)
        return True
    except Exception as e:
        logger.error(f"Error deleting channel message {message_id}: {e}")
        return False


async def post_media_group_to_channel(client: TelegramClient, channel_id: int,
                                      photo_file_ids: list, caption: str,
                                      parse_mode: str = "html") -> Optional[int]:
    try:
        if not photo_file_ids:
            return None

        if len(photo_file_ids) == 1:
            msg = await client.send_file(channel_id, photo_file_ids[0], caption=caption, parse_mode=parse_mode)
            return msg.id if msg else None

        media = []
        for i, fid in enumerate(photo_file_ids):
            cap = caption if i == 0 else None
            pm = parse_mode if i == 0 else None
            media.append(_photo_to_input_media(fid, cap, pm))

        if not media:
            return None

        msgs = await client.send_file(channel_id, file=media)
        if isinstance(msgs, list) and msgs:
            return msgs[0].id
        elif msgs:
            return msgs.id
        return None
    except Exception as e:
        logger.error(f"Error posting media group: {e}")
        try:
            msg = await client.send_file(channel_id, photo_file_ids[0], caption=caption, parse_mode=parse_mode)
            return msg.id if msg else None
        except Exception:
            return None


async def forward_photos_then_reply_text(client: TelegramClient, channel_id: int,
                                          photo_file_ids: list, text: str,
                                          parse_mode: str = "html") -> Optional[int]:
    try:
        if not photo_file_ids:
            msg = await client.send_message(channel_id, text, parse_mode=parse_mode)
            return msg.id

        if len(photo_file_ids) == 1:
            msg = await client.send_file(channel_id, photo_file_ids[0])
            msg_id = msg.id if msg else None
        else:
            media = []
            for fid in photo_file_ids:
                media.append(_photo_to_input_media(fid))
            msgs = await client.send_file(channel_id, file=media)
            if isinstance(msgs, list) and msgs:
                msg_id = msgs[0].id
            elif msgs:
                msg_id = msgs.id
            else:
                msg_id = None

        if msg_id:
            await client.send_message(channel_id, text, parse_mode=parse_mode,
                                      reply_to=msg_id)
        return msg_id
    except Exception as e:
        logger.error(f"Error forwarding photos and replying: {e}")
        return await post_text_to_channel(client, channel_id, text, parse_mode)

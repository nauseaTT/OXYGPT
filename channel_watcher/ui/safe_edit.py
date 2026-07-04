"""Crash-proof wrapper around Telethon's ``CallbackQuery.Event.edit``.

Every callback handler in this module edits the triggering message to
show updated content. Two Telegram-side error conditions are routine
(not bugs) and must never bubble up as an unhandled exception:

- ``MessageIdInvalidError``: the message was deleted (e.g. the user
  cleared their chat) — fall back to sending a fresh message instead.
- ``MessageNotModifiedError``: the new text+buttons are byte-for-byte
  identical to what's already displayed (e.g. the user double-taps a
  button, or a "check now" cycle finds nothing new and re-renders the
  exact same settings panel) — this is a no-op from the user's
  perspective, so it's simply swallowed.
"""

import logging
from typing import Any, Optional

from telethon.errors import MessageIdInvalidError, MessageNotModifiedError

logger = logging.getLogger(__name__)


async def safe_edit(
    event: Any,
    text: str,
    buttons: Optional[Any] = None,
    parse_mode: Optional[str] = None,
    fallback_reply: bool = True,
) -> None:
    """Edit *event*'s message, degrading gracefully on routine failures.

    - If the message no longer exists, replies with a new one instead
      (unless ``fallback_reply=False``, e.g. for silent refresh-in-place
      actions where a stray new message would be noise).
    - If the content is unchanged, does nothing — this is expected, not
      an error, and must not crash the update dispatcher.
    """
    try:
        await event.edit(text, buttons=buttons, parse_mode=parse_mode)
    except MessageNotModifiedError:
        pass
    except MessageIdInvalidError:
        if fallback_reply:
            await event.reply(text, buttons=buttons, parse_mode=parse_mode)

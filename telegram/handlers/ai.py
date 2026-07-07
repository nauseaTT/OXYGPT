"""AI-related Telegram event handlers.

This module contains the core message processing pipeline:

- `_send_response`: Routes AI responses to the correct output format
  (HTML booklet → image → chart → plain text) with priority ordering.
- `pending_message_handler`: The central message router. Handles all
  incoming user messages: pending state actions (window creation, rename,
  lock/block data), AI question processing, and group message filtering.
  - `_process_reply_ask`: Stateless reply-to-ask handler. Processes a formatted
  message (user text + reply chain) without saving to conversation history.
- `retry_failed_handler`: Retries a previously failed AI request.
- `inline_handler`: Handles direct mentor-mode messages via inline buttons.
- `again_talk_mentor` / `again_talk_quickask`: "Send message" button
  callbacks that prompt the user for input in the correct mode.
- `clear_processing`: Cancels an in-flight AI request if still pending.
- `expand_cb` / `auto_collapse_toggle_cb`: Group message expand/collapse UX.

All handler functions receive `self: TelegramBot` as the first argument
(descriptor-bound instance) and `event` as the Telethon event object.
"""

import asyncio
import json
import os
import re
import logging
from typing import Any, Optional, TYPE_CHECKING
# v2: Button facade + v2 error class + raw API (`tl`) all via the compat layer.
# The raw typing request moved to the private `telethon._tl` namespace and is
# snake_case in v2 (see the two typing-indicator blocks below).
from telethon_compat import Button, MessageNotModifiedError, tl

from ..animator import StatusAnimator
from ..utils import clean_number_string, get_time_until_reset, safe_html_truncate
from ..constants import PROCESSING_PHASES, ToolEvent
from api_http import AI_Service, DEFAULT_SYSTEM_ROLE
from chart_generator import ENABLE_CHART_GENERATION
from skills import SKILLS, get_skill, get_skill_prompt
from .verify import should_verify, send_verify_quick_ask, send_verify_mentor

if TYPE_CHECKING:
    from ..bot import TelegramBot

tlogger = logging.getLogger("telegram")


def _sanitize_html(text: str) -> str:
    """Escape HTML special characters in user input to prevent XSS."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Regex compiled once at module level to detect RTL script characters
# (Persian, Arabic, Urdu, etc.).  Used by _ensure_rtl() to determine
# whether a Right-to-Left Mark needs to be prepended.
_HAS_RTL_SCRIPT = re.compile(
    r'[\u0600-\u06FF'       # Arabic block (covers Persian, Arabic, Urdu, Kurdish)
    r'\u0750-\u077F'        # Arabic Supplement
    r'\u08A0-\u08FF'        # Arabic Extended-A
    r'\uFB50-\uFDFF'        # Arabic Presentation Forms-A
    r'\uFE70-\uFEFF'        # Arabic Presentation Forms-B
    r']'
)


def _ensure_rtl(text: str) -> str:
    """Prepend Right-to-Left Mark (U+200F) to lines with RTL script.

    In Telegram's message renderer the Unicode Bidirectional Algorithm
    determines paragraph direction from the first *strong* directional
    character.  When a Persian sentence starts with an English word
    (e.g. ``"Hello سلام"``) the paragraph becomes LTR and the Persian
    text may render on the wrong side.  Prepending U+200F — an invisible,
    zero-width RTL strong character — forces RTL paragraph direction
    without adding any visible content.

    This implementation processes each line individually:
    - Lines inside code blocks (between ``` markers) are left untouched
    - Lines with RTL script get U+200F prepended (unless already present)
    - Pure LTR lines and empty lines are unchanged
    - Indentation and HTML tags are preserved

    This ensures that:
    1. Multi-line Persian text renders correctly even when AI forgets U+200F
    2. Code blocks remain LTR for proper readability
    3. Mixed content (Persian text + code) works correctly

    Args:
        text: The AI response string (may contain HTML tags, code blocks).

    Returns:
        The same string with U+200F prepended to RTL lines.
    """
    if not text:
        return text

    lines = text.split('\n')
    result_lines = []
    in_code_block = False

    for line in lines:
        # Detect code block boundaries (``` with optional language specifier)
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        # Leave code block content untouched
        if in_code_block:
            result_lines.append(line)
            continue

        # For regular lines, check if RTL mark is needed
        line_stripped = line.lstrip()
        
        # Skip empty lines or lines already starting with U+200F
        if not line_stripped or line_stripped.startswith("\u200F"):
            result_lines.append(line)
            continue

        # If line contains RTL script, prepend U+200F while preserving indentation
        if _HAS_RTL_SCRIPT.search(line_stripped):
            indent = line[:len(line) - len(line_stripped)]
            result_lines.append(indent + "\u200F" + line_stripped)
        else:
            result_lines.append(line)

    return '\n'.join(result_lines)


# ── Previous AI Message Button Management ──────────────────────────


async def _disable_previous_ai_buttons(self: "TelegramBot", uid: int) -> None:
    """Disable interactive buttons on all previous AI messages for a user.

    When a user enters waiting state (after clicking Tap For Talk), all
    previous AI response messages have their interactive buttons removed.
    Only the info button (mentor/skill + tokens) is kept as read-only
    context for the user.

    This prevents the user from clicking stale buttons on older messages
    while waiting for input on the current conversation.

    Args:
        self: TelegramBot instance.
        uid: User ID whose previous messages should be disabled.
    """
    prev_msgs = self.previous_ai_messages.get(uid, [])
    for msg, original_buttons in prev_msgs:
        try:
            # Keep only the info button row (last row if it exists)
            info_only = [original_buttons[-1]] if original_buttons else []
            await msg.edit(buttons=info_only)
        except Exception:
            pass


async def _restore_previous_ai_buttons(self: "TelegramBot", uid: int) -> None:
    """Restore original buttons on all previous AI messages for a user.

    Called when an AI request fails — the user returns to their previous
    conversation state, so previous messages should have their interactive
    buttons (Tap For Talk, window selector) restored.

    Args:
        self: TelegramBot instance.
        uid: User ID whose previous messages should be restored.
    """
    prev_msgs = self.previous_ai_messages.get(uid, [])
    for msg, original_buttons in prev_msgs:
        try:
            await msg.edit(buttons=original_buttons)
        except Exception:
            pass


# ── Response Helpers ──────────────────────────────────────────────────


async def _cleanup_file(file_path: str, uid: int, file_type: str) -> None:
    """Safely remove a temporary file."""
    try:
        os.remove(file_path)
        tlogger.info(f"Cleaned up {file_type} file for {uid}: {file_path}")
    except Exception as rm_err:
        tlogger.warning(f"Failed to remove {file_type} file for {uid}: {rm_err}")


async def _send_response(
    msg: Any,
    event: Any,
    ai_service: Any,
    answer: str,
    buttons: list,
    uid: int,
) -> None:
    """
    Send AI response handling HTML booklet, image, chart, and plain-text in priority order.

    Order:
      0. HTML booklet          → send as document with caption, delete loading msg
      1. Generated image        → send with caption, delete loading msg
      2. Candlestick chart      → send chart, reply with answer text
      3. Plain text             → edit loading msg with answer text

    Each path handles its own file cleanup and prevents double-sending.

    Before dispatching, ``answer`` is passed through ``_ensure_rtl()`` which
    prepends a Unicode Right-to-Left Mark (U+200F) when the text contains
    Persian/Arabic script.  This prevents mixed LTR–RTL text from rendering
    with the wrong paragraph direction in Telegram.
    """
    # Helper: clean up all pending file paths except the one being sent.
    # This prevents leftover files from lower-priority outputs (e.g. image
    # or chart) from accumulating on disk when a higher-priority output
    # (HTML booklet) is sent first (Bug #6).
    async def _cleanup_other_paths(skip_path: Optional[str] = None) -> None:
        for attr_name, file_type in [("pending_image_path", "image"), ("pending_chart_path", "chart"), ("pending_html_path", "html")]:
            path = getattr(ai_service, attr_name, None)
            if path and path != skip_path and os.path.exists(path):
                await _cleanup_file(path, uid, file_type)
                setattr(ai_service, attr_name, None)

    # Normalise RTL direction: prepend U+200F when the answer contains
    # Persian/Arabic script so that mixed LTR–RTL text is always rendered
    # with the correct paragraph direction in Telegram.
    answer = _ensure_rtl(answer)

    # ── 0. HTML Booklet ──
    html_path = getattr(ai_service, "pending_html_path", None)
    if html_path and os.path.exists(html_path) and os.path.getsize(html_path) > 0:
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            caption = safe_html_truncate(answer)
            await event.reply(file=html_path, message=caption, buttons=buttons)
            tlogger.info(f"HTML booklet sent to {uid}: {html_path} ({os.path.getsize(html_path)} bytes)")
        except Exception as send_err:
            tlogger.error(f"Failed to send HTML booklet to {uid}: {send_err}", exc_info=True)
            try:
                await event.reply(answer, parse_mode="html", buttons=buttons)
            except Exception:
                pass
        finally:
            await _cleanup_file(html_path, uid, "html")
        await _cleanup_other_paths(skip_path=html_path)
        return

    # ── 1. Generated Image ──
    image_path = getattr(ai_service, "pending_image_path", None)
    if image_path:
        if os.path.exists(image_path) and os.path.getsize(image_path) > 1024:
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                caption = safe_html_truncate(answer)
                await event.reply(file=image_path, message=caption, buttons=buttons)
                tlogger.info(f"Image sent to {uid}: {image_path} ({os.path.getsize(image_path)} bytes)")
            except Exception as send_err:
                tlogger.error(f"Failed to send image to {uid}: {send_err}", exc_info=True)
                try:
                    await event.reply(answer, parse_mode="html", buttons=buttons)
                except Exception:
                    pass
            finally:
                await _cleanup_file(image_path, uid, "image")
        else:
            tlogger.warning(f"Invalid image file for {uid} (exists={os.path.exists(image_path)}, size={os.path.getsize(image_path) if os.path.exists(image_path) else 0})")
            await _cleanup_file(image_path, uid, "image")
            try:
                await msg.edit(answer, parse_mode="html", buttons=buttons)
            except Exception:
                try:
                    await event.reply(answer, parse_mode="html", buttons=buttons)
                except Exception:
                    pass
        await _cleanup_other_paths(skip_path=image_path)
        return

    # ── 2. Candlestick Chart (only when globally enabled) ──
    chart_path = getattr(ai_service, "pending_chart_path", None) if ENABLE_CHART_GENERATION else None
    if chart_path and os.path.exists(chart_path) and os.path.getsize(chart_path) > 0:
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            chart_msg = await event.reply(file=chart_path, message="📊 نمودار تحلیل درخواستی شما:")
            await chart_msg.reply(answer, parse_mode="html", buttons=buttons)
            tlogger.info(f"Chart sent to {uid}: {chart_path}")
        except Exception as send_err:
            tlogger.error(f"Failed to send chart to {uid}: {send_err}", exc_info=True)
            try:
                await event.reply(answer, parse_mode="html", buttons=buttons)
            except Exception:
                pass
        finally:
            await _cleanup_file(chart_path, uid, "chart")
        return

    # ── 3. Plain Text ──
    try:
        await msg.edit(answer, parse_mode="html", buttons=buttons)
    except Exception as edit_err:
        tlogger.error(f"Failed to edit message for {uid}: {edit_err}", exc_info=True)
        try:
            await event.reply(answer, parse_mode="html", buttons=buttons)
        except Exception:
            pass


# ── Handler Functions ─────────────────────────────────────────────────


async def _process_reply_ask(
    self: "TelegramBot",
    event: Any,
    formatted_text: str,
    user_text: str,
    reply_chain: list,
    image_data: Optional[bytes] = None,
) -> None:
    """Process a stateless reply-to-ask request.

    Handles the complete flow for the reply-to-ask feature:
    membership/limit validation, stateless ``AI_Service`` creation,
    AI generation with the reply-ask system prompt suffix, and
    response delivery via ``_send_response``.

    The key difference from the standard quick-ask flow:
    - No conversation window is touched (``window_id=None``).
    - History is wiped before the AI call and never persisted to DB.
    - The system role receives ``REPLY_ASK_SYSTEM_SUFFIX`` to adjust
      the model's behaviour for this stateless, third-person context.
    - Usage is still counted against the quick-ask pool (same
      ``check_user_limits`` and ``_update_usage`` paths).

    On failure, a plain error message is shown **without** a retry
    button — the user can simply re-type ``/ask``.  This avoids the
    complexity of adapting ``retry_failed_handler`` for the stateless
    path and keeps the retry surface simple.

    Args:
        self: TelegramBot instance.
        event: Incoming message or callback event.
        formatted_text: The fully formatted message text for the AI
            (user text + reply chain blocks).
        user_text: The user's original text after ``/ask`` (may be empty).
        reply_chain: The resolved reply chain from ``resolve_reply_chain()``.
        image_data: Optional image bytes from the replied message or
            from the ``/ask`` event itself.
    """
    uid = event.sender_id
    chat_id = getattr(event, 'chat_id', None)

    # ── Concurrency guard ──
    if uid in self.processing_users:
        return
    self.processing_users.add(uid)

    try:
        # ── Membership check ──
        not_joined = await self.check_user_joined(uid)
        if not_joined:
            await self.send_join_warning(event, not_joined)
            return

        # ── Block checks ──
        if uid not in self.admin_ids and self.check_user_blocked(uid):
            await event.reply("⛔️ شما توسط ادمین از دسترسی به ربات مسدود شده‌اید.")
            return

        if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
            return

        # ── Rate limit check ──
        is_limited, limit_msg = self.check_user_limits(uid)
        if is_limited:
            await self.send_limit_notification(event, limit_msg, is_callback=False)
            return

        # ── 80% soft warning ──
        is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
        if is_80 and alert_msg:
            await event.reply(alert_msg)

        # ── Build stateless AI_Service ──
        # We create the service with mode="quick_ask" so that provider/model
        # resolution is identical to a normal quick-ask request.  Immediately
        # after construction we override window_id and history to prevent any
        # interaction with the user's stored conversation windows.
        from api_http import get_service_manager, get_active_provider

        service_id = None
        if get_active_provider(self.db) == "openai":
            default_svc = self.db.get_setting("default_openai_service", "")
            if default_svc and get_service_manager(self.db).has_service(default_svc):
                service_id = default_svc

        ai_service = AI_Service(
            uid, db_manager=self.db, mode="quick_ask",
            chat_id=chat_id, service_id=service_id,
        )
        # Stateless override: no window, no history
        ai_service.window_id = None
        ai_service.history = []

        # ── Typing indicator ──
        # v2: the raw request `SetTypingRequest` (from telethon.tl.functions)
        # moved to the private, snake_case `tl.functions.messages.set_typing`,
        # and `SendMessageTypingAction` to `tl.types`. v2 also removed the entity
        # cache, so `peer=` must be an InputPeer built from the event's chat
        # reference rather than a bare id. (see migration guide: raw API is private)
        try:
            await self.bot(tl.functions.messages.set_typing(
                peer=event.chat.ref._to_input_peer(),
                top_msg_id=None,
                action=tl.types.SendMessageTypingAction(),
            ))
        except Exception:
            pass

        # ── Status message ──
        msg = await event.reply(
            "📎 بررسی پیام مورد نظر...",
            buttons=[Button.inline("لغو درخواست", b"clear_processing", style="danger")],
        )

        animator = None
        try:
            role = DEFAULT_SYSTEM_ROLE

            animator = StatusAnimator(msg, skill_key="default")
            await animator.start()

            async def on_status_change(status_text: str):
                if animator:
                    animator.update_step(status_text)

            async def on_tool_call(tool_event: ToolEvent):
                if animator:
                    animator.update_tool(tool_event)

            self.active_requests[uid] = {
                "generation_started": False,
                "msg": msg,
                "task": asyncio.current_task(),
                "animator": animator,
                "cancel_requested": False,
            }

            async def on_generation_start():
                if uid in self.active_requests:
                    self.active_requests[uid]["generation_started"] = True

            tlogger.info(
                f"[REPLY_ASK] uid={uid}, chat_id={chat_id}, "
                f"has_image={image_data is not None}, chain_length={len(reply_chain)}"
            )

            answer, tokens_used = await ai_service.handle_message(
                user_message=formatted_text,
                role=role,
                mode="reply_ask",
                on_status_change=on_status_change,
                on_generation_start=on_generation_start,
                on_tool_call=on_tool_call,
                image_data=image_data,
            )

            self.last_tokens[uid] = tokens_used

            if animator:
                await animator.stop()

            # ── Build response buttons ──
            buttons = [
                [
                    Button.inline(
                        f"💬 Reply Ask | 🪙 {tokens_used:,}",
                        b"place_holder",
                    )
                ],
            ]

            await _send_response(msg, event, ai_service, answer, buttons, uid)

        except asyncio.CancelledError:
            tlogger.info(f"Reply-ask request for user {uid} was cancelled.")
            await _restore_previous_ai_buttons(self, uid)
            raise
        except Exception as e:
            tlogger.error(f"Reply-ask failed for {uid}: {e}")
            await _restore_previous_ai_buttons(self, uid)
            if animator:
                try:
                    await animator.stop()
                    animator = None
                except Exception:
                    pass
            try:
                await msg.edit(
                    "پردازش درخواست با خطا مواجه شد. لطفا مجددا /ask را استفاده کنید.",
                )
            except Exception:
                await event.reply(
                    "پردازش درخواست با خطا مواجه شد. لطفا مجددا /ask را استفاده کنید.",
                )
        finally:
            if animator:
                try:
                    await animator.stop()
                except Exception:
                    pass
            self.active_requests.pop(uid, None)
    finally:
        self.processing_users.discard(uid)


async def pending_message_handler(self: "TelegramBot", event: Any) -> None:
    """Central message router for all incoming user messages.

    This is the catch-all handler registered last in the handler chain.
    Telethon fires ALL matching handlers, so this function runs alongside
    any dedicated command handler (e.g. start, ask_cmd, micheal_cmd).
    That is why we need the early command-detection below.

    Processing priority:
      1. EARLY RETURN for known commands — these have their own dedicated
         handlers registered earlier; we just clean up leaked pending state
         and return.
      2. Pending state actions (CREATE_WINDOW_TITLE, RENAME_WINDOW_TITLE,
         ADD_LOCK_DATA, BLOCK_USER, BLOCK_GROUP) — admin/config operations.
      3. SERVICE/OPENAI admin flows (SERVICE_CREATE_*, SERVICE_SET_*,
         SERVICE_ADD_*, OPENAI_SET_BASE_URL, OPENAI_ADD_KEY, etc.) —
         multi-step dialogs that read text input from the admin.
      4. Pending question — the user's AI query after being prompted.
         Checks limits, membership, creates AI_Service, calls
         generate_response, and sends the result via _send_response.
      5. Group messages — silently ignored if not a command or reply.

    PREVIOUS AI MESSAGE BUTTON POLICY
      - Private chat (PV): Previous AI message buttons are NEVER edited.
        They remain intact (Tap For Talk, window selector, info) so the
        user can continue interacting with them.
      - Group chat: Previous AI message is collapsed AFTER the new AI
        response is sent (not before). Only the first 3 lines are shown
        with an Expand button. Full text and info are stored in
        ``collapsed_messages`` for later expansion.

    PENDING STATE CLEANUP CONTRACT
    Every admin flow in this function MUST either:
      - Call delete_pending_state(uid) on successful completion, OR
      - Re-save the pending_state to continue the flow (e.g. on validation
        failure, so the user can retry).

    The early returns for "/" commands (below) also call delete_pending_state
    as a safety net: if the admin types /start while in the middle of
    creating a service, the pending_action is purged so the next text
    won't be misrouted into the abandoned flow.

    Concurrency: Uses processing_users set to prevent concurrent AI requests.
    uid is added right after the early-return gates and removed on every exit path.

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: Incoming Telethon NewMessage event.
    """
    uid = event.sender_id
    if not event.text and not event.photo:
        return

    # ── Early return for known commands ──────────────────────────────
    # These all have dedicated handlers registered earlier in bot.py.
    # We STILL receive the event (Telethon fires all matching handlers),
    # so we immediately return to avoid double-processing.
    #
    # IMPORTANT: We do NOT call delete_pending_state() here.  Command
    # handlers that need cleanup (start, cancel) already do so at the
    # top of their own body.  Handlers that set pending state (/ask,
    # /new with no args, /micheal, etc.) call save_pending_state()
    # which uses ON CONFLICT DO UPDATE — so any leaked state from an
    # abandoned flow is naturally overwritten on the next command.
    # Deleting state here was the root cause of Bug [#1]: after /ask
    # set pending_question=True, this catch-all would immediately wipe
    # it, causing subsequent user text to be silently ignored.
        #
    # NOTE: We use the first word (split on space) so that commands
    # with inline text (e.g. "/ask something") also match the same
    # early-return set as bare commands.  This is critical for the
    # reply-to-ask feature where ask_cmd handles the inline text
    # and pending_message_handler must NOT also process the event.
    if event.text and event.text.strip():
        _cmd_text = event.text.strip().split(maxsplit=1)[0]
        if _cmd_text in ("/start", "اکسی", "/cancel", "/arise",
                         "/w", "/sw", "/new", "/clear",
                         "/ask", "/learn", "/code", "/deep",
                         "/micheal", "/daye", "/zeussy", "/albrooks",
                         "/status", "/help", "/support",
                         "\U0001f4ca \u0698\u0648\u0631\u0646\u0627\u0644 \u0645\u0639\u0627\u0645\u0644\u0627\u062a"):
            return

    if uid in self.processing_users:
        return
    self.processing_users.add(uid)

    image_data = None
    if event.photo:
        image_data = await event.download_media(bytes)

    # Block checks
    if uid not in self.admin_ids and self.check_user_blocked(uid):
        self.processing_users.discard(uid)
        await event.reply("⛔️ شما توسط ادمین از دسترسی به ربات مسدود شده‌اید.")
        return

    chat_id = getattr(event, 'chat_id', None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        self.processing_users.discard(uid)
        return

    # ── Channel Watcher state check ───────────────────────────────────
    # If the user is in one of the Channel Watcher conversational states
    # (setup flow or Ask AI), route the text to the appropriate handler
    # and return early to avoid the normal AI processing path.
    #
    # NOTE: we use ``peek_state`` (not ``get_state``) here so this per-message
    # check does NOT refresh the idle timeout.  Only states that genuinely
    # expect *text* input are routed; button-only states (delivery choice,
    # confirm, delete confirm) fall through to normal processing so a user
    # who abandons a panel is never trapped.
    _cw_state = None
    try:
        from channel_watcher.states import get_state_manager as _get_cw_mgr
        from channel_watcher.states import UserState as CWState
        _cw_state_mgr = _get_cw_mgr()
        _cw_state, _cw_data = await _cw_state_mgr.peek_state(uid)
    except ImportError:
        _cw_state = None

    if _cw_state is not None:
        _cw_text_routes = {
            "AWAITING_CHANNEL_LINK": ("channel_watcher.handlers.setup", "cw_handle_channel_link"),
            "AWAITING_GROUP_LINK": ("channel_watcher.handlers.setup", "cw_handle_group_link"),
            "AWAITING_ASK_AI": ("channel_watcher.handlers.callbacks", "cw_handle_ask_ai_message"),
            "AWAITING_INTERVAL": ("channel_watcher.handlers.settings", "cw_set_interval_save"),
            "AWAITING_SYSTEM_PROMPT": ("channel_watcher.handlers.settings", "cw_set_prompt_save"),
        }
        _route = _cw_text_routes.get(_cw_state.name)
        if _route:
            self.processing_users.discard(uid)
            import importlib
            _handler = getattr(importlib.import_module(_route[0]), _route[1])
            await _handler(event)
            return
        # Button-only CW state → don't swallow; fall through to normal flow.

    try:
        state = self.db.get_pending_state(uid)

        # ── Stale verify state recovery ──
        # If the user sends a text message while a verify panel is showing
        # (without clicking any verify buttons), we treat the verify as
        # implicitly accepted — clean it up, delete the verify message,
        # and set pending_question=True so their message is processed.
        pending_action = state.get("pending_action") or ""
        if pending_action.startswith("{"):
            self.db.delete_pending_state(uid)
            verify_msg = self.pending_message.pop((uid, "verify_msg"), None)
            if verify_msg:
                try:
                    await verify_msg.delete()
                except Exception:
                    pass
            # Check if user already has a pending question or action
            # Re-fetch clean state
            state = self.db.get_pending_state(uid)
            # Set default pending_question so the message is processed
            if not state["pending_question"] and not state["pending_action"]:
                self.db.save_pending_state(uid, pending_question=True, pending_action="skill_default")
                state = self.db.get_pending_state(uid)

        if state["pending_action"] == "CREATE_WINDOW_TITLE":
            if not event.text:
                self.processing_users.discard(uid)
                return
            title = event.text.strip()
            if title.startswith("/"):
                self.processing_users.discard(uid)
                return
            success = self.db.create_window(uid, title)
            self.db.delete_pending_state(uid)

            prev_msg = self.pending_message.pop((uid, "ask_ai"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass

            if success:
                safe_title = _sanitize_html(title)
                await event.reply(f"✅ پنجره جدید با نام <b>{safe_title}</b> ساخته شد.", parse_mode="html", buttons=[Button.inline("🗂 مدیریت پنجرههای مکالمه", b"manage_windows", style="primary")])
            else:
                await event.reply("❌ خطا: شما نمی‌توانید بیش از ۵ پنجره مکالمه داشته باشید.")
            self.processing_users.discard(uid)
            return

        if state["pending_action"] and state["pending_action"].startswith("RENAME_WINDOW_TITLE:"):
            if not event.text:
                self.processing_users.discard(uid)
                return
            title = event.text.strip()
            if title.startswith("/"):
                self.processing_users.discard(uid)
                return
            win_id = int(state["pending_action"].split(":")[1])

            if not self.db.is_window_owner(uid, win_id):
                await event.reply("❌ خطا: دسترسی غیرمجاز!")
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return

            self.db.rename_window(uid, win_id, title)
            self.db.delete_pending_state(uid)

            prev_msg = self.pending_message.pop((uid, "ask_ai"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass

            safe_title = _sanitize_html(title)
            await event.reply(f"✅ نام پنجره به <b>{safe_title}</b> تغییر یافت.", parse_mode="html", buttons=[Button.inline("🗂 مدیریت پنجرههای مکالمه", b"manage_windows", style="primary")])
            self.processing_users.discard(uid)
            return

        if uid in self.admin_ids and state["pending_action"] == "ADD_LOCK_DATA":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.processing_users.discard(uid)
                return
            try:
                if "|" in text:
                    parts = [p.strip() for p in text.split("|")]
                elif "\n" in text:
                    parts = [p.strip() for p in text.split("\n") if p.strip()]
                elif "," in text:
                    parts = [p.strip() for p in text.split(",") if p.strip()]
                else:
                    parts = [p.strip() for p in text.split() if p.strip()]

                if len(parts) < 3:
                    raise ValueError("فرمت نامعتبر است. باید شامل ۳ بخش باشد: آیدی عددی، نام، و لینک دعوت.")

                cleaned_id_str = clean_number_string(parts[0])
                if not cleaned_id_str:
                    raise ValueError(f"آیدی عددی نامعتبر است: '{parts[0]}'")

                channel_id = int(cleaned_id_str)

                if len(parts) > 3 and " " in text and "|" not in text and "\n" not in text and "," not in text:
                    title = " ".join(parts[1:-1])
                    invite_link = parts[-1]
                else:
                    title = parts[1]
                    invite_link = parts[2]

                self.db.add_lock(channel_id, title, invite_link)
                self.db.delete_pending_state(uid)

                prev_lock_msg = self.pending_message.pop((uid, "add_lock"), None)
                if prev_lock_msg:
                    try:
                        await prev_lock_msg.delete()
                    except Exception:
                        pass

                await event.reply(f"✅ قفل کانال/گروه <b>{title}</b> با موفقیت اضافه شد.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا در پردازش اطلاعات: {e}\n\nلطفا مجددا تلاش کنید یا انصراف دهید.")
            self.processing_users.discard(uid)
            return

        if uid in self.admin_ids and state["pending_action"] == "BLOCK_USER":
            text = event.text.strip()
            self.processing_users.discard(uid)
            self.db.delete_pending_state(uid)

            prev_msg = self.pending_message.pop((uid, "block_user"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass

            try:
                if "|" in text:
                    parts = [p.strip() for p in text.split("|")]
                    target_uid = int(clean_number_string(parts[0]))
                    reason = parts[1] if len(parts) > 1 else ""
                else:
                    target_uid = int(clean_number_string(text))
                    reason = ""

                self.db.block_user(target_uid, uid, reason)
                await event.reply(f"✅ کاربر <code>{target_uid}</code> با موفقیت بلاک شد.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            return

        if uid in self.admin_ids and state["pending_action"] == "BLOCK_GROUP":
            text = event.text.strip()
            self.processing_users.discard(uid)
            self.db.delete_pending_state(uid)

            prev_msg = self.pending_message.pop((uid, "block_group"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass

            try:
                if "|" in text:
                    parts = [p.strip() for p in text.split("|")]
                    group_id = int(clean_number_string(parts[0]))
                    reason = parts[1] if len(parts) > 1 else ""
                else:
                    group_id = int(clean_number_string(text))
                    reason = ""

                self.db.block_group(group_id, uid, reason)
                await event.reply(f"✅ گروه <code>{group_id}</code> با موفقیت بلاک شد.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            return

        # ── OpenAI Base URL ──
        if uid in self.admin_ids and state["pending_action"] == "OPENAI_SET_BASE_URL":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "openai_base_url"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            try:
                if not text.startswith("http"):
                    raise ValueError("URL باید با http یا https شروع شود.")
                self.db.delete_pending_state(uid)
                self.db.save_setting("openai_base_url", text)
                from api_http import refresh_openai_pool
                refresh_openai_pool(self.db)
                await event.reply(f"✅ Base URL OpenAI به <code>{text}</code> تغییر یافت.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            self.processing_users.discard(uid)
            return

        # ── OpenAI API Key ──
        if uid in self.admin_ids and state["pending_action"] == "OPENAI_ADD_KEY":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "openai_add_key"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            try:
                new_keys = [line.strip() for line in text.split("\n") if line.strip()]
                if not new_keys:
                    raise ValueError("هیچ کلیدی دریافت نشد.")
                self.db.delete_pending_state(uid)
                existing_json = self.db.get_setting("openai_api_keys", "[]")
                try:
                    existing_keys = json.loads(existing_json)
                except Exception:
                    existing_keys = []
                existing_keys.extend(new_keys)
                self.db.save_setting("openai_api_keys", json.dumps(existing_keys))
                from api_http import refresh_openai_pool
                refresh_openai_pool(self.db)
                await event.reply(f"✅ <code>{len(new_keys)}</code> کلید OpenAI اضافه شد. مجموع: <code>{len(existing_keys)}</code>", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            self.processing_users.discard(uid)
            return

        # ── CW Classifier Model ──
        if uid in self.admin_ids and state["pending_action"] == "CW_CLASSIFIER_MODEL":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "cw_classifier_model"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            try:
                self.db.delete_pending_state(uid)
                self.db.save_setting("cw_classifier_model", text)
                # Invalidate cached classifier service so it picks up the new model
                self._classifier_service = None
                await event.reply(f"✅ مدل Classifier به <code>{text}</code> تغییر یافت.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            self.processing_users.discard(uid)
            return

        # ── OpenAI Custom Model ──
        if uid in self.admin_ids and state["pending_action"] and state["pending_action"].startswith("OPENAI_CUSTOM_MODEL:"):
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            setting_key = state["pending_action"].replace("OPENAI_CUSTOM_MODEL:", "")
            self.db.delete_pending_state(uid)
            prev_msg = self.pending_message.pop((uid, "openai_custom_model"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            try:
                self.db.save_setting(setting_key, text)
                await event.reply(f"✅ مدل <code>{setting_key}</code> به <code>{text}</code> تغییر یافت.", parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            self.processing_users.discard(uid)
            return

        # ── Service Create: ID ──
        # Every admin flow in this function follows the same cleanup
        # contract: if the text starts with "/" (meaning the admin
        # typed a command mid-flow), we delete the pending state and
        # return so the command's own dedicated handler can process it.
        # Without delete_pending_state here, the abandoned pending_action
        # would intercept the NEXT text message the admin sends.
        if uid in self.admin_ids and state["pending_action"] == "SERVICE_CREATE_ID":
            text = (event.text or "").strip().lower().replace(" ", "_")
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "service_create"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            if not text or (not text.isalnum() and "_" not in text):
                msg = await event.reply(
                    "❌ شناسه نامعتبر. فقط حروف انگلیسی، عدد و _ مجاز است.\n\n"
                    "یک شناسه یکتا وارد کنید:\n"
                    "<code>nvidia</code>\n<code>agentrouter</code>\n<code>groq</code>",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
                self.processing_users.discard(uid)
                return
            existing = self.db.get_service(text)
            if existing:
                msg = await event.reply(
                    "❌ این شناسه قبلاً استفاده شده.\n\n"
                    "یک شناسه دیگر وارد کنید:",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
                self.processing_users.discard(uid)
                return
            self.db.save_setting("_temp_service_id", text)
            self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_NAME")
            msg = await event.reply(
                f"✅ شناسه: <code>{text}</code>\n\n"
                "حالا یک نام نمایشی برای سرویس وارد کنید:\n"
                "مثال: <code>Nvidia NIM</code>",
                buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                parse_mode="html"
            )
            self.pending_message[(uid, "service_create")] = msg
            self.processing_users.discard(uid)
            return

        # ── Service Create: Name ──
        if uid in self.admin_ids and state["pending_action"] == "SERVICE_CREATE_NAME":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "service_create"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            self.db.save_setting("_temp_service_name", text)
            self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_URL")
            msg = await event.reply(
                f"✅ نام: <code>{text}</code>\n\n"
                "حالا Base URL سرویس را وارد کنید:\n"
                "مثال:\n<code>https://integrate.api.nvidia.com/v1</code>",
                buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                parse_mode="html"
            )
            self.pending_message[(uid, "service_create")] = msg
            self.processing_users.discard(uid)
            return

        # ── Service Create: URL ──
        if uid in self.admin_ids and state["pending_action"] == "SERVICE_CREATE_URL":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "service_create"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            if not text.startswith("http"):
                msg = await event.reply(
                    "❌ URL باید با http یا https شروع شود.\n\n"
                    "Base URL سرویس را وارد کنید:\n"
                    "مثال:\n<code>https://integrate.api.nvidia.com/v1</code>",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
                self.processing_users.discard(uid)
                return
            try:
                self.db.save_setting("_temp_service_url", text)
                self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_KEY")
                msg = await event.reply(
                    f"✅ URL: <code>{text}</code>\n\n"
                    "حالا کلید(های) API را وارد کنید (هر کلید در یک خط):\n"
                    "مثال:\n<code>nvapi-xxxxx</code>",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
            except Exception as e:
                await event.reply(f"❌ خطا: {e}")
            self.processing_users.discard(uid)
            return

        # ── Service Create: Key ──
        if uid in self.admin_ids and state["pending_action"] == "SERVICE_CREATE_KEY":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "service_create"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            new_keys = [line.strip() for line in text.split("\n") if line.strip()]

            # ── Empty-key error path ─────────────────────────────────
            # The guide message that was shown to the admin (with
            # format examples) was just deleted in prev_msg.delete()
            # above.  If we only send a short error text and return,
            # the admin would lose the instructions and have to guess
            # the format.  To avoid that, we rebuild a fresh guide
            # message with the same buttons and save it back into
            # pending_message so the format example is visible again
            # on the next attempt.
            if not new_keys:
                self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_KEY")
                msg = await event.reply(
                    "❌ هیچ کلیدی دریافت نشد. دوباره امتحان کنید.\n\n"
                    "لطفا کلید(های) API را وارد کنید (هر کلید در یک خط):\n"
                    "مثال:\n"
                    "<code>nvapi-xxxxx</code>",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
                self.processing_users.discard(uid)
                return
            try:
                self.db.save_setting("_temp_service_keys", json.dumps(new_keys))
                msg = await event.reply(
                    f"✅ <code>{len(new_keys)}</code> کلید دریافت شد.\n\n"
                    "حالا نام مدلها را وارد کنید (هر کدام در یک خط):\n"
                    "خط ۱: مدل سرچ\n"
                    "خط ۲: مدل سوال سریع\n"
                    "خط ۳: مدل منتورها\n"
                    "خط ۴: مدل پشتیبان\n\n"
                    "مثال:\n"
                    "<code>nvidia/llama-3.1-8b-instruct\nnvidia/llama-3.1-8b-instruct\nnvidia/llama-3.1-70b-instruct\nnvidia/llama-3.1-8b-instruct</code>",
                    buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
                    parse_mode="html"
                )
                self.pending_message[(uid, "service_create")] = msg
                self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_MODELS")
            except Exception as e:
                await event.reply(f"❌ خطا در ذخیره کلیدها: {e}")
            self.processing_users.discard(uid)
            return

        # ── Service Create: Models ──
        if uid in self.admin_ids and state["pending_action"] == "SERVICE_CREATE_MODELS":
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            prev_msg = self.pending_message.pop((uid, "service_create"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            default_model = lines[0] if lines else "gpt-3.5-turbo"
            models = {
                "search": lines[0] if len(lines) > 0 else default_model,
                "quick_ask": lines[1] if len(lines) > 1 else default_model,
                "mentors": lines[2] if len(lines) > 2 else default_model,
                "fallback": lines[3] if len(lines) > 3 else default_model,
            }
            svc_id = self.db.get_setting("_temp_service_id", "")
            svc_name = self.db.get_setting("_temp_service_name", svc_id)
            svc_url = self.db.get_setting("_temp_service_url", "")
            try:
                svc_keys = json.loads(self.db.get_setting("_temp_service_keys", "[]"))
            except Exception:
                svc_keys = []

            try:
                service = {
                    "id": svc_id,
                    "name": svc_name,
                    "base_url": svc_url,
                    "api_keys": svc_keys,
                    "models": models
                }
                self.db.save_service(service)
                from api_http import get_service_manager
                get_service_manager(self.db).reload()

                await event.reply(
                    f"✅ <b>سرویس جدید ساخته شد!</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>ID:</b> <code>{svc_id}</code>\n"
                    f"<b>نام:</b> <code>{svc_name}</code>\n"
                    f"<b>URL:</b> <code>{svc_url}</code>\n"
                    f"<b>کلیدها:</b> <code>{len(svc_keys)}</code>\n\n"
                    f"<b>مدلها:</b>\n"
                    f"  سرچ: <code>{models['search']}</code>\n"
                    f"  سوال سریع: <code>{models['quick_ask']}</code>\n"
                    f"  منتورها: <code>{models['mentors']}</code>\n"
                    f"  پشتیبان: <code>{models['fallback']}</code>",
                    parse_mode="html"
                )
            except Exception as e:
                await event.reply(f"❌ خطا در ذخیره سرویس: {e}")
            for key in ["_temp_service_id", "_temp_service_name", "_temp_service_url", "_temp_service_keys"]:
                self.db.save_setting(key, "")
            self.db.delete_pending_state(uid)
            self.processing_users.discard(uid)
            return

        # ── Service Edit: Set URL ──
        if uid in self.admin_ids and state["pending_action"] and state["pending_action"].startswith("SERVICE_SET_URL:"):
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            svc_id = state["pending_action"].replace("SERVICE_SET_URL:", "")
            prev_msg = self.pending_message.pop((uid, "service_set_url"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            if not text.startswith("http"):
                await event.reply("❌ URL باید با http یا https شروع شود.")
                self.processing_users.discard(uid)
                return
            self.db.delete_pending_state(uid)
            svc = self.db.get_service(svc_id)
            if svc:
                svc["base_url"] = text
                self.db.save_service(svc)
                from api_http import get_service_manager
                get_service_manager(self.db).reload()
                await event.reply(f"✅ Base URL سرویس به <code>{text}</code> تغییر یافت.", parse_mode="html")
            else:
                await event.reply(f"❌ سرویس <code>{svc_id}</code> یافت نشد.", parse_mode="html")
            self.processing_users.discard(uid)
            return

        # ── Service Edit: Add Key ──
        if uid in self.admin_ids and state["pending_action"] and state["pending_action"].startswith("SERVICE_ADD_KEY:"):
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            svc_id = state["pending_action"].replace("SERVICE_ADD_KEY:", "")
            prev_msg = self.pending_message.pop((uid, "service_add_key"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            new_keys = [line.strip() for line in text.split("\n") if line.strip()]
            if not new_keys:
                await event.reply("❌ هیچ کلیدی دریافت نشد. دوباره امتحان کنید.")
                self.processing_users.discard(uid)
                return
            self.db.delete_pending_state(uid)
            svc = self.db.get_service(svc_id)
            if svc:
                svc.setdefault("api_keys", []).extend(new_keys)
                self.db.save_service(svc)
                from api_http import get_service_manager
                get_service_manager(self.db).reload()
                await event.reply(f"✅ <code>{len(new_keys)}</code> کلید اضافه شد.", parse_mode="html")
            else:
                await event.reply(f"❌ سرویس <code>{svc_id}</code> یافت نشد.", parse_mode="html")
            self.processing_users.discard(uid)
            return

        # ── Service Edit: Set Model ──
        if uid in self.admin_ids and state["pending_action"] and state["pending_action"].startswith("SERVICE_SET_MODEL:"):
            text = (event.text or "").strip()
            if text.startswith("/"):
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
                return
            parts = state["pending_action"].replace("SERVICE_SET_MODEL:", "").split(":", 1)
            svc_id = parts[0] if len(parts) > 0 else ""
            model_type = parts[1] if len(parts) > 1 else "fallback"
            prev_msg = self.pending_message.pop((uid, "service_set_model"), None)
            if prev_msg:
                try:
                    await prev_msg.delete()
                except Exception:
                    pass
            svc = self.db.get_service(svc_id)
            if svc:
                svc.setdefault("models", {})[model_type] = text
                self.db.save_service(svc)
                from api_http import get_service_manager
                get_service_manager(self.db).reload()
                await event.reply(f"✅ مدل <code>{model_type}</code> به <code>{text}</code> تغییر یافت.", parse_mode="html")
            else:
                await event.reply(f"❌ سرویس <code>{svc_id}</code> یافت نشد.", parse_mode="html")
            self.db.delete_pending_state(uid)
            self.processing_users.discard(uid)
            return

        is_private = event.is_private
        is_command = bool(event.text and event.text.startswith("/"))
        is_reply_to_bot = False
        # v2: `event.is_reply` / `event.get_reply_message()` were renamed to
        # `replied_message_id` / `get_replied_message()`. The compat layer keeps
        # the v1 names as thin wrappers over the v2 API, and adds `sender_id`
        # (v2 exposes only `.sender`). No behavioral change.
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id == (await self.bot.get_me()).id:
                is_reply_to_bot = True

        if not is_private and not is_command and not is_reply_to_bot and not state["pending_question"]:
            self.processing_users.discard(uid)
            return

        not_joined = await self.check_user_joined(uid)
        if not_joined:
            await self.send_join_warning(event, not_joined)
            self.processing_users.discard(uid)
            return

        if state["pending_question"]:
            if not is_reply_to_bot and not is_private:
                self.processing_users.discard(uid)
                return

            text = (event.text or "").strip()
            if text.startswith("/"):
                self.processing_users.discard(uid)
                return

            try:
                sub_type = self.get_user_subscription(uid)
                active_win = self.db.get_active_window(uid)

                is_limited, limit_msg = self.check_user_limits(uid)
                if is_limited:
                    self.db.delete_pending_state(uid)
                    await self.send_limit_notification(event, limit_msg, is_callback=False)
                    self.processing_users.discard(uid)
                    return

                menu_msg = self.pending_message.pop((uid, "menu_prompt"), None)
                if menu_msg:
                    try:
                        await menu_msg.delete()
                    except Exception:
                        pass

                prev_ai_msg = self.pending_message.pop((uid, "ask_ai"), None)
                prev_tokens = self.last_tokens.pop(uid, 0)
                is_group = event.chat_id and event.chat_id < 0

                # ── Track previous AI messages for button management ──
                # When user sends a new message, we need to track the
                # previous AI response so we can manage its buttons.
                # Buttons are disabled during waiting state, restored
                # on error, or collapsed on success (groups only).
                #
                # Support mode is the exception: its "entry" message (the
                # welcome banner or a previous turn's answer) isn't a
                # meaningful chat-history item worth keeping around with
                # disabled buttons — just delete it so the new answer
                # doesn't end up stacked underneath a stale banner.
                if prev_ai_msg:
                    if state.get("pending_mentor_key") == "support":
                        try:
                            await prev_ai_msg.delete()
                        except Exception:
                            pass
                    else:
                        if uid not in self.previous_ai_messages:
                            self.previous_ai_messages[uid] = []
                        # Save current buttons before they get modified
                        try:
                            current_buttons = prev_ai_msg.buttons or []
                            self.previous_ai_messages[uid].append((prev_ai_msg, current_buttons))
                        except Exception:
                            pass

                # ── Typing indicator ──────────────────────────────
                # Lightweight "bot is typing" feedback before the
                # heavier animation starts.  Costs zero API calls
                # and auto-expires after ~5 seconds.
                # v2: raw request is private + snake_case
                # (`tl.functions.messages.set_typing`); `peer=` needs an
                # InputPeer built from the event chat (no more entity cache).
                try:
                    await self.bot(tl.functions.messages.set_typing(
                        peer=event.chat.ref._to_input_peer(),
                        top_msg_id=None,
                        action=tl.types.SendMessageTypingAction()
                    ))
                except Exception:
                    pass  # Non-critical, safe to ignore

                msg = await event.reply("در حال پردازش درخواست شما... لطفا صبور باشید.", buttons=[Button.inline("لغو درخواست", b"clear_processing", style="danger")])

                animator = None
                try:
                    pending_action = state.get("pending_action") or ""
                    if pending_action.startswith("skill_"):
                        skill_key = pending_action.replace("skill_", "")
                        skill_prompt = get_skill_prompt(skill_key)
                        role = skill_prompt if skill_prompt else DEFAULT_SYSTEM_ROLE
                    else:
                        skill_key = "default"
                        role = state["pending_role"] if state["pending_role"] else DEFAULT_SYSTEM_ROLE
                    mentor_key = state["pending_mentor_key"]

                    animator = StatusAnimator(msg, mentor_key=mentor_key, skill_key=skill_key)
                    await animator.start()

                    async def on_status_change(status_text: str):
                        if animator:
                            animator.update_step(status_text)

                    async def on_tool_call(tool_event: ToolEvent):
                        if animator:
                            animator.update_tool(tool_event)

                    self.active_requests[uid] = {
                        "generation_started": False,
                        "msg": msg,
                        "task": asyncio.current_task(),
                        "animator": animator,
                        "cancel_requested": False,
                    }

                    async def on_generation_start():
                        if uid in self.active_requests:
                            self.active_requests[uid]["generation_started"] = True

                    # NOTE: Model selection is handled internally by
                    # AI_Service.handle_message() based on provider, mode,
                    # and DB settings.  Do NOT compute model_name here —
                    # it would be dead code.

                    if mentor_key:
                        # "support" is not a trading mentor persona — it's the
                        # AI Support Assistant mode (see telegram/handlers/support.py).
                        # It reuses this same generic mentor pipeline (window,
                        # rate-limit, animator) but resolves its own dedicated
                        # model settings via mode="support" in AI_Service.handle_message.
                        _conv_mode = "support" if mentor_key == "support" else "mentor"
                        ai_service = self.get_ai_service(uid, mode=_conv_mode, mentor_key=mentor_key, chat_id=chat_id)
                        tlogger.debug(f"[FLOW DEBUG] >>> handle_message: uid={uid}, mode={_conv_mode}, mentor={mentor_key}")
                        answer, tokens_used = await ai_service.handle_message(
                            user_message=text,
                            role=role,
                            mode=_conv_mode,
                            on_status_change=on_status_change,
                            on_generation_start=on_generation_start,
                            on_tool_call=on_tool_call,
                            image_data=image_data
                        )
                        tlogger.debug(f"[FLOW DEBUG] <<< handle_message done: answer_len={len(answer)}, pending_image={getattr(ai_service, 'pending_image_path', None)}")
                        if mentor_key == "support":
                            from .support import build_support_turn_buttons
                            buttons, _closing_note = build_support_turn_buttons(self, uid, ai_service, tokens_used)
                            answer = f"{answer}{_closing_note}"
                        else:
                            buttons = [
                                [
                                    Button.inline("Tap For Talk", f"Amentors_{mentor_key}_{uid}".encode(), style="success"),
                                    Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                                ],
                                [Button.inline(f"👤 منتور: {mentor_key} | 🪙 {tokens_used:,}", b"place_holder")]
                            ]
                    else:
                        ai_service = self.get_ai_service(uid, mode="quick_ask", chat_id=chat_id)
                        tlogger.debug(f"[FLOW DEBUG] >>> handle_message: uid={uid}, mode=quick_ask")
                        answer, tokens_used = await ai_service.handle_message(
                            user_message=text,
                            role=role,
                            mode="quick_ask",
                            on_status_change=on_status_change,
                            on_generation_start=on_generation_start,
                            on_tool_call=on_tool_call,
                            image_data=image_data
                        )
                        tlogger.debug(f"[FLOW DEBUG] <<< handle_message done: answer_len={len(answer)}, pending_image={getattr(ai_service, 'pending_image_path', None)}")
                        
                        skill_key = pending_action.replace("skill_", "") if pending_action.startswith("skill_") else "default"
                        skill_data = get_skill(skill_key)
                        
                        buttons = [
                            [
                                Button.inline("Tap For Talk", f"Aquickask_{uid}_{skill_key}".encode(), style="success"),
                                Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                            ],
                            [Button.inline(f"{skill_data['icon']} {skill_data['name']} | 🪙 {tokens_used:,}", b"place_holder")]
                        ]

                    self.last_tokens[uid] = tokens_used

                    if animator:
                        await animator.stop()

                    await _send_response(msg, event, ai_service, answer, buttons, uid)

                    # ── Clean up previous AI messages AFTER response ──
                    # In groups, all previous AI messages are collapsed to save
                    # space. This runs AFTER _send_response so the user sees
                    # the new response before old messages are collapsed.
                    # In PV, previous messages keep info-only buttons but the
                    # list is still cleared to prevent memory leak (Bug #24).
                    if uid in self.previous_ai_messages:
                        if is_group:
                            for prev_msg, _ in self.previous_ai_messages[uid]:
                                try:
                                    # v2: `message.message`/`raw_text` were removed
                                    # in favor of `message.text`. The compat layer
                                    # aliases `.message` -> `.text`, so this
                                    # `prev_msg.text or prev_msg.message` fallback
                                    # is harmless (both yield the same text).
                                    full_text = prev_msg.text or prev_msg.message or ""
                                    lines = full_text.split('\n')
                                    collapsed = '\n'.join(lines[:3]) + ('\n...' if len(lines) > 3 else '')
                                    _mk = state.get("pending_mentor_key")
                                    _pa = state.get("pending_action") or ""
                                    if _mk:
                                        _info_saved = f"👤 منتور: {_mk} | 🪙 {prev_tokens:,}"
                                    else:
                                        _sk = _pa.replace("skill_", "") if _pa.startswith("skill_") else "default"
                                        _sd = get_skill(_sk)
                                        _info_saved = f"{_sd['icon']} {_sd['name']} | 🪙 {prev_tokens:,}"
                                    self.collapsed_messages[prev_msg.id] = {
                                        "chat_id": event.chat_id,
                                        "full_text": full_text,
                                        "info_text": _info_saved,
                                        "collapse_task": None,
                                        "auto_collapse_disabled": False,
                                    }
                                    await prev_msg.edit(collapsed, buttons=[
                                        [Button.inline("🔽 Expand", f"expand:{event.chat_id}:{prev_msg.id}".encode(), style="primary")]
                                    ])
                                except Exception:
                                    pass
                        # Clear previous messages list for both PV and groups
                        # to prevent unbounded memory growth (Bug #24).
                        self.previous_ai_messages.pop(uid, None)

                except asyncio.CancelledError:
                    tlogger.info(f"Request for user {uid} was cancelled.")
                    # Restore previous buttons on cancellation
                    await _restore_previous_ai_buttons(self, uid)
                    raise
                except Exception as e:
                    tlogger.error(f"quick ask failed: {e}")
                    # Restore previous buttons on error
                    await _restore_previous_ai_buttons(self, uid)
                    
                    # CRITICAL BUG FIX: Stop animator BEFORE editing message to prevent
                    # race condition where animator overwrites the error message.
                    # This fixes Bug #[RETRY_FREEZE] where retry button would freeze
                    # on second failure because animator kept running after error.
                    if animator:
                        try:
                            await animator.stop()
                            animator = None  # Prevent double-stop in finally block
                        except Exception as animator_error:
                            tlogger.debug(f"Error stopping animator: {animator_error}")
                    
                    self.failed_messages[uid] = {
                        "text": text,
                        "mentor_key": mentor_key,
                        "role": role,
                        "skill_key": skill_key,
                        "msg_id": msg.id
                    }
                    try:
                        retry_btn = [Button.inline("🔄 تلاش مجدد", f"retry_failed:{uid}".encode(), style="primary")]
                        await msg.edit("پردازش درخواست با خطا مواجه شد. لطفا مجددا تلاش کنید.", buttons=[retry_btn])
                    except Exception:
                        await event.reply("پردازش درخواست با خطا مواجه شد. لطفا مجددا تلاش کنید.")
                finally:
                    # Only stop if not already stopped in except block
                    if animator:
                        try:
                            await animator.stop()
                        except Exception:
                            pass
                    self.active_requests.pop(uid, None)
            finally:
                self.db.delete_pending_state(uid)
                self.processing_users.discard(uid)
            return

        self.processing_users.discard(uid)

    except Exception as e:
        tlogger.error(f"Error in pending_message_handler: {e}")
        self.processing_users.discard(uid)


async def retry_failed_handler(self: "TelegramBot", event: Any) -> None:
    """Retry a previously failed AI request.

    Triggered by the "retry" button on failed messages. Retrieves the
    stored request info (text, mentor, role, skill) and re-runs the
    AI generation pipeline. Only the original user can retry their request.

    Usage limits are re-checked before consuming resources so that
    a user who has hit their 12-hour ceiling cannot bypass the limit
    by clicking the retry button (Bug #RETRY_LIMITS).

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event with format "retry_failed:<msg_id>".
    """
    uid = event.sender_id
    if uid not in self.failed_messages:
        await event.answer("اطلاعات پیام ناموفق یافت نشد.", alert=True)
        return

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return

    chat_id = getattr(event, 'chat_id', None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        await event.answer()
        return

    failed_info = self.failed_messages[uid]
    text = failed_info["text"]
    mentor_key = failed_info["mentor_key"]
    role = failed_info["role"]
    skill_key = failed_info.get("skill_key", "default")
    msg_id = failed_info["msg_id"]

    if uid in self.processing_users:
        await event.answer("در حال پردازش درخواست قبلی شما هستیم...", alert=True)
        return
    self.processing_users.add(uid)

    animator = None
    try:
        sub_type = self.get_user_subscription(uid)

        # Re-check usage limits — the request that failed might have been
        # the one that pushed the user over the edge, or limits may have
        # been hit by another concurrent session since the original request.
        is_limited, limit_msg = self.check_user_limits(uid)
        if is_limited:
            from ..utils import get_time_until_reset
            time_left = get_time_until_reset()
            await event.answer(
                f"❌ شما به سقف مجاز مصرف خود رسیده‌اید!\n⏳ {time_left} تا ریست",
                alert=True
            )
            return

        msg = await self.bot.get_messages(event.chat_id, ids=msg_id)
        if not msg:
            msg = event

        animator = StatusAnimator(msg, mentor_key=mentor_key, skill_key=skill_key)
        await animator.start()

        async def on_status_change(status_text: str):
            if animator:
                animator.update_step(status_text)

        self.active_requests[uid] = {
            "generation_started": False,
            "msg": msg,
            "task": asyncio.current_task(),
            "animator": animator,
            "cancel_requested": False,
        }

        async def on_generation_start():
            if uid in self.active_requests:
                self.active_requests[uid]["generation_started"] = True

        async def on_tool_call(tool_event: ToolEvent):
            if animator:
                animator.update_tool(tool_event)

        if mentor_key:
            _conv_mode = "support" if mentor_key == "support" else "mentor"
            ai_service = self.get_ai_service(uid, mode=_conv_mode, mentor_key=mentor_key, chat_id=chat_id)
            answer, tokens_used = await ai_service.handle_message(
                user_message=text,
                role=role,
                mode=_conv_mode,
                on_status_change=on_status_change,
                on_generation_start=on_generation_start,
                on_tool_call=on_tool_call
            )
            if mentor_key == "support":
                from .support import build_support_turn_buttons
                buttons, _closing_note = build_support_turn_buttons(self, uid, ai_service, tokens_used)
                answer = f"{answer}{_closing_note}"
            else:
                buttons = [
                    [
                        Button.inline("Tap For Talk", f"Amentors_{mentor_key}_{uid}".encode(), style="success"),
                        Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                    ],
                    [Button.inline(f"👤 منتور: {mentor_key} | 🪙 {tokens_used:,}", b"place_holder")]
                ]
        else:
            skill_data = get_skill(skill_key)
            ai_service = self.get_ai_service(uid, mode="quick_ask", chat_id=chat_id)
            answer, tokens_used = await ai_service.handle_message(
                user_message=text,
                role=role,
                mode="quick_ask",
                on_status_change=on_status_change,
                on_generation_start=on_generation_start,
                on_tool_call=on_tool_call
            )
            buttons = [
                [
                    Button.inline("Tap For Talk", f"Aquickask_{uid}_{skill_key}".encode(), style="success"),
                    Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                ],
                [Button.inline(f"{skill_data['icon']} {skill_data['name']} | 🪙 {tokens_used:,}", b"place_holder")]
            ]

        if animator:
            await animator.stop()

        await _send_response(msg, event, ai_service, answer, buttons, uid)

        self.failed_messages.pop(uid, None)

    except asyncio.CancelledError:
        tlogger.info(f"Retry request for user {uid} was cancelled.")
    except Exception as e:
        tlogger.error(f"Retry failed: {e}")
        
        # CRITICAL FIX: Stop animator BEFORE editing message to prevent race condition.
        # Previously, the animator could overwrite the error message if it was still
        # running when we tried to edit the message, causing the "freeze" bug where
        # the message would stay in "processing" state forever.
        if animator:
            try:
                await animator.stop()
                animator = None  # Prevent double-stop in finally block
            except Exception as anim_err:
                tlogger.warning(f"Error stopping animator in retry error handler: {anim_err}")
        
        # Now safe to edit message without race condition
        try:
            retry_btn = [Button.inline("🔄 تلاش مجدد", f"retry_failed:{uid}".encode(), style="primary")]
            await msg.edit("پردازش درخواست با خطا مواجه شد. لطفا مجددا تلاش کنید.", buttons=[retry_btn])
        except Exception:
            try:
                await event.reply("پردازش درخواست با خطا مواجه شد. لطفا مجددا تلاش کنید.")
            except Exception:
                pass
        await event.answer()
    finally:
        # Only stop animator if it wasn't already stopped in the except block
        if animator:
            try:
                await animator.stop()
            except Exception:
                pass
        self.active_requests.pop(uid, None)
        self.processing_users.discard(uid)


async def inline_handler(self: "TelegramBot", event: Any) -> None:
    """Handle direct mentor-mode messages sent via inline article buttons.

    When a user selects a mentor from the inline query results, this handler
    processes the message. It matches the message text against known inline
    articles to determine the mentor key, then runs the AI pipeline.

    EARLY GATE CHECKS
    Because this handler registers as a catch-all ``events.NewMessage``
    handler (no pattern filter), it fires for EVERY text message alongside
    ``pending_message_handler``.  Early gates prevent unnecessary work:
      1. Pending state check — if the user is already in a pending action
         or AI question flow, ``pending_message_handler`` will handle it.
      2. ``processing_users`` — skip if the user is currently waiting for
         an AI response.
      3. Command check — commands have their own dedicated handlers.
      4. Inline article match — only process text that contains one of the
         known mentor identifiers (e.g. ``\\u200bmicheal``).

    Without these gates, the handler would attempt to process every regular
    text message twice (once here, once in ``pending_message_handler``),
    potentially creating duplicate sessions or confusing the user.

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: Incoming NewMessage event containing inline article text.
    """
    uid = event.sender_id

    if not event.text:
        return

    if uid in self.processing_users:
        return

    if event.text.startswith("/"):
        return

    raw_id = next((i[2] for i in self.inline_article if i[2].strip() in event.text), None)
    if not raw_id:
        return

    self.processing_users.add(uid)

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        self.processing_users.discard(uid)
        await event.reply("⛔️ شما توسط ادمین از دسترسی به ربات مسدود شده‌اید.")
        return

    chat_id = getattr(event, 'chat_id', None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        self.processing_users.discard(uid)
        return

    animator = None
    try:
        not_joined = await self.check_user_joined(uid)
        if not_joined:
            await self.send_join_warning(event, not_joined)
            self.processing_users.discard(uid)
            return

        is_limited, limit_msg = self.check_user_limits(uid)
        if is_limited:
            await self.send_limit_notification(event, limit_msg, is_callback=False)
            self.processing_users.discard(uid)
            return

        mentor_key = raw_id.replace("\u200b", "").strip()

        role_prompt = self.mentor_prompts.get(mentor_key)
        if not role_prompt:
            tlogger.warning("پیدا نشدن پرامپت منتور یا آیدی منتور نامعتبر")
            await event.reply("پرامپت این منتور پیدا نشد.")
            self.processing_users.discard(uid)
            return

        msg = await event.reply("در حال پردازش درخواست شما... لطفا صبور باشید.", buttons=[Button.inline("لغو درخواست", b"clear_processing", style="danger")])

        animator = StatusAnimator(msg, mentor_key=mentor_key, skill_key="default")
        await animator.start()

        async def on_status_change(status_text: str):
            if animator:
                animator.update_step(status_text)

        async def on_tool_call(tool_event: ToolEvent):
            if animator:
                animator.update_tool(tool_event)

        self.active_requests[uid] = {
            "generation_started": False,
            "msg": msg,
            "task": asyncio.current_task(),
            "animator": animator,
            "cancel_requested": False,
        }

        async def on_generation_start():
            if uid in self.active_requests:
                self.active_requests[uid]["generation_started"] = True

        ai_service = self.get_ai_service(uid, mode="mentor", mentor_key=mentor_key, chat_id=chat_id)
        ai_answer, tokens_used = await ai_service.handle_message(
            user_message=event.text,
            role=role_prompt,
            mode="mentor",
            on_status_change=on_status_change,
            on_generation_start=on_generation_start,
            on_tool_call=on_tool_call
        )
        buttons = [
            [
                Button.inline("Tap For Talk", f"Amentors_{mentor_key}_{uid}".encode(), style="success"),
                Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
            ],
            [Button.inline(f"👤 منتور: {mentor_key} | 🪙 {tokens_used:,}", b"place_holder")]
        ]

        if animator:
            await animator.stop()

        await _send_response(msg, event, ai_service, ai_answer, buttons, uid)

        if chat_id and chat_id < 0:
            self.pending_message[(uid, "ask_ai")] = msg

    except asyncio.CancelledError:
        tlogger.info(f"Inline request for user {uid} was cancelled.")
    except Exception as e:
        tlogger.error(f"'inline_handler' func has an issue: {e}", exc_info=True)
        await event.reply("خطا در پردازش درخواست inline. لطفا مجددا تلاش کنید.")
    finally:
        if animator:
            await animator.stop()
        self.active_requests.pop(uid, None)
        self.processing_users.discard(uid)


async def again_talk_mentor(self: "TelegramBot", event: Any) -> None:
    """Handle the "send message" button for mentor mode conversations.

    Triggered when user clicks the confirm button after seeing a mentor's
    intro message. Validates ownership, checks limits, then prompts the
    user to type their question with a text input prompt.

    Callback data format: "Amentors_{mentor_key}_{owner_id}"

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event.
    """
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return

    chat_id = getattr(event, 'chat_id', None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        await event.answer()
        return

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    data = event.data.decode().replace("Amentors_", "").strip()
    parts = data.split("_")
    if len(parts) < 2:
        await event.answer()
        return

    key_mentor = parts[0]
    owner_id = int(parts[1])

    if uid != owner_id:
        await event.answer("این دکمه متعلق به کاربر دیگری است!", alert=True)
        return

    if uid in self.processing_users:
        await event.answer("در حال پردازش درخواست قبلی شما هستیم...", alert=True)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        from ..utils import get_time_until_reset
        time_left = get_time_until_reset()
        await event.answer(
            f"❌ شما به سقف مجاز مصرف خود رسیده‌اید!\n⏳ {time_left} تا ریست",
            alert=True
        )
        return

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        pending_msg_entry = self.pending_message.get((uid, "ask_ai"))
        current_msg = await event.get_message()
        if pending_msg_entry and current_msg and current_msg.id == pending_msg_entry.id:
            self.db.delete_pending_state(uid)
            self.processing_users.discard(uid)
            self.pending_message.pop((uid, "ask_ai"), None)

            # ── Restore previous AI message buttons on cancel ──
            await _restore_previous_ai_buttons(self, uid)
            self.previous_ai_messages.pop(uid, None)

            prev_tokens_cancel = self.last_tokens.get(uid, 0)
            try:
                await current_msg.edit(buttons=[
                    [
                        Button.inline("Tap For Talk", f"Amentors_{key_mentor}_{uid}".encode(), style="success"),
                        Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                    ],
                    [Button.inline(f"👤 منتور: {key_mentor} | 🪙 {prev_tokens_cancel:,}", b"place_holder")]
                ])
            except MessageNotModifiedError:
                pass  # Buttons already in desired state
            await event.answer("✅ لغو شد")
            return
        else:
            await event.answer("یک مکالمه باز دارید.", alert=True)
            await event.respond(
                "یک مکالمه باز دارید.",
                buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
            )
            return

    active_win = self.db.get_active_window(uid)
    total_win_tokens = active_win["total_input_tokens"] + active_win["total_output_tokens"]
    if total_win_tokens > 50000:
        await event.answer(
            f"⚠️ هشدار مصرف توکن بالا!\n\n"
            f"مجموع توکن‌های این پنجره ({total_win_tokens:,}) سنگین شده است. خلاصه‌سازی خودکار فعال است، اما در صورت نیاز حافظه را پاکسازی کنید.",
            alert=True
        )

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        await event.answer(alert_msg, alert=True)
        return
    else:
        await event.answer()

    # Second-verify check: intercept if mentor idle > 5 hours
    mentor_win = self.db.get_mentor_window(uid, key_mentor)
    if should_verify(mentor_win, "mentor", key_mentor):
        msg = await event.get_message()
        await send_verify_mentor(self, event, uid, key_mentor, entry_type="tap", original_msg_id=msg.id if msg else None)
        return

    if key_mentor and self.mentor_prompts.get(key_mentor):
        self.pending_message.pop((uid, "ask_ai"), None)

        self.db.save_pending_state(uid, pending_question=True, pending_role=self.mentor_prompts.get(key_mentor), pending_mentor_key=key_mentor)

        # ── Disable previous AI message buttons ──
        # When user enters waiting state, disable interactive buttons
        # on all previous AI messages to prevent stale interactions.
        await _disable_previous_ai_buttons(self, uid)

        msg = await event.get_message()
        try:
            await msg.edit(buttons=[
                [
                    Button.inline("✅ پیامت رو بفرست", f"Amentors_{key_mentor}_{uid}".encode(), style="danger"),
                    Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                ]
            ])
        except MessageNotModifiedError:
            pass  # Buttons already in desired state
        self.pending_message[(uid, "ask_ai")] = msg


async def again_talk_quickask(self: "TelegramBot", event: Any) -> None:
    """Handle the "send message" button for quick-ask / skill mode.

    Triggered when user clicks the confirm button to start a conversation
    in quick_ask mode or a specific skill. Validates ownership and limits,
    then prompts the user to type their question.

    Callback data format: "Aquickask_{owner_id}_{skill_key}"

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event.
    """
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return

    chat_id = getattr(event, 'chat_id', None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        await event.answer()
        return

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    data = event.data.decode().replace("Aquickask_", "").strip()
    # Format: uid_skillkey (e.g., "123456789_learn")
    parts = data.rsplit("_", 1)
    if len(parts) != 2:
        await event.answer()
        return

    try:
        owner_id = int(parts[0])
    except ValueError:
        await event.answer()
        return

    skill_key = parts[1]

    if uid != owner_id:
        await event.answer("این دکمه متعلق به کاربر دیگری است!", alert=True)
        return

    if uid in self.processing_users:
        await event.answer("در حال پردازش درخواست قبلی شما هستیم...", alert=True)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        from ..utils import get_time_until_reset
        time_left = get_time_until_reset()
        await event.answer(
            f"❌ شما به سقف مجاز مصرف خود رسیده‌اید!\n⏳ {time_left} تا ریست",
            alert=True
        )
        return

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        pending_msg_entry = self.pending_message.get((uid, "ask_ai"))
        current_msg = await event.get_message()
        if pending_msg_entry and current_msg and current_msg.id == pending_msg_entry.id:
            self.db.delete_pending_state(uid)
            self.processing_users.discard(uid)
            self.pending_message.pop((uid, "ask_ai"), None)

            # ── Restore previous AI message buttons on cancel ──
            await _restore_previous_ai_buttons(self, uid)
            self.previous_ai_messages.pop(uid, None)

            skill_data = get_skill(skill_key)
            prev_tokens_cancel = self.last_tokens.get(uid, 0)
            try:
                await current_msg.edit(buttons=[
                    [
                        Button.inline("Tap For Talk", f"Aquickask_{uid}_{skill_key}".encode(), style="success"),
                        Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                    ],
                    [Button.inline(f"{skill_data['icon']} {skill_data['name']} | 🪙 {prev_tokens_cancel:,}", b"place_holder")]
                ])
            except MessageNotModifiedError:
                pass  # Buttons already in desired state
            await event.answer("✅ لغو شد")
            return
        else:
            await event.answer("یک مکالمه باز دارید.", alert=True)
            await event.respond(
                "یک مکالمه باز دارید.",
                buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
            )
            return

    active_win = self.db.get_active_window(uid)
    total_win_tokens = active_win["total_input_tokens"] + active_win["total_output_tokens"]
    if total_win_tokens > 50000:
        await event.answer(
            f"⚠️ هشدار مصرف توکن بالا!\n\n"
            f"مجموع توکن‌های این پنجره ({total_win_tokens:,}) سنگین شده است. خلاصه‌سازی خودکار فعال است، اما در صورت نیاز حافظه را پاکسازی کنید.",
            alert=True
        )

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        await event.answer(alert_msg, alert=True)
        return
    else:
        await event.answer()

    # Second-verify check: intercept if user has been idle > 3 hours
    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if should_verify(active_win, "quick_ask"):
        msg = await event.get_message()
        await send_verify_quick_ask(self, event, uid, skill_key, entry_type="tap", original_msg_id=msg.id if msg else None)
        return

    self.pending_message.pop((uid, "ask_ai"), None)

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{skill_key}")

    # ── Disable previous AI message buttons ──
    # When user enters waiting state, disable interactive buttons
    # on all previous AI messages to prevent stale interactions.
    await _disable_previous_ai_buttons(self, uid)

    buttons = [[
        Button.inline("✅ پیامت رو بفرست", f"Aquickask_{uid}_{skill_key}".encode(), style="danger"),
        Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
    ]]
    row = []
    for sk, sd in SKILLS.items():
        if sk == "default":
            continue
        is_active = (sk == skill_key)
        btn_text = f"{'✅ ' if is_active else ''}{sd['icon']} {sd['name']}"
        btn_data = f"skill_toggle:{sk}"
        style = "success" if is_active else "primary"
        row.append(Button.inline(btn_text, btn_data.encode(), style=style))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    msg = await event.get_message()
    try:
        await msg.edit(buttons=buttons)
    except MessageNotModifiedError:
        pass  # Buttons already in desired state
    self.pending_message[(uid, "ask_ai")] = msg


async def clear_processing(self: "TelegramBot", event: Any) -> None:
    """Cancel an in-flight AI request with double-tap safety.

    Cancellation behaviour depends on the request phase:

    * **Before generation** (``generation_started=False``):
      Immediately cancels the task, stops the animator, and cleans up.
    * **During generation** (``generation_started=True``):
      First tap sets ``cancel_requested`` and shows a confirmation
      alert.  Second tap (when ``cancel_requested`` is already True)
      performs the actual cancellation.

    In both paths the animator is stopped so the animated message
    ceases to be edited.

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event from the "cancel" button.
    """
    uid = event.sender_id

    if uid not in self.active_requests:
        await event.answer("درخواست فعالی یافت نشد.", alert=True)
        return

    req_info = self.active_requests[uid]

    # ── Second tap: already confirmed — force cancel ──────────────
    if req_info.get("cancel_requested", False):
        animator = req_info.get("animator")
        if animator:
            await animator.stop()

        task = req_info.get("task")
        if task:
            task.cancel()

        self.active_requests.pop(uid, None)
        self.processing_users.discard(uid)
        self.db.delete_pending_state(uid)

        try:
            await event.edit("✅ درخواست با موفقیت لغو شد.")
        except Exception:
            pass
        return

    # ── Before generation started — cancel immediately ────────────
    if not req_info.get("generation_started", False):
        animator = req_info.get("animator")
        if animator:
            await animator.stop()

        task = req_info.get("task")
        if task:
            task.cancel()

        self.active_requests.pop(uid, None)
        self.processing_users.discard(uid)
        self.db.delete_pending_state(uid)

        try:
            await event.edit("✅ درخواست با موفقیت لغو شد.")
        except Exception:
            pass
        return

    # ── Generation in progress — first tap: ask for confirmation ──
    req_info["cancel_requested"] = True
    await event.answer(
        "⚠️ مدل درحال پاسخ نهاییه. اگه از لغو مطمئنی یکبار دیگه رو دکمه لغو بزن",
        alert=True,
    )


async def expand_cb(self: "TelegramBot", event: Any) -> None:
    """Expand a collapsed group message to show full AI response.

    In groups, AI responses are collapsed to the first 3 lines to save space.
    This handler restores the full text and adds an auto-collapse toggle button.
    The auto-collapse feature re-collapses after 3 minutes of inactivity.

    Callback data format: "expand:{chat_id}:{msg_id}"

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event.
    """
    uid = event.sender_id
    data = event.data.decode()
    parts = data.replace("expand:", "").split(":")
    if len(parts) != 2:
        await event.answer()
        return

    try:
        chat_id = int(parts[0])
        msg_id = int(parts[1])
    except (ValueError, IndexError):
        await event.answer()
        return

    msg_info = self.collapsed_messages.get(msg_id)
    if not msg_info:
        await event.answer("این پیام منقضی شده است.", alert=True)
        return

    full_text = msg_info.get("full_text", "")
    if not full_text:
        await event.answer()
        return

    chat_id_to_use = msg_info.get("chat_id")
    if not chat_id_to_use:
        await event.answer()
        return

    info_text = msg_info.get("info_text", "")

    auto_collapse_disabled = msg_info.get("auto_collapse_disabled", False)
    if auto_collapse_disabled:
        toggle_btn = Button.inline("❌ کلاپس خودکار", f"auto_collapse_toggle:{chat_id_to_use}:{msg_id}".encode(), style="danger")
    else:
        toggle_btn = Button.inline("✅ کلاپس خودکار", f"auto_collapse_toggle:{chat_id_to_use}:{msg_id}".encode(), style="success")

    await self.bot.edit_message(chat_id_to_use, msg_id, full_text, buttons=[
        [Button.inline(info_text, b"place_holder")] if info_text else [],
        [toggle_btn]
    ])
    await event.answer()

    if not auto_collapse_disabled:
        existing_task = msg_info.get("collapse_task")
        if existing_task and not existing_task.done():
            existing_task.cancel()

        async def auto_collapse():
            await asyncio.sleep(180)
            if msg_info.get("auto_collapse_disabled"):
                return
            try:
                full = msg_info.get("full_text", "")
                lines = full.split('\n')
                collapsed = '\n'.join(lines[:3]) + ('\n...' if len(lines) > 3 else '')
                await self.bot.edit_message(chat_id_to_use, msg_id, collapsed, buttons=[
                    [Button.inline("🔽 Expand", f"expand:{chat_id_to_use}:{msg_id}".encode(), style="primary")]
                ])
            except Exception:
                pass

        task = asyncio.create_task(auto_collapse())
        msg_info["collapse_task"] = task


async def auto_collapse_toggle_cb(self: "TelegramBot", event: Any) -> None:
    """Toggle the auto-collapse feature for a group message.

    When enabled, expanded messages automatically collapse after 3 minutes.
    When disabled, the message stays expanded until manually collapsed.

    Callback data format: "auto_collapse_toggle:{chat_id}:{msg_id}"

    Args:
        self: TelegramBot instance (bound via descriptor).
        event: CallbackQuery event.
    """
    data = event.data.decode()
    parts = data.replace("auto_collapse_toggle:", "").split(":")
    if len(parts) != 2:
        await event.answer()
        return

    try:
        chat_id = int(parts[0])
        msg_id = int(parts[1])
    except (ValueError, IndexError):
        await event.answer()
        return

    msg_info = self.collapsed_messages.get(msg_id)
    if not msg_info:
        await event.answer("این پیام منقضی شده است.", alert=True)
        return

    is_disabled = msg_info.get("auto_collapse_disabled", False)
    msg_info["auto_collapse_disabled"] = not is_disabled

    info_text = msg_info.get("info_text", "")

    if msg_info["auto_collapse_disabled"]:
        existing_task = msg_info.get("collapse_task")
        if existing_task and not existing_task.done():
            existing_task.cancel()
        await self.bot.edit_message(chat_id, msg_id, buttons=[
            [Button.inline(info_text, b"place_holder")] if info_text else [],
            [Button.inline("❌ کلاپس خودکار", f"auto_collapse_toggle:{chat_id}:{msg_id}".encode(), style="danger")]
        ])
        await event.answer("✅ کلاپس خودکار غیرفعال شد")
    else:
        await self.bot.edit_message(chat_id, msg_id, buttons=[
            [Button.inline(info_text, b"place_holder")] if info_text else [],
            [Button.inline("✅ کلاپس خودکار", f"auto_collapse_toggle:{chat_id}:{msg_id}".encode(), style="success")]
        ])
        await event.answer("✅ کلاپس خودکار فعال شد")

        async def auto_collapse():
            await asyncio.sleep(180)
            if msg_info.get("auto_collapse_disabled"):
                return
            try:
                full = msg_info.get("full_text", "")
                lines = full.split('\n')
                collapsed = '\n'.join(lines[:3]) + ('\n...' if len(lines) > 3 else '')
                await self.bot.edit_message(chat_id, msg_id, collapsed, buttons=[
                    [Button.inline("🔽 Expand", f"expand:{chat_id}:{msg_id}".encode(), style="primary")]
                ])
            except Exception:
                pass

        task = asyncio.create_task(auto_collapse())
        msg_info["collapse_task"] = task

"""AI Support Assistant handlers.

The Support Assistant is a dedicated conversation mode (mode="support",
mentor_key="support") that helps confused users understand and use the
bot's features. It intentionally reuses the existing mentor pipeline
(window management, rate limiting, block checks, animator) instead of
introducing a parallel infrastructure:

- `support_entry_cb` / `support_cmd`: Entry point from the main menu button
  or the `/support` command. Sets pending state exactly like mentor
  selection in `menu.py` (`mentor_key="support"`,
  `pending_role=SUPPORT_SYSTEM_PROMPT`), so any free-text message the user
  sends afterward is picked up by the existing generic mentor branch in
  `ai.py`'s `pending_message_handler` (see the `_conv_mode` special-case
  there). Shows a random sample of suggested questions (see
  `_suggested_question_buttons`).
- `support_suggested_cb`: Handles taps on the suggested-question quick
  buttons shown on entry. Callback events aren't `NewMessage` events, so
  this can't flow through `pending_message_handler` — it runs its own
  compact processing pipeline, mirroring `_process_reply_ask` in `ai.py`.
- `support_continue_cb`: The support-mode equivalent of `again_talk_mentor`
  in `ai.py` — the "Tap For Talk" button that re-arms `pending_question`
  so the user can send another message in the SAME conversation (window
  history already persists across turns; this only re-arms the UI gate
  that `pending_message_handler` checks).
- `support_exit_cb`: Leaves support mode and returns to the main menu.
- `build_support_turn_buttons` / `SUPPORT_MAX_TURNS`: Shared turn-cap
  enforcement used by both the free-text path (`ai.py`) and
  `support_suggested_cb`. Once a conversation hits `SUPPORT_MAX_TURNS`
  user messages, the window history is wiped (`_close_support_window`) so
  the next `/support` entry starts a brand-new conversation instead of
  immediately re-hitting the cap.

All handlers receive `self: TelegramBot` as the first argument (bound via
descriptor protocol in `TelegramBot._bind_handlers`).
"""

import asyncio
import logging
import random
from typing import Any, TYPE_CHECKING

# v2: Button facade + v2 error class come from the compat layer (matching the
# rest of the telegram/ handlers), so this module runs unchanged on Telethon v2.
# See telethon_compat.py and MIGRATION_NOTES.md.
from telethon_compat import Button, MessageNotModifiedError

if TYPE_CHECKING:
    from ..bot import TelegramBot

from support_knowledge import (
    SUPPORT_SYSTEM_PROMPT,
    SUPPORT_WELCOME_TEXT,
    SUPPORT_SUGGESTED_QUESTIONS,
)
from ..animator import StatusAnimator
from .ai import _ensure_rtl

tlogger = logging.getLogger("telegram")

# Maximum user messages allowed in a single support conversation. Once hit,
# the window's history is wiped so the next /support entry starts fresh —
# keeps the support window light and forces a natural session boundary.
SUPPORT_MAX_TURNS = 8

# How many suggested questions to show at once (randomly sampled from the
# full pool in support_knowledge.py so repeat visits feel less repetitive).
SUPPORT_SUGGESTED_DISPLAY_COUNT = 4


def _suggested_question_buttons() -> list:
    """Build the inline keyboard of suggested questions shown on entry.

    Randomly samples SUPPORT_SUGGESTED_DISPLAY_COUNT questions out of the
    full pool on every call. The callback data encodes the index into the
    FULL pool (not the sampled subset), since callback data is the only
    state carried across the button tap.
    """
    pool_size = len(SUPPORT_SUGGESTED_QUESTIONS)
    k = min(SUPPORT_SUGGESTED_DISPLAY_COUNT, pool_size)
    chosen_indices = random.sample(range(pool_size), k)
    rows = [
        [Button.inline(f"❓ {SUPPORT_SUGGESTED_QUESTIONS[i][0]}", f"support_suggested:{i}".encode())]
        for i in chosen_indices
    ]
    rows.append([Button.inline("🔙 خروج از پشتیبانی", b"support_exit", style="danger")])
    return rows


def _count_user_turns(ai_service: Any) -> int:
    """Count how many user messages exist in this AI_Service's history."""
    return sum(1 for m in getattr(ai_service, "history", []) if m.get("role") == "user")


def _close_support_window(self: "TelegramBot", uid: int, ai_service: Any) -> None:
    """Wipe the support window's history so the next /support starts clean.

    Mirrors `clear_win_confirm_cb` in windows.py: clears history/metrics in
    the DB and drops the in-memory AI_Service so the next request reloads
    fresh from the DB instead of reusing stale in-memory history.

    IMPORTANT — background summarization race (api_http.py's
    AI_Service._background_summarize): once a conversation crosses 5 user
    turns / 15k tokens, handle_message() fires a background
    asyncio.create_task() that summarizes history in the background and,
    when done, writes it straight to this same window_id via
    db_manager.save_window_session(). That task guards against races by
    comparing `self._history_version` before/after — but only catches
    changes made through the SAME AI_Service instance. Our direct
    `self.db.save_window_session(window_id, [], ...)` call bypasses that
    instance entirely, so a summarization task started a few turns ago
    could still be in flight and would overwrite our clear with its own
    (stale) summary once it finishes — silently resurrecting "closed"
    history. Bumping `_history_version` on the SAME ai_service object
    here makes that guard correctly detect the change and skip its DB
    write, exactly like it already does for concurrent handle_message()
    calls.
    """
    window_id = getattr(ai_service, "window_id", None)
    if window_id:
        self.db.save_window_session(window_id, [], 0, 0, 0)
    ai_service.history = []
    ai_service._history_version = getattr(ai_service, "_history_version", 0) + 1
    self.sessions.pop(uid, None)
    self.db.delete_pending_state(uid)
    self.pending_message.pop((uid, "ask_ai"), None)


def build_support_turn_buttons(self: "TelegramBot", uid: int, ai_service: Any, tokens_used: int):
    """Build response buttons for one support turn and enforce SUPPORT_MAX_TURNS.

    Returns (buttons, closing_note_html). `closing_note_html` is empty
    unless the cap was just reached, in which case it should be appended
    to the AI's answer before sending — and the window has already been
    cleared as a side effect, so no "continue" button is offered.
    """
    count = _count_user_turns(ai_service)
    if count >= SUPPORT_MAX_TURNS:
        _close_support_window(self, uid, ai_service)
        buttons = [
            [Button.inline(f"🛟 {count}/{SUPPORT_MAX_TURNS} — مکالمه بسته شد", b"place_holder")],
            [Button.inline("🔙 بازگشت به منو", b"back_to_main")],
        ]
        closing_note = (
            f"\n\n🛟 <i>به سقف {SUPPORT_MAX_TURNS} پیام این مکالمه رسیدیم — می‌بندمش که سبک بمونه. "
            f"هروقت خواستی، دوباره بزن رو «پشتیبان هوشمند» و از اول شروع کن 😉</i>"
        )
        return buttons, closing_note

    buttons = [
        [Button.inline(f"✅ ادامه بده | پیام {count}/{SUPPORT_MAX_TURNS} | 🪙 {tokens_used:,}", f"Asupport_{uid}".encode(), style="success")],
        [Button.inline("🔙 خروج از پشتیبانی", b"support_exit", style="danger")],
    ]
    return buttons, ""


async def _enter_support(self: "TelegramBot", event: Any, use_edit: bool) -> None:
    """Shared entry guards + pending-state setup for Support Assistant mode.

    `use_edit` selects between editing the tapped button's message
    (callback entry, e.g. the main-menu button) and replying with a new
    message (command entry via `/support`, no existing message to edit) —
    mirrors the split between `menu.py`'s button-triggered mentor flow and
    `shortcuts.py`'s `_enter_mentor` command-triggered flow.
    """
    uid = event.sender_id

    if self.db.get_setting("support_assistant_enabled", "1") != "1":
        msg = "🛟 دستیار پشتیبان موقتاً غیرفعاله. لطفا از بخش «راهنما» استفاده کن یا بعداً دوباره امتحان کن."
        if use_edit:
            await event.answer(msg, alert=True)
        else:
            await event.reply(msg)
        return

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        if use_edit:
            await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        else:
            await event.reply("⛔️ شما مسدود شده‌اید.")
        return

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        await self.send_limit_notification(event, limit_msg, is_callback=use_edit)
        return

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        if use_edit:
            await event.answer("یک مکالمه باز دارید.", alert=True)
        await event.respond(
            "یک مکالمه باز دارید.",
            buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
        )
        return

    if use_edit:
        await event.answer()

    self.db.save_pending_state(
        uid, pending_question=True,
        pending_role=SUPPORT_SYSTEM_PROMPT,
        pending_mentor_key="support",
    )

    welcome_text = _ensure_rtl(SUPPORT_WELCOME_TEXT)

    if use_edit:
        msg = await event.edit(welcome_text, buttons=_suggested_question_buttons(), parse_mode="html")
    else:
        msg = await event.reply(welcome_text, buttons=_suggested_question_buttons(), parse_mode="html")
    self.pending_message[(uid, "ask_ai")] = msg


async def support_entry_cb(self: "TelegramBot", event: Any) -> None:
    """Enter Support Assistant mode via the main-menu button tap."""
    await _enter_support(self, event, use_edit=True)


async def support_cmd(self: "TelegramBot", event: Any) -> None:
    """Enter Support Assistant mode via the `/support` command."""
    await _enter_support(self, event, use_edit=False)


async def support_continue_cb(self: "TelegramBot", event: Any) -> None:
    """Re-arm support mode for another message ("Tap For Talk" equivalent).

    Mirrors `again_talk_mentor` in ai.py: validates button ownership, guards
    against concurrent processing, then flips pending_question back to True
    so the user's next free-text message is routed through the generic
    mentor branch in `pending_message_handler` (see the `_conv_mode`
    special-case there). Unlike `again_talk_mentor`, there's no mentor-idle
    re-verify step and no mentor_prompts lookup — the role is always the
    fixed SUPPORT_SYSTEM_PROMPT.

    Callback data format: "Asupport_{owner_id}".
    """
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return

    chat_id = getattr(event, "chat_id", None)
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        await event.answer()
        return

    if self.db.get_setting("support_assistant_enabled", "1") != "1":
        await event.answer("🛟 دستیار پشتیبان موقتاً غیرفعاله.", alert=True)
        return

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    try:
        owner_id = int(event.data.decode().replace("Asupport_", "").strip())
    except ValueError:
        await event.answer()
        return

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
        await event.answer(f"❌ شما به سقف مجاز مصرف خود رسیده‌اید!\n⏳ {time_left} تا ریست", alert=True)
        return

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        pending_msg_entry = self.pending_message.get((uid, "ask_ai"))
        current_msg = await event.get_message()
        if pending_msg_entry and current_msg and current_msg.id == pending_msg_entry.id:
            # Same "toggle to cancel" behavior as again_talk_mentor: tapping
            # the confirm button again cancels the pending wait state.
            self.db.delete_pending_state(uid)
            self.pending_message.pop((uid, "ask_ai"), None)
            from .ai import _restore_previous_ai_buttons
            await _restore_previous_ai_buttons(self, uid)
            self.previous_ai_messages.pop(uid, None)
            try:
                await current_msg.edit(buttons=[
                    [Button.inline("🛟 ادامه بده", f"Asupport_{uid}".encode(), style="success")],
                    [Button.inline("🔙 خروج از پشتیبانی", b"support_exit", style="danger")],
                ])
            except MessageNotModifiedError:
                pass
            await event.answer("✅ لغو شد")
            return
        else:
            await event.answer("یک مکالمه باز دارید.", alert=True)
            await event.respond(
                "یک مکالمه باز دارید.",
                buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
            )
            return

    await event.answer()

    from .ai import _disable_previous_ai_buttons
    await _disable_previous_ai_buttons(self, uid)

    self.pending_message.pop((uid, "ask_ai"), None)
    self.db.save_pending_state(
        uid, pending_question=True,
        pending_role=SUPPORT_SYSTEM_PROMPT,
        pending_mentor_key="support",
    )

    msg = await event.get_message()
    try:
        await msg.edit(buttons=[[Button.inline("✅ پیامت رو بفرست", f"Asupport_{uid}".encode(), style="danger")]])
    except MessageNotModifiedError:
        pass
    self.pending_message[(uid, "ask_ai")] = msg


async def support_exit_cb(self: "TelegramBot", event: Any) -> None:
    """Leave Support Assistant mode and return to the main menu."""
    uid = event.sender_id
    self.db.delete_pending_state(uid)
    self.pending_message.pop((uid, "ask_ai"), None)
    from .menu import back_to_main_cb
    await back_to_main_cb(self, event)


async def support_suggested_cb(self: "TelegramBot", event: Any) -> None:
    """Handle a tap on one of the suggested-question quick buttons.

    Runs a self-contained processing pipeline (guards → AI call → send
    response) instead of routing through `pending_message_handler`,
    since a CallbackQuery event carries no free-text message to be
    picked up by that catch-all. Mirrors `_process_reply_ask` in `ai.py`.
    """
    from .ai import _send_response, _restore_previous_ai_buttons, ToolEvent

    uid = event.sender_id
    chat_id = getattr(event, "chat_id", None)

    try:
        idx = int(event.data.decode().replace("support_suggested:", "").strip())
        _label, question_text = SUPPORT_SUGGESTED_QUESTIONS[idx]
    except (ValueError, IndexError):
        await event.answer("این سوال دیگه در دسترس نیست.", alert=True)
        return

    if uid in self.processing_users:
        await event.answer("یک درخواست دیگه در حال پردازشه.", alert=True)
        return
    self.processing_users.add(uid)
    await event.answer()

    try:
        if self.db.get_setting("support_assistant_enabled", "1") != "1":
            await event.respond("🛟 دستیار پشتیبان موقتاً غیرفعاله.")
            return

        if uid not in self.admin_ids and self.check_user_blocked(uid):
            await event.respond("⛔️ شما توسط ادمین از دسترسی به ربات مسدود شده‌اید.")
            return

        if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
            return

        is_limited, limit_msg = self.check_user_limits(uid)
        if is_limited:
            await self.send_limit_notification(event, limit_msg, is_callback=True)
            return

        is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
        if is_80 and alert_msg:
            await event.respond(alert_msg)

        # Edit the tapped message in place (welcome banner → placeholder →
        # final answer) instead of sending a separate new message, so the
        # suggested-question flow doesn't leave the welcome banner behind.
        msg = await event.edit(
            _ensure_rtl(f"🛟 {_label}\n\nدر حال آماده‌سازی پاسخ..."),
            buttons=[Button.inline("لغو درخواست", b"clear_processing", style="danger")],
            parse_mode="html",
        )

        animator = None
        try:
            animator = StatusAnimator(msg, mentor_key="support", skill_key="default")
            await animator.start()

            async def on_status_change(status_text: str):
                if animator:
                    animator.update_step(status_text)

            async def on_tool_call(tool_event: "ToolEvent"):
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

            ai_service = self.get_ai_service(uid, mode="support", mentor_key="support", chat_id=chat_id)
            answer, tokens_used = await ai_service.handle_message(
                user_message=question_text,
                role=SUPPORT_SYSTEM_PROMPT,
                mode="support",
                on_status_change=on_status_change,
                on_generation_start=on_generation_start,
                on_tool_call=on_tool_call,
            )

            # CRITICAL: stop the animator BEFORE building/sending the final
            # response. StatusAnimator runs its own background edit loop on
            # `msg`; leaving it running while _send_response also edits `msg`
            # is a race — the animator's next tick overwrites the final
            # answer back to a "waiting" placeholder, making the response
            # look like it never arrived. Mirrors the ordering already used
            # in ai.py's pending_message_handler/retry_failed_handler.
            if animator:
                await animator.stop()
                animator = None

            buttons, closing_note = build_support_turn_buttons(self, uid, ai_service, tokens_used)
            answer = f"{answer}{closing_note}"

            # Match the mentor turn lifecycle: pending_question is cleared
            # after every turn, and re-armed only via the "ادامه بده" button
            # (see support_continue_cb). build_support_turn_buttons already
            # clears it when the turn cap was just hit; this covers the
            # normal case too (idempotent if already cleared).
            self.db.delete_pending_state(uid)
            self.pending_message.pop((uid, "ask_ai"), None)

            await _send_response(msg, event, ai_service, answer, buttons, uid)

        except asyncio.CancelledError:
            tlogger.info(f"Support suggested-question request for user {uid} was cancelled.")
            await _restore_previous_ai_buttons(self, uid)
            raise
        except Exception as e:
            tlogger.error(f"Support suggested-question failed for {uid}: {e}")
            await _restore_previous_ai_buttons(self, uid)
            if animator:
                try:
                    await animator.stop()
                    animator = None
                except Exception:
                    pass
            try:
                await msg.edit("پردازش درخواست با خطا مواجه شد. لطفا دوباره تلاش کنید.")
            except Exception:
                pass
        finally:
            if animator:
                try:
                    await animator.stop()
                except Exception:
                    pass
            self.active_requests.pop(uid, None)
    finally:
        self.processing_users.discard(uid)

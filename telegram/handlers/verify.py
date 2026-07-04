"""Second-verify layer for stale conversation windows.

When a user returns to an AI conversation after a significant idle period
(30 minutes for quick-ask, 30 minutes for mentors), this module intercepts the
entry flow and presents a verification message reminding the user of their
active window context.  The user may continue, switch windows, start a new
conversation, or delete windows — all from the verify panel.

Architecture
------------
Each entry point (shortcuts, menu handler, Tap-for-Talk button) calls
``_should_verify()`` BEFORE sending the normal "waiting for input" message.
If verification is needed, a verify message is sent/edited instead of the
normal flow message.  Callback handlers in this module process the user's
choice and then resume the original flow by editing the verify message
into the normal entry message and setting the appropriate pending state.

Pool isolation
--------------
- Quick-ask windows share one 3-hour pool (the active quick-ask window's
  ``last_interaction_time`` is checked).
- Each mentor has its own independent 5-hour pool (the mentor's dedicated
  window ``last_interaction_time`` is checked).
"""

import json
from datetime import datetime, timedelta
from typing import Any, Optional, List, Dict, TYPE_CHECKING
from telethon import Button

if TYPE_CHECKING:
    from ..bot import TelegramBot

from ..constants import QUICK_ASK_VERIFY_SECONDS, MENTOR_VERIFY_SECONDS, VERIFY_MAX_WORDS_PREVIEW

try:
    from zoneinfo import ZoneInfo
    tz_tehran = ZoneInfo("Asia/Tehran")
except ImportError:
    from datetime import timezone
    tz_tehran = timezone(timedelta(hours=3, minutes=30))


# ── Public API ──────────────────────────────────────────────────────────


def should_verify(win: Optional[Dict[str, Any]], mode: str, mentor_key: Optional[str] = None) -> bool:
    """Check if a conversation window needs second-verify.

    Args:
        win: The conversation window dict (may be None if no window exists).
        mode: ``"quick_ask"`` or ``"mentor"``.
        mentor_key: Mentor identifier (required if mode is ``"mentor"``).

    Returns:
        ``True`` if the verify message should be shown.
    """
    if not win:
        return False

    history_str = win.get("history", "[]")
    try:
        history = json.loads(history_str) if isinstance(history_str, str) else history_str
    except (json.JSONDecodeError, TypeError):
        history = []

    # No history → nothing to verify, proceed directly
    if not history:
        return False

    last_time_str = win.get("last_interaction_time")
    if not last_time_str:
        return False

    try:
        last_time = datetime.fromisoformat(last_time_str)
    except (ValueError, TypeError):
        return False

    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=tz_tehran)

    threshold = MENTOR_VERIFY_SECONDS if mode == "mentor" else QUICK_ASK_VERIFY_SECONDS
    elapsed = (datetime.now(tz_tehran) - last_time).total_seconds()

    return elapsed >= threshold


def format_time_ago(timestamp_str: str) -> str:
    """Format an ISO-8601 timestamp as a human-readable Persian relative time.

    Args:
        timestamp_str: ISO-8601 datetime string (with or without timezone).

    Returns:
        Persian relative time string like ``"۳ ساعت پیش"``, ``"دیروز"``, etc.
    """
    if not timestamp_str:
        return ""

    try:
        then = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return ""

    if then.tzinfo is None:
        then = then.replace(tzinfo=tz_tehran)

    now = datetime.now(tz_tehran)
    diff = now - then
    total_seconds = int(diff.total_seconds())

    if total_seconds < 60:
        return "چند لحظه پیش"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} دقیقه پیش"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if minutes == 0:
            return f"{hours} ساعت پیش"
        return f"{hours} ساعت و {minutes} دقیقه پیش"
    elif total_seconds < 172800:
        return "دیروز"
    elif total_seconds < 259200:
        return "۲ روز پیش"
    else:
        days = total_seconds // 86400
        return f"{days} روز پیش"


def _get_last_user_message_preview(win: Dict[str, Any], max_words: int = VERIFY_MAX_WORDS_PREVIEW) -> str:
    """Get a short preview of the last user message in a conversation window.

    First tries ``last_user_message`` cached column; falls back to parsing
    the JSON history.  Returns the first ``max_words`` words.

    Args:
        win: Window dict.
        max_words: Maximum number of words to show.

    Returns:
        Truncated message preview string, or empty string if none found.
    """
    cached = win.get("last_user_message")
    if cached:
        words = cached.split()
        preview = " ".join(words[:max_words])
        if len(words) > max_words:
            preview += "..."
        return preview

    # Fallback: parse history
    history_str = win.get("history", "[]")
    try:
        history = json.loads(history_str) if isinstance(history_str, str) else history_str
    except (json.JSONDecodeError, TypeError):
        return ""

    for msg in reversed(history):
        if msg.get("role") == "user":
            parts = msg.get("parts", [])
            for part in reversed(parts):
                if "text" in part and part["text"].strip():
                    text = part["text"].strip()
                    words = text.split()
                    preview = " ".join(words[:max_words])
                    if len(words) > max_words:
                        preview += "..."
                    return preview
    return ""


# ── Verify Message Builders ────────────────────────────────────────────


async def send_verify_quick_ask(
    self: "TelegramBot",
    event: Any,
    uid: int,
    skill_key: str,
    entry_type: str = "entry",
    original_msg_id: Optional[int] = None
) -> None:
    """Send or edit a verify message for quick-ask mode.

    Args:
        self: TelegramBot instance.
        event: The callback/command event.
        uid: User ID.
        skill_key: The active skill key (e.g. ``"default"``, ``"learn"``).
        entry_type: ``"entry"`` for fresh entry, ``"tap"`` for Tap-for-Talk.
        original_msg_id: The message ID to edit (for Tap-for-Talk path).
    """
    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if active_win["mode"] != "quick_ask":
        windows = self.db.get_user_windows(uid)
        qa_win = next((w for w in windows if w["mode"] == "quick_ask"), None)
        if qa_win:
            self.db.set_active_window(uid, qa_win["window_id"])
            active_win = qa_win
        else:
            # No QA window exists — create one and skip verify
            self.db.create_window(uid, "سوال سریع", mode="quick_ask")
            active_win = self.db.get_active_window(uid, default_mode="quick_ask")
            _resume_normal_quick_ask(self, event, uid, skill_key, entry_type, original_msg_id)
            return

    all_qa_windows = self.db.get_quick_ask_windows(uid)
    last_msg_preview = _get_last_user_message_preview(active_win)
    time_ago = format_time_ago(active_win.get("last_interaction_time", ""))

    text = (
        f"🔔 <b>میخوای ادامه مکالمه قبلیت رو بری؟</b>\n\n"
        f"<b>{active_win['title']}</b>\n"
        f"💬 {last_msg_preview}\n"
        f"🕒 {time_ago}"
    )

    buttons = [
        [Button.inline("✅ ادامه", f"verify_qa_continue:{skill_key}".encode(), style="success")]
    ]

    for win in all_qa_windows:
        if win["window_id"] == active_win["window_id"]:
            continue
        title_display = win["title"]
        buttons.append([
            Button.inline(f"📂 {title_display}", f"verify_qa_switch:{win['window_id']}:{skill_key}".encode(), style="primary"),
            Button.inline("🗑 حذف", f"verify_qa_delete_req:{win['window_id']}:{skill_key}".encode(), style="danger")
        ])

    if len(all_qa_windows) < 5:
        buttons.append([Button.inline("➕ مکالمه جدید", f"verify_qa_new:{skill_key}".encode(), style="success")])

    buttons.append([Button.inline("🔙 انصراف", b"back_to_main")])

    # Save pending verify state so callbacks know the context
    verify_data = json.dumps({"type": "qa", "skill": skill_key, "entry_type": entry_type})
    self.db.save_pending_state(uid, pending_question=False, pending_action=verify_data)

    if entry_type == "tap" and original_msg_id:
        try:
            msg = await event.get_message()
            if msg:
                await msg.edit(text, buttons=buttons, parse_mode="html")
                self.pending_message[(uid, "verify_msg")] = msg
                return
        except Exception:
            pass

    try:
        msg = await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        msg = await event.respond(text, buttons=buttons, parse_mode="html")

    if msg:
        self.pending_message[(uid, "verify_msg")] = msg


async def send_verify_mentor(
    self: "TelegramBot",
    event: Any,
    uid: int,
    mentor_key: str,
    entry_type: str = "entry",
    original_msg_id: Optional[int] = None
) -> None:
    """Send or edit a verify message for mentor mode.

    Args:
        self: TelegramBot instance.
        event: The callback/command event.
        uid: User ID.
        mentor_key: The mentor key (e.g. ``"micheal"``).
        entry_type: ``"entry"`` or ``"tap"``.
        original_msg_id: Message ID to edit (for Tap-for-Talk path).
    """
    mentor_win = self.db.get_mentor_window(uid, mentor_key)
    if not mentor_win:
        # No mentor window yet — create one and skip verify
        title = f"منتور {mentor_key}"
        self.db.create_window(uid, title, mode="mentor", mentor_key=mentor_key)
        _resume_normal_mentor(self, event, uid, mentor_key, entry_type, original_msg_id)
        return

    last_msg_preview = _get_last_user_message_preview(mentor_win)
    time_ago = format_time_ago(mentor_win.get("last_interaction_time", ""))

    total_requests = mentor_win.get("total_requests", 0)
    total_tokens = mentor_win.get("total_input_tokens", 0) + mentor_win.get("total_output_tokens", 0)

    text = (
        f"🧠 <b>منتور {mentor_key}</b>\n"
        f"💬 {last_msg_preview}\n"
        f"📊 {total_requests} پیام | 🪙 {total_tokens:,} توکن\n"
        f"🕒 {time_ago}"
    )

    buttons = [
        [Button.inline("✅ ادامه", f"verify_mentor_continue:{mentor_key}".encode(), style="success")],
        [Button.inline("🔄 ریست مکالمه", f"verify_mentor_reset:{mentor_key}".encode(), style="danger")]
    ]

    verify_data = json.dumps({"type": "mentor", "mentor_key": mentor_key, "entry_type": entry_type})
    self.db.save_pending_state(uid, pending_question=False, pending_action=verify_data)

    if entry_type == "tap" and original_msg_id:
        try:
            msg = await event.get_message()
            if msg:
                await msg.edit(text, buttons=buttons, parse_mode="html")
                self.pending_message[(uid, "verify_msg")] = msg
                return
        except Exception:
            pass

    try:
        msg = await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        msg = await event.respond(text, buttons=buttons, parse_mode="html")

    if msg:
        self.pending_message[(uid, "verify_msg")] = msg


# ── Resume Helpers ──────────────────────────────────────────────────────


async def _resume_normal_quick_ask(
    self: "TelegramBot",
    event: Any,
    uid: int,
    skill_key: str,
    entry_type: str,
    original_msg_id: Optional[int] = None
) -> None:
    """Continue the normal quick-ask entry flow after verify is resolved.

    For ``entry_type="entry"``: edits the verify message into the standard
    "enter your message" prompt with skill info and sets pending_question.

    For ``entry_type="skill_select"``: shows the skill selection keyboard so
    the user can pick a skill before typing.
    """
    from skills import SKILLS, get_skill

    if entry_type == "skill_select":
        # Show skill selection keyboard (same as quickask_cb normal flow)
        self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{skill_key}")

        buttons = []
        row = []
        for s_key, s_data in SKILLS.items():
            if s_key == "default":
                continue
            is_active = (s_key == skill_key)
            btn_text = f"{'✅ ' if is_active else ''}{s_data['icon']} {s_data['name']}"
            btn_data = f"skill_toggle:{s_key}"
            style = "success" if is_active else "primary"
            row.append(Button.inline(btn_text, btn_data.encode(), style=style))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([Button.inline("🔙 انصراف", b"back_to_main")])

        text = "✍️ <b>پیامت رو بفرست:</b>\n"
        text += "<blockquote><code> دکمه skill را زمانی فعال کنید که سوالتان به آن مربوط است.</code></blockquote>"

        try:
            msg = await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            msg = await event.respond(text, buttons=buttons, parse_mode="html")

        if msg:
            self.pending_message[(uid, "menu_prompt")] = msg
        return

    skill_data = get_skill(skill_key)
    skill_label = f" ({skill_data['name']})" if skill_key != "default" else ""

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{skill_key}")

    buttons = [[Button.inline("🔙 انصراف", b"back_to_main")]]

    text = (
        f"✍️ <b>پیامت رو بفرست{skill_label}:</b>\n\n"
        f"{skill_data['icon']} حالت فعال: <b>{skill_data['name']}</b>"
    )

    try:
        msg = await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        msg = await event.respond(text, buttons=buttons, parse_mode="html")

    if msg:
        self.pending_message[(uid, "menu_prompt")] = msg


async def _resume_normal_mentor(
    self: "TelegramBot",
    event: Any,
    uid: int,
    mentor_key: str,
    entry_type: str,
    original_msg_id: Optional[int] = None
) -> None:
    """Continue the normal mentor entry flow after verify is resolved."""
    role_prompt = self.mentor_prompts.get(mentor_key)
    if not role_prompt:
        await event.answer("منتور یافت نشد.", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=True, pending_role=role_prompt, pending_mentor_key=mentor_key)

    text = f"🎓 منتور <b>{mentor_key}</b> فعال شد.\nپیامت رو ارسال کن."
    buttons = [[Button.inline("🔙 انصراف", b"back_to_main")]]

    try:
        msg = await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        msg = await event.respond(text, buttons=buttons, parse_mode="html")

    if msg:
        self.pending_message[(uid, "menu_prompt")] = msg


async def _resume_tap_quick_ask(
    self: "TelegramBot",
    event: Any,
    uid: int,
    skill_key: str,
    entry_type: str,
    original_msg_id: Optional[int] = None
) -> None:
    """Resume the Tap-for-Talk quick-ask flow after verify."""
    self.pending_message.pop((uid, "ask_ai"), None)

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{skill_key}")

    await _disable_previous_ai_buttons(self, uid)

    msg = await event.get_message()
    if msg:
        try:
            await msg.edit(buttons=[
                [
                    Button.inline("✅ پیامت رو بفرست", f"Aquickask_{uid}_{skill_key}".encode(), style="danger"),
                    Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                ]
            ])
        except Exception:
            pass
        self.pending_message[(uid, "ask_ai")] = msg


async def _resume_tap_mentor(
    self: "TelegramBot",
    event: Any,
    uid: int,
    mentor_key: str,
    entry_type: str,
    original_msg_id: Optional[int] = None
) -> None:
    """Resume the Tap-for-Talk mentor flow after verify."""
    role_prompt = self.mentor_prompts.get(mentor_key)
    if not role_prompt:
        return

    self.pending_message.pop((uid, "ask_ai"), None)

    self.db.save_pending_state(uid, pending_question=True, pending_role=role_prompt, pending_mentor_key=mentor_key)

    await _disable_previous_ai_buttons(self, uid)

    msg = await event.get_message()
    if msg:
        try:
            await msg.edit(buttons=[
                [
                    Button.inline("✅ پیامت رو بفرست", f"Amentors_{mentor_key}_{uid}".encode(), style="danger"),
                    Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
                ]
            ])
        except Exception:
            pass
        self.pending_message[(uid, "ask_ai")] = msg


# ── Helper: disable previous AI buttons ────────────────────────────────


async def _disable_previous_ai_buttons(self: "TelegramBot", uid: int) -> None:
    """Disable interactive buttons on all previous AI messages for a user.

    Called when entering waiting state — only the info row is kept so
    stale Tap-for-Talk buttons cannot be clicked.
    """
    prev_msgs = self.previous_ai_messages.get(uid, [])
    for msg, original_buttons in prev_msgs:
        try:
            info_only = [original_buttons[-1]] if original_buttons else []
            await msg.edit(buttons=info_only)
        except Exception:
            pass


# ── Verify Callback Handlers ───────────────────────────────────────────

# Quick-ask callbacks


async def verify_qa_continue_cb(self: "TelegramBot", event: Any) -> None:
    """User clicked "continue" in quick-ask verify panel.

    Parses the pending verify state for skill_key and resumes the
    normal flow in the currently active window.
    """
    uid = event.sender_id
    data = event.data.decode()
    skill_key = data.replace("verify_qa_continue:", "").strip()

    state = self.db.get_pending_state(uid)
    try:
        verify_data = json.loads(state.get("pending_action", "{}"))
    except (json.JSONDecodeError, TypeError):
        verify_data = {}

    entry_type = verify_data.get("entry_type", "entry")

    _resume_map = {
        "entry": _resume_normal_quick_ask,
        "tap": _resume_tap_quick_ask,
        "skill_select": _resume_normal_quick_ask,
    }
    handler = _resume_map.get(entry_type, _resume_normal_quick_ask)
    await handler(self, event, uid, skill_key, entry_type)

    self.pending_message.pop((uid, "verify_msg"), None)
    await event.answer("✅ ادامه می‌دهیم...", alert=False)


async def verify_qa_switch_cb(self: "TelegramBot", event: Any) -> None:
    """User selected a different quick-ask window from the verify panel.

    Switches active window and resumes the normal flow.
    """
    uid = event.sender_id
    data = event.data.decode()
    # Format: verify_qa_switch:{win_id}:{skill_key}
    parts = data.replace("verify_qa_switch:", "").split(":")
    if len(parts) < 2:
        await event.answer("خطا: داده نامعتبر", alert=True)
        return

    try:
        win_id = int(parts[0])
    except ValueError:
        await event.answer("خطا: پنجره نامعتبر", alert=True)
        return

    skill_key = parts[1]

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ دسترسی غیرمجاز!", alert=True)
        return

    self.db.set_active_window(uid, win_id)

    state = self.db.get_pending_state(uid)
    try:
        verify_data = json.loads(state.get("pending_action", "{}"))
    except (json.JSONDecodeError, TypeError):
        verify_data = {}

    entry_type = verify_data.get("entry_type", "entry")

    _resume_map = {
        "entry": _resume_normal_quick_ask,
        "tap": _resume_tap_quick_ask,
        "skill_select": _resume_normal_quick_ask,
    }
    handler = _resume_map.get(entry_type, _resume_normal_quick_ask)
    await handler(self, event, uid, skill_key, entry_type)

    self.pending_message.pop((uid, "verify_msg"), None)
    await event.answer(f"پنجره تغییر یافت.", alert=True)


async def verify_qa_new_cb(self: "TelegramBot", event: Any) -> None:
    """User clicked "new conversation" in quick-ask verify panel.

    Creates a new quick_ask window with date-time title, activates it,
    and resumes the normal flow.
    """
    uid = event.sender_id
    data = event.data.decode()
    skill_key = data.replace("verify_qa_new:", "").strip()

    windows = self.db.get_user_windows(uid)
    if len(windows) >= 5:
        await event.answer("❌ شما به حداکثر تعداد پنجره‌ها (۵) رسیده‌اید.", alert=True)
        return

    now = datetime.now(tz_tehran)
    title = now.strftime("%b-%d | %H:%M").upper()
    success = self.db.create_window(uid, title, mode="quick_ask")
    if not success:
        await event.answer("❌ خطا در ساخت پنجره جدید.", alert=True)
        return

    # Activate the new window
    windows = self.db.get_user_windows(uid)
    new_win = next((w for w in windows if w["title"] == title), None)
    if new_win:
        self.db.set_active_window(uid, new_win["window_id"])

    state = self.db.get_pending_state(uid)
    try:
        verify_data = json.loads(state.get("pending_action", "{}"))
    except (json.JSONDecodeError, TypeError):
        verify_data = {}

    entry_type = verify_data.get("entry_type", "entry")

    _resume_map = {
        "entry": _resume_normal_quick_ask,
        "tap": _resume_tap_quick_ask,
        "skill_select": _resume_normal_quick_ask,
    }
    handler = _resume_map.get(entry_type, _resume_normal_quick_ask)
    await handler(self, event, uid, skill_key, entry_type)

    self.pending_message.pop((uid, "verify_msg"), None)
    await event.answer(f"✅ پنجره جدید «{title}» ساخته شد.", alert=True)


async def verify_qa_delete_req_cb(self: "TelegramBot", event: Any) -> None:
    """User clicked delete on a window in the verify panel.

    Shows confirmation dialog; on confirm, deletes and returns to verify.
    """
    uid = event.sender_id
    data = event.data.decode()
    # Format: verify_qa_delete_req:{win_id}:{skill_key}
    parts = data.replace("verify_qa_delete_req:", "").split(":")
    if len(parts) < 2:
        await event.answer("خطا: داده نامعتبر", alert=True)
        return

    try:
        win_id = int(parts[0])
    except ValueError:
        await event.answer("خطا: پنجره نامعتبر", alert=True)
        return

    skill_key = parts[1]

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ دسترسی غیرمجاز!", alert=True)
        return

    windows = self.db.get_user_windows(uid)
    target = next((w for w in windows if w["window_id"] == win_id), None)
    win_title = target["title"] if target else f"#{win_id}"

    await event.edit(
        f"⚠️ <b>آیا از حذف پنجره «{win_title}» مطمئن هستید؟</b>\nتمام تاریخچه این پنجره برای همیشه پاک می‌شود.",
        buttons=[
            [Button.inline("✅ بله، حذف شود", f"verify_qa_delete_confirm:{win_id}:{skill_key}".encode(), style="danger")],
            [Button.inline("❌ خیر", f"verify_qa_cancel_delete:{skill_key}".encode(), style="primary")]
        ],
        parse_mode="html"
    )


async def verify_qa_delete_confirm_cb(self: "TelegramBot", event: Any) -> None:
    """Confirm deletion of a window from the verify panel."""
    uid = event.sender_id
    data = event.data.decode()
    parts = data.replace("verify_qa_delete_confirm:", "").split(":")
    if len(parts) < 2:
        await event.answer("خطا: داده نامعتبر", alert=True)
        return

    try:
        win_id = int(parts[0])
    except ValueError:
        await event.answer("خطا: پنجره نامعتبر", alert=True)
        return

    skill_key = parts[1]

    self.db.delete_window(uid, win_id)
    await event.answer("✅ پنجره حذف شد.", alert=True)

    # Return to verify panel with updated window list
    await send_verify_quick_ask(self, event, uid, skill_key, entry_type="entry")


async def verify_qa_cancel_delete_cb(self: "TelegramBot", event: Any) -> None:
    """User cancelled deletion from the confirm dialog."""
    uid = event.sender_id
    data = event.data.decode()
    skill_key = data.replace("verify_qa_cancel_delete:", "").strip()

    await send_verify_quick_ask(self, event, uid, skill_key, entry_type="entry")
    await event.answer()


# Mentor callbacks


async def verify_mentor_continue_cb(self: "TelegramBot", event: Any) -> None:
    """User clicked "continue" in mentor verify panel."""
    uid = event.sender_id
    data = event.data.decode()
    mentor_key = data.replace("verify_mentor_continue:", "").strip()

    state = self.db.get_pending_state(uid)
    try:
        verify_data = json.loads(state.get("pending_action", "{}"))
    except (json.JSONDecodeError, TypeError):
        verify_data = {}

    entry_type = verify_data.get("entry_type", "entry")

    _resume_map = {
        "entry": _resume_normal_mentor,
        "tap": _resume_tap_mentor,
    }
    handler = _resume_map.get(entry_type, _resume_normal_mentor)
    await handler(self, event, uid, mentor_key, entry_type)

    self.pending_message.pop((uid, "verify_msg"), None)
    await event.answer("✅ ادامه می‌دهیم...", alert=False)


async def verify_mentor_reset_cb(self: "TelegramBot", event: Any) -> None:
    """User clicked "reset conversation" in mentor verify panel.

    Clears the mentor window's history and metrics, then resumes
    the normal entry flow so the user starts fresh.
    """
    uid = event.sender_id
    data = event.data.decode()
    mentor_key = data.replace("verify_mentor_reset:", "").strip()

    mentor_win = self.db.get_mentor_window(uid, mentor_key)
    if mentor_win:
        # Clear history and metrics directly
        self.db.save_window_session(mentor_win["window_id"], [], 0, 0, 0)
        self.db.update_window_interaction(mentor_win["window_id"], [])

        if uid in self.sessions:
            active_win = self.db.get_active_window(uid)
            if active_win and active_win["window_id"] == mentor_win["window_id"]:
                del self.sessions[uid]

    await event.answer("✅ مکالمه ریست شد.", alert=True)

    # Resume normal mentor flow (will create fresh window if needed)
    await _resume_normal_mentor(self, event, uid, mentor_key, "entry", None)
    self.pending_message.pop((uid, "verify_msg"), None)

import random
from typing import Any, TYPE_CHECKING
from telethon import Button

from skills import SKILLS, get_skill

if TYPE_CHECKING:
    from ..bot import TelegramBot

from ..constants import TIPS_GENERAL
from .verify import should_verify, send_verify_quick_ask, send_verify_mentor


# ─── Window Commands ───────────────────────────────────────────────

async def windows_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return
    from .windows import _build_windows_panel
    text, buttons = _build_windows_panel(self, uid)
    await event.reply(text, buttons=buttons, parse_mode="html")


async def switch_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    windows = self.db.get_user_windows(uid)
    if len(windows) <= 1:
        await event.reply("📌 شما فقط یک پنجره دارید. برای ساخت پنجره جدید از /new استفاده کنید.")
        return

    from ..utils import _DIVIDER

    text = (
        f"\u200f<b>🔄 تغییر پنجره فعال</b>\n\n"
        f"\u200f⚠️ با تغییر پنجره، حافظه مدل عوض میشه و از "
        f"\u200fمکالمه قبلی چیزی یادش نمیاد.\n"
        f"\u200f{_DIVIDER}"
    )

    buttons = []
    row = []
    for win in windows:
        btn = Button.inline(
            win["title"],
            f"switch_window_inline:{win['window_id']}".encode(),
            style="success" if win["is_active"] else None
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        Button.inline("➕ پنجره جدید", b"create_window_start", style="success"),
        Button.inline("❌ بستن", b"close_window_panel")
    ])

    await event.reply(text, buttons=buttons, parse_mode="html")


async def new_window_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    windows = self.db.get_user_windows(uid)
    if len(windows) >= 5:
        await event.reply("❌ شما به حداکثر تعداد پنجره‌ها (۵) رسیده‌اید.")
        return

    parts = event.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        self.db.save_pending_state(uid, pending_question=False, pending_action="CREATE_WINDOW_TITLE")
        msg = await event.reply(
            "✍️ نام پنجره جدید را ارسال کنید:\n(مثلا: تحلیل بیتکوین)",
            buttons=[[Button.inline("🔙 انصراف", b"back_to_main")]]
        )
        self.pending_message[(uid, "ask_ai")] = msg
        return

    title = parts[1].strip()
    success = self.db.create_window(uid, title)
    if success:
        windows = self.db.get_user_windows(uid)
        new_win = next((w for w in windows if w["title"] == title), None)
        if new_win:
            self.db.set_active_window(uid, new_win["window_id"])

        await event.reply(
            f"✅ پنجره <b>{title}</b> ساخته شد و فعال شد.",
            parse_mode="html",
            buttons=[[Button.inline("🗂 مدیریت پنجره‌ها", b"manage_windows", style="primary")]]
        )
    else:
        await event.reply("❌ خطا در ساخت پنجره.")


async def clear_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    active_win = self.db.get_active_window(uid)
    if active_win["total_requests"] == 0:
        await event.reply("📌 حافظه این پنجره از قبل خالی است.")
        return

    win_id = active_win["window_id"]
    await event.reply(
        f"⚠️ <b>پاکسازی حافظه پنجره «{active_win['title']}»</b>\n\n"
        f"💬 پیام‌ها: <code>{active_win['total_requests']}</code>\n"
        f"🪙 توکن‌ها: <code>{active_win['total_input_tokens'] + active_win['total_output_tokens']:,}</code>\n\n"
        "آیا مطمئن هستید؟",
        buttons=[
            [Button.inline("✅ بله، پاکسازی شود", f"clear_win_shortcut:{win_id}".encode(), style="danger")],
            [Button.inline("❌ خیر، بازگشت", b"back_to_main")]
        ],
        parse_mode="html"
    )


async def clear_win_shortcut_cb(self: "TelegramBot", event: Any) -> None:
    """Handle confirmed memory clear request from /clear command.
    
    Clears conversation history and metrics for the specified window in the
    database. If the window being cleared is currently active and has an
    in-memory session, that session is destroyed to force a clean reload
    from the database on the next user interaction.
    
    This ensures the cleared history state is immediately reflected in the
    AI model's context, fixing Bug #[MEMORY_CLEAR] where in-memory sessions
    would retain old history after a database clear operation.
    
    Args:
        event: Telegram callback query event containing the window_id.
    """
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    # Clear history and metrics in database
    self.db.save_window_session(win_id, [], 0, 0, 0)

    # If this window is active and has an in-memory session, destroy it.
    # The session will be automatically recreated on next message with
    # fresh (empty) history loaded from the database.
    if uid in self.sessions:
        active_win = self.db.get_active_window(uid)
        if active_win and active_win["window_id"] == win_id:
            del self.sessions[uid]

    await event.answer("✅ حافظه پنجره پاکسازی شد.", alert=True)
    try:
        await event.delete()
    except Exception:
        pass


async def cancel_pending_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    self.db.delete_pending_state(uid)
    self.processing_users.discard(uid)

    # ── Restore previous AI message buttons ──
    # When user cancels, restore original buttons on all previous
    # AI messages so they can continue interacting with them.
    from .ai import _restore_previous_ai_buttons
    await _restore_previous_ai_buttons(self, uid)
    self.previous_ai_messages.pop(uid, None)

    self.pending_message.pop((uid, "ask_ai"), None)

    menu_msg = self.pending_message.pop((uid, "menu_prompt"), None)
    if menu_msg:
        try:
            await menu_msg.delete()
        except Exception:
            pass

    await event.answer("✅ لغو شد", alert=False)
    try:
        await event.delete()
    except Exception:
        pass


# ─── Quick Ask Shortcuts ───────────────────────────────────────────

async def _enter_quick_ask(self: "TelegramBot", event: Any, skill_key: str) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        await self.send_limit_notification(event, limit_msg, is_callback=False)
        return

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        await event.reply(alert_msg)

    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if active_win["mode"] != "quick_ask":
        windows = self.db.get_user_windows(uid)
        qa_win = next((w for w in windows if w["mode"] == "quick_ask"), None)
        if qa_win:
            self.db.set_active_window(uid, qa_win["window_id"])
        else:
            self.db.create_window(uid, "سوال سریع", mode="quick_ask")
            self.db.get_active_window(uid, default_mode="quick_ask")

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        await event.reply(
            "یک مکالمه باز دارید.",
            buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
        )
        return

    # Second-verify check: intercept if user has been idle > 3 hours
    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if should_verify(active_win, "quick_ask"):
        await send_verify_quick_ask(self, event, uid, skill_key, entry_type="entry")
        return

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{skill_key}")

    skill_data = get_skill(skill_key)
    skill_label = f" ({skill_data['name']})" if skill_key != "default" else ""

    self.pending_message.pop((uid, "ask_ai"), None)

    buttons = []
    if skill_key != "default":
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

    buttons.append([Button.inline("🔙 انصراف", b"back_to_main")])

    msg = await event.reply(
        f"✍️ <b>پیامت رو بفرست{skill_label}:</b>\n\n"
        f"{skill_data['icon']} حالت فعال: <b>{skill_data['name']}</b>",
        buttons=buttons, parse_mode="html"
    )
    self.pending_message[(uid, "menu_prompt")] = msg


async def ask_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_quick_ask(self, event, "default")


async def learn_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_quick_ask(self, event, "learn")


async def code_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_quick_ask(self, event, "coding")


async def deep_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_quick_ask(self, event, "deepthink")


# ─── Mentor Shortcuts ──────────────────────────────────────────────

async def _enter_mentor(self: "TelegramBot", event: Any, mentor_key: str) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        await self.send_limit_notification(event, limit_msg, is_callback=False)
        return

    state = self.db.get_pending_state(uid)
    if state["pending_question"]:
        await event.reply(
            "یک مکالمه باز دارید.",
            buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
        )
        return

    role_prompt = self.mentor_prompts.get(mentor_key)
    if not role_prompt:
        await event.reply("منتور یافت نشد.")
        return

    # Second-verify check: intercept if mentor idle > 5 hours
    mentor_win = self.db.get_mentor_window(uid, mentor_key)
    if should_verify(mentor_win, "mentor", mentor_key):
        await send_verify_mentor(self, event, uid, mentor_key, entry_type="entry")
        return

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        await event.reply(alert_msg)

    self.pending_message.pop((uid, "ask_ai"), None)

    self.db.save_pending_state(uid, pending_question=True, pending_role=role_prompt, pending_mentor_key=mentor_key)

    msg = await event.reply(
        f"🎓 منتور <b>{mentor_key}</b> فعال شد.\nپیامت رو ارسال کن.",
        buttons=[[Button.inline("🔙 انصراف", b"back_to_main")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "menu_prompt")] = msg


async def micheal_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_mentor(self, event, "micheal")


async def daye_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_mentor(self, event, "daye")


async def zeussy_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_mentor(self, event, "zeussy")


async def albrooks_cmd(self: "TelegramBot", event: Any) -> None:
    await _enter_mentor(self, event, "albrooks")


# ─── Info Commands ─────────────────────────────────────────────────

async def status_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    from ..utils import get_time_until_reset, make_progress_bar

    active_win = self.db.get_active_window(uid)
    sub_type = self.get_user_subscription(uid)
    usage = self.db.get_user_usage(uid)
    total_requests = usage["total_requests"]
    total_tokens = usage["total_input_tokens"] + usage["total_output_tokens"]
    time_left = get_time_until_reset()

    if sub_type == "paid":
        max_requests = 760
        max_tokens = 300000
    else:
        max_requests = 25
        max_tokens = 150000

    req_bar = make_progress_bar(total_requests, max_requests)
    tok_bar = make_progress_bar(total_tokens, max_tokens)

    mode_label = {
        "quick_ask": "💬 سوال سریع",
        "mentor": f"🎓 منتور ({active_win.get('mentor_key', '-')})"
    }.get(active_win["mode"], active_win["mode"])

    text = (
        f"📊 <b>وضعیت فعلی</b>\n\n"
        f"📌 <b>پنجره فعال:</b> <code>{active_win['title']}</code>\n"
        f"🔄 <b>حالت:</b> {mode_label}\n\n"
        f"👤 <b>اشتراک:</b> <code>{'ویژه' if sub_type == 'paid' else 'رایگان'}</code>\n"
        f"⏳ <b>بازنشانی:</b> <code>{time_left}</code>\n\n"
        f"📈 <b>مصرف این دوره:</b>\n"
        f"🔹 پیام‌ها: <code>{total_requests}</code> / {max_requests}\n"
        f"   {req_bar}\n"
        f"🔹 توکن‌ها: <code>{total_tokens:,}</code> / {max_tokens:,}\n"
        f"   {tok_bar}"
    )

    await event.reply(text, parse_mode="html")


async def help_cmd(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    random_tip = random.choice(TIPS_GENERAL)

    text = f"""
ℹ️ <b>راهنمای کامل OxyGPT</b>

🤖 دستیار هوشمند با AI، جستجوی وب، منتورهای معاملاتی و ژورنال


<b>💬 میخوام سوال بپرسم یا چیزی یاد بگیرم</b>
<code>/ask</code> · <code>/learn</code> · <code>/code</code> · <code>/deep</code>
میتونی عکس بفرستی یا باهاش بحث کنی، مثل یه هم‌فکر حرفه‌ای

<b>🎓 میخوام تحلیل تخصصی بازار داشته باشم</b>
۴ منتور با سبک‌های متفاوت:
<code>/micheal</code> — ICT · <code>/daye</code> — Quarterly Theory
<code>/zeussy</code> — Time-Price · <code>/albrooks</code> — Price Action

<b>🗂 چندتا تحلیل رو همزمان پیش میبرم</b>
تا ۵ پنجره مجزا با حافظه مستقل:
<code>/new نام</code> · <code>/w</code> · <code>/sw</code> · <code>/clear</code>

<b>📊 معاملاتمو ثبت و تحلیل میکنم</b>
از دکمه <b>📊 ژورنال معاملات</b> توی منوی اصلی استفاده کن


<b>⚙️ مدیریت</b>
<code>/status</code> — وضعیت مصرف و اشتراک
<code>/cancel</code> — لغو عملیات در حال انجام

💡 {random_tip}
    """

    buttons = [[Button.inline("🔙 بازگشت به منوی اصلی", b"back_to_main")]]
    await event.reply(text, buttons=buttons, parse_mode="html")




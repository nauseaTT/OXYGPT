import random
from datetime import datetime
from typing import TYPE_CHECKING, Any

from telethon import Button

if TYPE_CHECKING:
    from ..bot import TelegramBot

from ..constants import (
    TIME_GREETINGS, WEEKDAY_GREETINGS, LATE_NIGHT_HOOKS,
    HOOKS, NEW_USER_HOOKS
)
from ..utils import tz_tehran


async def start(self: "TelegramBot", event: Any) -> None:
    """Handle /start and "اکسی" commands — the main entry point.

    Sends the welcome/start menu.  Also serves as a hard reset: any
    leaked pending state (from an abandoned service creation, block
    flow, etc.) is deleted here so that the admin can start fresh
    without stale pending_action values intercepting their next text
    message.

    Order of operations:
      1. Block check (blocked users get a rejection and return).
      2. Group block check (blocked groups are silently ignored).
      3. delete_pending_state — clean up any stale flow state.
      4. Channel membership checks (only for non-admins).
      5. Build and send the welcome menu.
    """
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.reply("⛔️ شما توسط ادمین از دسترسی به ربات مسدود شده‌اید.")
        return

    chat_id = event.chat_id
    if chat_id and chat_id < 0 and self.check_group_blocked(chat_id):
        return

    self.db.delete_pending_state(uid)

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    usage = self.db.get_user_usage(uid)
    is_new = usage.get("total_requests", 0) == 0

    active_win = self.db.get_active_window(uid)
    window_title = active_win.get("title", "")

    first_name = getattr(event.sender, "first_name", None) or "رفیق"

    now = datetime.now(tz_tehran)
    hour = now.hour
    weekday = now.weekday()

    if 6 <= hour < 12:
        time_key = "morning"
    elif 12 <= hour < 17:
        time_key = "afternoon"
    elif 17 <= hour < 20:
        time_key = "evening"
    else:
        time_key = "night"

    is_late_night = 2 <= hour < 5

    if is_new:
        welcome = random.choice(NEW_USER_HOOKS)
        hook = random.choice(HOOKS)
        greeting = f"🎉 {welcome} {first_name}! {hook}"
    else:
        greeting_text = random.choice(TIME_GREETINGS[time_key])

        if is_late_night:
            hook = random.choice(LATE_NIGHT_HOOKS)
        else:
            available = [h for h in HOOKS if h != self.last_hook.get(uid)]
            hook = random.choice(available if available else HOOKS)
        self.last_hook[uid] = hook

        if weekday in WEEKDAY_GREETINGS:
            greeting = f"{greeting_text} {first_name}! {WEEKDAY_GREETINGS[weekday]} {hook}"
        else:
            greeting = f"{greeting_text} {first_name}! {hook}"

        if window_title and window_title not in ("پنجره اصلی", "سوال سریع"):
            greeting += f"\n\n🗂 <code>{window_title}</code>"

    buttons = [
        [
            Button.inline("💬 سوال سریع", b"quickask", style="primary"),
            Button.inline("👥 منتورها", b"mentors", style="success")
        ],
        [
            Button.inline("🗂 مدیریت پنجره‌های مکالمه", b"manage_windows", style="primary")
        ],
        [
            Button.inline("📊 ژورنال معاملات", b"trading_ai_panel"),
            Button.inline("📡 پایش کانال", b"cw_list_monitors"),
        ],
        [
            Button.inline("ℹ️ راهنما", b"help_menu"),
            Button.inline("🛟 پشتیبان هوشمند", b"support_entry", style="success"),
        ]
    ]

    if uid in self.admin_ids:
        buttons.append([Button.inline("⚙️ پنل ادمین", b"admin_panel")])

    await event.reply(greeting + "\n\n👇", buttons=buttons, parse_mode="html")


async def check_membership_callback(self: "TelegramBot", event: object) -> None:
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await event.answer("❌ شما هنوز عضو تمام کانال‌ها/گروه‌ها نشده‌اید!", alert=True)
    else:
        await event.answer("🎉 عضویت شما تایید شد!", alert=True)
        await event.delete()
        await start(self, event)


async def cancel_command(self: "TelegramBot", event: object) -> None:
    """Handle /cancel — abort any in-progress flow and reset user state.

    Performs four tiers of cleanup:
      1. Active request: cancels the in-flight AI task (if any) and
         stops the associated StatusAnimator so the animated message
         stops being edited.
      2. DB: delete_pending_state removes any pending_action/ pending_question
         from the database, so the next text message won't be misrouted.
      3. Memory: processing_users discard ensures the user is no longer
         marked as "in a request" (relevant if the user was waiting for a reply).
      4. UI: delete known pending_message entries to clean up stale bot
         messages.  Only the most common keys are covered here; service
         creation keys (service_create, service_set_url, etc.) are cleaned
         up individually in the SERVICE_CREATE_* handlers or via the early
         return in pending_message_handler.

    Note: This handler intentionally does NOT cover every pending_message key
    (e.g. ``service_create``, ``service_set_url``, ``openai_base_url``).
    Those are ephemeral — if the user abandoned the flow, delete_pending_state
    already prevents re-entry, and the stale messages eventually expire via
    ``_cleanup_stale_data_loop``.
    """
    uid = event.sender_id
    self.db.delete_pending_state(uid)
    self.processing_users.discard(uid)

    # ── Clear Channel Watcher pending states (if any) ─────────────
    try:
        from channel_watcher.states import get_state_manager as _cw_mgr
        await _cw_mgr().clear_state(uid)
    except ImportError:
        pass

    # ── Cancel active AI request (if any) ─────────────────────────
    req_info = self.active_requests.pop(uid, None)
    if req_info:
        animator = req_info.get("animator")
        if animator:
            try:
                await animator.stop()
            except Exception:
                pass

        task = req_info.get("task")
        if task:
            try:
                task.cancel()
            except Exception:
                pass

        msg = req_info.get("msg")
        if msg:
            try:
                await msg.edit("❌ درخواست لغو شد.")
            except Exception:
                pass

    # ── Clean up known pending_message entries ────────────────────
    # Restore previous AI message buttons before cleanup
    from .ai import _restore_previous_ai_buttons
    await _restore_previous_ai_buttons(self, uid)
    self.previous_ai_messages.pop(uid, None)

    prev_msg = self.pending_message.pop((uid, "ask_ai"), None)
    if prev_msg:
        try:
            await prev_msg.delete()
        except Exception:
            pass

    prev_lock = self.pending_message.pop((uid, "add_lock"), None)
    if prev_lock:
        try:
            await prev_lock.delete()
        except Exception:
            pass

    prev_block_user = self.pending_message.pop((uid, "block_user"), None)
    if prev_block_user:
        try:
            await prev_block_user.delete()
        except Exception:
            pass

    prev_block_group = self.pending_message.pop((uid, "block_group"), None)
    if prev_block_group:
        try:
            await prev_block_group.delete()
        except Exception:
            pass

    await event.reply("❌ عملیات جاری لغو شد و وضعیت شما بازنشانی گردید.")


async def arise_command(self: "TelegramBot", event: object) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        return

    if not event.is_reply:
        await event.reply("احمق این پیامو باید رو یکی ریپلای کنی")
        return

    reply_msg = await event.get_reply_message()
    if not reply_msg or not reply_msg.sender_id:
        await event.reply("روی شبح ریپلای زدی؟ طرف آیدی عددی نداره")
        return

    target_uid = reply_msg.sender_id
    await self.send_user_management_panel(event, target_uid)


async def send_user_management_panel(self: "TelegramBot", event: object, target_uid: int) -> None:
    sub_type = self.get_user_subscription(target_uid)
    usage = self.db.get_user_usage(target_uid)
    windows = self.db.get_user_windows(target_uid)
    is_blocked = self.db.is_user_blocked(target_uid)

    total_requests = usage.get("total_requests", 0)
    total_input_tokens = usage.get("total_input_tokens", 0)
    total_output_tokens = usage.get("total_output_tokens", 0)
    total_tokens = total_input_tokens + total_output_tokens

    block_status = "🚫 مسدود" if is_blocked else "✅ فعال"

    text = (
        f"👤 <b>پنل مدیریت دیتابیس کاربر:</b>\n\n"
        f"🔹 <b>آیدی عددی:</b> <code>{target_uid}</code>\n"
        f"🔹 <b>نوع اشتراک:</b> <code>{'ویژه (Paid)' if sub_type == 'paid' else 'رایگان (Free)'}</code>\n"
        f"🔹 <b>وضعیت:</b> {block_status}\n\n"
        f"📊 <b>آمار مصرف ۱۲ ساعته کاربر:</b>\n"
        f"▫️ تعداد درخواست‌ها: <code>{total_requests}</code>\n"
        f"▫️ توکن ورودی: <code>{total_input_tokens:,}</code>\n"
        f"▫️ توکن خروجی: <code>{total_output_tokens:,}</code>\n"
        f"▫️ مجموع توکن‌ها: <code>{total_tokens:,}</code>\n\n"
        f"🗂 <b>تعداد پنجره‌های مکالمه:</b> <code>{len(windows)}</code>\n"
    )

    is_blocked = self.db.is_user_blocked(target_uid)
    block_btn_text = "✅ آنبلاک کاربر" if is_blocked else "🚫 بلاک کاربر"
    block_btn_data = f"admin_unblock_user:{target_uid}".encode() if is_blocked else f"admin_block_user:{target_uid}".encode()
    block_btn_style = "success" if is_blocked else "danger"

    buttons = [
        [
            Button.inline("🔄 تغییر اشتراک (Free ⇄ Paid)", f"admin_toggle_sub:{target_uid}".encode(), style="primary")
        ],
        [
            Button.inline("🧹 ریست لیمیت مصرف ۱۲ ساعته", f"admin_reset_user_limit:{target_uid}".encode(), style="danger")
        ],
        [
            Button.inline("🗑 پاکسازی تمام پنجره‌ها", f"admin_clear_user_windows:{target_uid}".encode(), style="danger")
        ],
        [
            Button.inline(block_btn_text, block_btn_data, style=block_btn_style)
        ],
        [
            Button.inline("❌ بستن پنل ادمین", b"close_window_panel")
        ]
    ]

    from telethon import events as telethon_events
    if isinstance(event, telethon_events.CallbackQuery.Event):
        await event.edit(text, buttons=buttons, parse_mode="html")
    else:
        await event.reply(text, buttons=buttons, parse_mode="html")

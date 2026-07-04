from typing import Any, Tuple, List, TYPE_CHECKING
from telethon import Button

from ..utils import get_time_until_reset, make_progress_bar

if TYPE_CHECKING:
    from ..bot import TelegramBot


def _build_windows_panel(self: "TelegramBot", uid: int) -> Tuple[str, List]:
    windows = self.db.get_user_windows(uid)
    active_win = self.db.get_active_window(uid)
    sub_type = self.get_user_subscription(uid)
    time_left = get_time_until_reset()

    usage = self.db.get_user_usage(uid)
    user_total_requests = usage["total_requests"]
    user_total_tokens = usage["total_input_tokens"] + usage["total_output_tokens"]

    if sub_type == "paid":
        max_requests = 760
        max_tokens = 300000
    else:
        max_requests = 25
        max_tokens = 150000

    req_bar = make_progress_bar(user_total_requests, max_requests)
    tok_bar = make_progress_bar(user_total_tokens, max_tokens)

    text = (
        "🗂 <b>مدیریت پنجره‌های مکالمه</b>\n\n"
        "شما می‌توانید تا ۵ پنجره مکالمه مجزا با حافظه اختصاصی داشته باشید.\n\n"
        f"👤 <b>نوع اشتراک شما:</b> <code>{'پولی (ویژه)' if sub_type == 'paid' else 'رایگان'}</code>\n"
        f"⏳ <b>زمان باقی‌مانده تا ریست شدن محدودیت‌ها:</b> <code>{time_left}</code>\n\n"
        f"📈 <b>میزان مصرف کل حساب شما در این دوره ۱۲ ساعته:</b>\n"
        f"🔹 <b>تعداد پیام‌ها:</b> {user_total_requests} از {max_requests}\n"
        f"   {req_bar}\n"
        f"🔹 <b>توکن‌های مصرفی:</b> {user_total_tokens:,} از {max_tokens:,}\n"
        f"   {tok_bar}\n\n"
        f"📌 <b>پنجره فعال فعلی:</b> <code>{active_win['title']}</code>\n"
        f"📊 مصرف توکن این پنجره: <code>{active_win['total_input_tokens'] + active_win['total_output_tokens']:,}</code>\n"
        f"💬 پیام‌های دریافتی این پنجره: <code>{active_win['total_requests']}</code>\n\n"
        "<b>لیست پنجره‌های شما:</b>\n"
    )

    buttons = []
    for win in windows:
        status = "🟢 (فعال)" if win["is_active"] else ""
        text += f"- {win['title']} {status} [پیام‌ها: {win['total_requests']}]\n"

        style = "success" if win["is_active"] else "primary"
        row = [
            Button.inline(f"👁 {win['title']}", f"set_active_win:{win['window_id']}".encode(), style=style),
            Button.inline("✏️ تغییر نام", f"rename_win_start:{win['window_id']}".encode()),
            Button.inline("🧹 پاکسازی", f"clear_win_req:{win['window_id']}".encode(), style="danger")
        ]
        if len(windows) > 1:
            row.append(Button.inline("🗑 حذف", f"delete_win_req:{win['window_id']}".encode(), style="danger"))
        buttons.append(row)

    if len(windows) < 5:
        buttons.append([Button.inline("➕ ساخت پنجره جدید", b"create_window_start", style="success")])

    buttons.append([Button.inline("🔙 بازگشت به منوی اصلی", b"back_to_main")])

    return text, buttons


async def manage_windows_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        try:
            await event.answer(alert_msg, alert=True)
        except AttributeError:
            await event.reply(alert_msg)
    else:
        try:
            await event.answer()
        except AttributeError:
            pass

    text, buttons = _build_windows_panel(self, uid)
    await event.edit(text, buttons=buttons, parse_mode="html")


async def create_window_start_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    self.db.save_pending_state(uid, pending_question=False, pending_action="CREATE_WINDOW_TITLE")
    msg = await event.edit(
        "✍️ لطفا نام پنجره جدید خود را ارسال کنید:\n(مثلا: تحلیل بیتکوین)",
        buttons=[[Button.inline("🔙 انصراف", b"manage_windows")]]
    )
    self.pending_message[(uid, "ask_ai")] = msg


async def set_active_win_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    self.db.set_active_window(uid, win_id)
    await event.answer("پنجره فعال تغییر یافت.", alert=True)
    await manage_windows_cb(self, event)


async def delete_win_req_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    await event.edit(
        "⚠️ <b>آیا از حذف این پنجره مطمئن هستید؟</b>\nتمام تاریخچه پیام‌های این پنجره برای همیشه پاک خواهد شد.",
        buttons=[
            [Button.inline("✅ بله، حذف شود", f"delete_win_confirm:{win_id}".encode(), style="danger")],
            [Button.inline("❌ خیر، بازگشت", b"manage_windows", style="primary")]
        ],
        parse_mode="html"
    )


async def delete_win_confirm_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    self.db.delete_window(uid, win_id)
    await event.answer("پنجره با موفقیت حذف شد.", alert=True)
    await manage_windows_cb(self, event)


async def clear_win_req_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    await event.edit(
        "⚠️ <b>آیا از پاکسازی حافظه این پنجره مطمئن هستید؟</b>\nتمام تاریخچه پیام‌های این پنجره صفر خواهد شد.",
        buttons=[
            [Button.inline("✅ بله، پاکسازی شود", f"clear_win_confirm:{win_id}".encode(), style="danger")],
            [Button.inline("❌ خیر، بازگشت", b"manage_windows", style="primary")]
        ],
        parse_mode="html"
    )


async def clear_win_confirm_cb(self: "TelegramBot", event: Any) -> None:
    """Clear conversation history and metrics for a specific window.
    
    Clears the window's history from the database and removes the in-memory
    session if it's the currently active window. This ensures that the next
    message will reload from the database with empty history instead of using
    stale in-memory data.
    
    Args:
        self: TelegramBot instance.
        event: Telethon callback query event.
    
    Workflow:
        1. Verify user owns the window
        2. Clear history in database (history=[], metrics=0)
        3. If window is currently active, delete in-memory session
        4. Next message will auto-create fresh session from DB
    """
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    # Clear history and metrics in database
    self.db.save_window_session(win_id, [], 0, 0, 0)

    # If this window is currently active and has an in-memory session,
    # delete it to force reload from DB on next message
    if uid in self.sessions:
        active_win = self.db.get_active_window(uid)
        if active_win and active_win["window_id"] == win_id:
            del self.sessions[uid]

    await event.answer("حافظه پنجره با موفقیت پاکسازی شد.", alert=True)
    await manage_windows_cb(self, event)


async def rename_win_start_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=False, pending_action=f"RENAME_WINDOW_TITLE:{win_id}")
    msg = await event.edit(
        "✍️ لطفا نام جدید پنجره را ارسال کنید:",
        buttons=[[Button.inline("🔙 انصراف", b"manage_windows")]]
    )
    self.pending_message[(uid, "ask_ai")] = msg

import asyncio
from typing import Any, TYPE_CHECKING
# v2: `from telethon import Button` -> compat Button facade over the v2
# button classes (types.Button.Callback/Url/...). See telethon_compat.py.
from telethon_compat import Button

if TYPE_CHECKING:
    from ..bot import TelegramBot


async def select_window_panel_handler(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    data = event.data.decode().replace("select_window_panel:", "").strip()
    try:
        owner_id = int(data)
    except ValueError:
        await event.answer()
        return

    if uid != owner_id:
        await event.answer("این دکمه متعلق به کاربر دیگری است!", alert=True)
        return

    from ..utils import _DIVIDER

    windows = self.db.get_user_windows(uid)

    text = (
        f"\u200f<b>🗂 انتخاب پنجره</b>\n\n"
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

    buttons.append([Button.inline("❌ بستن پنل", b"close_window_panel")])

    await event.respond(text, buttons=buttons, parse_mode="html")
    await event.answer()


async def switch_window_inline_handler(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    win_id = int(event.data.decode().split(":")[1])

    if not self.db.is_window_owner(uid, win_id):
        await event.answer("❌ خطا: دسترسی غیرمجاز!", alert=True)
        return

    self.db.set_active_window(uid, win_id)
    active_win = self.db.get_active_window(uid)

    if uid in self.sessions:
        from api_http import AI_Service
        self.sessions[uid] = AI_Service(uid, db_manager=self.db, mode=active_win["mode"], mentor_key=active_win["mentor_key"])

    await event.answer(f"✅ پنجره فعال به «{active_win['title']}» تغییر یافت.", alert=False)

    msg_count = active_win["total_requests"]
    token_count = active_win["total_input_tokens"] + active_win["total_output_tokens"]

    try:
        await event.edit(
            f"\u200f✅ <b>پنجره فعال با موفقیت تغییر یافت</b>\n\n"
            f"\u200f📌 <b>پنجره جدید:</b> <code>{active_win['title']}</code>\n"
            f"\u200f💬 {msg_count} پیام  •  🪙 {token_count:,} توکن\n\n"
            f"\u200f<i>این پیام به زودی حذف می‌شود...</i>",
            buttons=[Button.inline("❌ بستن", b"close_window_panel")],
            parse_mode="html"
        )
        await asyncio.sleep(2)
        await event.delete()
    except Exception:
        pass


async def inline_query(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        return

    # v2: `events.InlineQuery` in 2.0.0a0 does not yet ship the v1 helpers
    # `event.text`, `event.sender_id`, `event.builder.article(...)` and
    # `event.answer(results)`. The compat layer re-adds them using the raw
    # `messages.set_inline_bot_results` request, so this handler is unchanged.
    query = event.text + "\n"
    await event.answer([
        event.builder.article(title=title, text=query + id, description=description)
        for title, _, id, description in self.inline_article
    ])

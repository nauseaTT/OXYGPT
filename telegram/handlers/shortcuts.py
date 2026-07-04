import random
from typing import Any, Dict, List, Optional, TYPE_CHECKING
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

# ─── Reply-Ask Helpers ─────────────────────────────────────────────

# Maximum hops in a reply chain before we stop following.
_REPLY_CHAIN_MAX_HOPS: int = 5
# Total character limit across the entire reply chain (to prevent OOM).
_REPLY_CHAIN_MAX_CHARS: int = 4000


async def resolve_reply_chain(
    event: Any,
    max_hops: int = _REPLY_CHAIN_MAX_HOPS,
    max_chars: int = _REPLY_CHAIN_MAX_CHARS,
) -> List[Dict[str, Any]]:
    """Walk the reply chain backwards, collecting text, sender info, and the first image.

    Follows ``event.is_reply`` links up to ``max_hops`` levels deep,
    extracting the text content and sender identity from each message.
    The **first** photo encountered in the chain is returned as raw bytes;
    subsequent photos are ignored (only one image per request is supported).

    Sender information (username and display name) is resolved via
    ``reply_msg.get_sender()`` for each hop.  If resolution fails the
    fields are set to ``None`` — downstream callers (see
    ``_format_reply_ask_message``) gracefully fall back to the bare
    quote format.

    If the combined text exceeds ``max_chars``, the deepest levels are
    truncated first with an appended ``… (truncated)`` marker.

    Args:
        event: The incoming Telethon event (must support ``is_reply`` and
            ``get_reply_message()``).
        max_hops: Maximum number of reply hops to follow.
        max_chars: Maximum total characters across the chain.

    Returns:
        List of dicts, each containing:
        - ``text``: The message text (may be empty).
        - ``image``: Raw image bytes (``None`` if no photo or already found).
        - ``level``: 1-indexed depth (1 = immediate reply, 2 = reply-to-reply…).
        - ``sender_id``: Telegram user ID of the message author.
        - ``sender_username``: Telegram @username (``None`` if unavailable).
        - ``sender_display_name``: First + last name (``None`` if unavailable).
    """
    chain: List[Dict[str, Any]] = []
    total_chars: int = 0
    image_found: bool = False
    current = event

    for _ in range(max_hops):
        if not current.is_reply:
            break
        try:
            reply_msg = await current.get_reply_message()
        except Exception:
            break
        if not reply_msg:
            break

        text = (reply_msg.text or "").strip()
        image = None
        if reply_msg.photo and not image_found:
            try:
                image = await reply_msg.download_media(bytes)
                image_found = True
            except Exception:
                pass

        sender_username = None
        sender_display_name = None
        try:
            sender = await reply_msg.get_sender()
            if sender:
                sender_username = getattr(sender, 'username', None)
                # User objects use first/last name; Chat/Channel use title
                first = getattr(sender, 'first_name', None)
                last = getattr(sender, 'last_name', None)
                title = getattr(sender, 'title', None)
                if first is not None or last is not None:
                    sender_display_name = ((first or '') + ' ' + (last or '')).strip() or None
                elif title:
                    sender_display_name = title
        except Exception:
            pass

        total_chars += len(text)
        chain.append({
            "text": text,
            "image": image,
            "level": len(chain) + 1,
            "sender_id": reply_msg.sender_id,
            "sender_username": sender_username,
            "sender_display_name": sender_display_name,
        })

        # Move to the next level in the chain
        current = reply_msg

    # Truncate from the deepest level if total exceeds max_chars
    if total_chars > max_chars:
        for item in reversed(chain):
            excess = total_chars - max_chars
            if excess <= 0:
                break
            item_text = item["text"]
            if not item_text:
                continue
            if len(item_text) > excess:
                trim_point = max(0, len(item_text) - excess - 20)
                item["text"] = item_text[:trim_point] + "\n… (truncated)"
                total_chars -= excess
            else:
                total_chars -= len(item_text)
                item["text"] = ""

    return chain


def _format_reply_ask_message(
    user_text: str,
    reply_chain: List[Dict[str, Any]],
) -> str:
    """Build the structured message text for a reply-to-ask request.

    When both ``user_text`` and a ``reply_chain`` are present, the user's
    text is placed first (with a trailing colon), followed by each level of
    the reply chain wrapped in ``««« … »»»`` delimiters.  The chain is
    ordered from outermost (deepest replied-to) to innermost (most recent).

    The delimiter nesting depth indicates the reply level:
    - Level 1: ``«««text»»»``
    - Level 2: ``«««««text»»»»»``
    - etc.

    **Sender attribution**

    Each quoted block is prefixed with sender information resolved by
    ``resolve_reply_chain``.  The format is:

    - If both @username and display name are available:
      ``«««@username (First Last): text»»»``
    - If only @username is available:
      ``«««@username: text»»»``
    - If only display name is available:
      ``«««First Last: text»»»``
    - If neither is available:
      ``«««text»»»``  (bare quote, unchanged)

    When only one of the two is present, the other is omitted entirely.

    Args:
        user_text: The user's own text after ``/ask`` (may be empty).
        reply_chain: The resolved reply chain from ``resolve_reply_chain()``.

    Returns:
        A single formatted string ready to be sent as the user message
        to the AI model.
    """
    parts: List[str] = []

    if user_text:
        parts.append(user_text + ":")

    for item in reversed(reply_chain):
        text = item["text"]
        if not text:
            continue
        level = item["level"]
        opener = "«" * level
        closer = "»" * level

        sender_username: Optional[str] = item.get("sender_username")
        sender_display_name: Optional[str] = item.get("sender_display_name")

        sender_prefix = ""
        if sender_username and sender_display_name:
            sender_prefix = f"@{sender_username} ({sender_display_name}): "
        elif sender_username:
            sender_prefix = f"@{sender_username}: "
        elif sender_display_name:
            sender_prefix = f"{sender_display_name}: "

        parts.append(f"{opener}{sender_prefix}{text}{closer}")

    return "\n\n".join(parts)

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
    """Handle the /ask command.

    Three distinct paths depending on input:

    1. **Reply-to-message** (with or without inline text) — stateless:
       Walks the reply chain, builds a formatted message, and sends it
       directly to the AI model.  No conversation history is saved.
       If the replied message has no text AND no image (sticker, voice,
       deleted message, etc.) the user receives a notification instead
       of a blank query.

    2. **Standalone ``/ask <text>``** (no reply) — stateless:
       The inline text is sent directly to the AI model as a standalone
       query.  No conversation history is saved.

    3. **Bare ``/ask``** (no reply, no inline text) — normal flow:
       Prompts the user for their message via ``_enter_quick_ask``.
       Conversation history IS saved (standard window-based flow).

    Args:
        event: Incoming Telethon message event.
    """
    uid = event.sender_id
    parts = (event.text or "").strip().split(maxsplit=1)
    text_after_cmd = parts[1].strip() if len(parts) > 1 else ""

    # ── Stateless path: reply or inline text present ────────────────
    if event.is_reply or text_after_cmd:
        from .ai import _process_reply_ask

        event_image_data = None
        if event.photo:
            event_image_data = await event.download_media(bytes)

        chain = []
        if event.is_reply:
            chain = await resolve_reply_chain(event)

        # Event photo takes priority over reply chain image
        image_data = event_image_data
        if image_data is None and chain:
            for item in chain:
                if item["image"]:
                    image_data = item["image"]
                    break

        formatted_text = _format_reply_ask_message(text_after_cmd, chain)

        # If the combined result is empty AND there's no image, we cannot
        # send a blank query to the model.  Two sub‑cases:
        #
        # 1. User replied to a message — the replied message has no text
        #    or image content (sticker, voice, deleted message, etc.).
        #    Inform the user instead of falling into the verify panel.
        # 2. Bare /ask (no reply, no inline text) — should never reach
        #    here because the outer `if event.is_reply or text_after_cmd`
        #    gate would be False.  Safety fallback to the normal flow.
        if not formatted_text and not image_data:
            if event.is_reply:
                await event.reply("❌ پیامی که به آن ریپلای کرده‌اید محتوای متنی ندارد.")
                return
            await _enter_quick_ask(self, event, "default")
            return

        await _process_reply_ask(
            self, event, formatted_text,
            user_text=text_after_cmd, reply_chain=chain,
            image_data=image_data,
        )
        return

    # ── Normal (conversational) /ask ────────────────────────────────
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




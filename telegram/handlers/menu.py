import json
import random
from typing import Any, TYPE_CHECKING
# v2: Button now comes from the compat facade (v2 button classes). See telethon_compat.py.
from telethon_compat import Button

if TYPE_CHECKING:
    from ..bot import TelegramBot

from skills import SKILLS, get_skill, get_skill_prompt
from ..constants import TIPS_GENERAL, HELP_TEXT
from .verify import should_verify, send_verify_quick_ask


async def quickask_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id

    if uid not in self.admin_ids and self.check_user_blocked(uid):
        await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
        return

    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        await self.send_limit_notification(event, limit_msg, is_callback=True)
        return

    is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
    if is_80 and alert_msg:
        await event.answer(alert_msg, alert=True)
    else:
        await event.answer(alert=False)

    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if active_win["mode"] != "quick_ask":
        windows = self.db.get_user_windows(uid)
        qa_win = next((w for w in windows if w["mode"] == "quick_ask"), None)
        if qa_win:
            self.db.set_active_window(uid, qa_win["window_id"])
        else:
            self.db.create_window(uid, "سوال سریع", mode="quick_ask")

    # Re-fetch fresh active window after possible mode correction
    fresh_win = self.db.get_active_window(uid, default_mode="quick_ask")

    state = self.db.get_pending_state(uid)
    pending_action = state.get("pending_action") or ""
    active_skill = pending_action.replace("skill_", "") if pending_action.startswith("skill_") else "default"

    # Second-verify check: intercept if user has been idle > 3 hours
    if should_verify(fresh_win, "quick_ask"):
        await send_verify_quick_ask(self, event, uid, active_skill, entry_type="skill_select")
        return

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{active_skill}")

    # Show skill selection and store reference for cleanup
    msg = await _show_skill_selection_direct(event, uid, active_skill)
    if msg:
        self.pending_message[(uid, "menu_prompt")] = msg


async def skill_toggle_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    data = event.data.decode()
    skill_key = data.replace("skill_toggle:", "").strip()
    
    if skill_key not in SKILLS:
        await event.answer("اسکیل نامعتبر!", alert=True)
        return

    state = self.db.get_pending_state(uid)
    current_skill = "default"
    pending_action = state.get("pending_action") or ""
    if pending_action.startswith("skill_"):
        current_skill = pending_action.replace("skill_", "")

    if skill_key == current_skill and skill_key != "default":
        new_skill = "default"
        await event.answer("✅ اسکیل غیرفعال شد", alert=True)
    else:
        new_skill = skill_key
        skill_data = get_skill(skill_key)
        await event.answer(f"✅ اسکیل {skill_data['name']} فعال شد", alert=True)

    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{new_skill}")

    on_ai_response = self.pending_message.get((uid, "ask_ai")) is not None

    buttons = []
    if on_ai_response:
        buttons.append([
            Button.inline("✅ پیامت رو بفرست", f"Aquickask_{uid}_{new_skill}".encode(), style="success"),
            Button.inline("🗂 انتخاب پنجره", f"select_window_panel:{uid}".encode(), style="primary")
        ])

    row = []
    for sk, sd in SKILLS.items():
        if sk == "default":
            continue
        is_active = (sk == new_skill)
        btn_text = f"{'✅ ' if is_active else ''}{sd['icon']} {sd['name']}"
        btn_data = f"skill_toggle:{sk}"
        style = "success" if is_active else "primary"
        row.append(Button.inline(btn_text, btn_data.encode(), style=style))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not on_ai_response:
        buttons.append([Button.inline("🔙 انصراف", b"back_to_main")])

    try:
        msg = await event.edit(buttons=buttons)
    except Exception:
        msg = await event.respond(
            "✍️ <b>پیامت رو بفرست:</b>",
            buttons=buttons,
            parse_mode="html"
        )
    if msg:
        self.pending_message[(uid, "menu_prompt")] = msg


async def skill_send_message_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    is_limited, limit_msg = self.check_user_limits(uid)
    if is_limited:
        await self.send_limit_notification(event, limit_msg, is_callback=True)
        return

    state = self.db.get_pending_state(uid)
    active_skill = "default"
    pending_action = state.get("pending_action") or ""
    if pending_action.startswith("skill_"):
        active_skill = pending_action.replace("skill_", "")

    # Second-verify check: intercept if user has been idle > 3 hours
    active_win = self.db.get_active_window(uid, default_mode="quick_ask")
    if should_verify(active_win, "quick_ask"):
        await send_verify_quick_ask(self, event, uid, active_skill, entry_type="entry")
        return

    skill_data = get_skill(active_skill)
    skill_text = f" ({skill_data['name']})" if active_skill != "default" else ""
    
    self.db.save_pending_state(uid, pending_question=True, pending_action=f"skill_{active_skill}")
    
    self.pending_message.pop((uid, "ask_ai"), None)

    buttons = [
        [Button.inline("🔙 انصراف", b"back_to_main")]
    ]
    
    try:
        msg = await event.edit(
            f"✍️ <b>پیامت رو بفرست{skill_text}:</b>\n\n"
            f"{skill_data['icon']} حالت فعال: <b>{skill_data['name']}</b>",
            buttons=buttons,
            parse_mode="html"
        )
    except Exception:
        msg = await event.respond(
            f"✍️ <b>پیامت رو بفرست{skill_text}:</b>\n\n"
            f"{skill_data['icon']} حالت فعال: <b>{skill_data['name']}</b>",
            buttons=buttons,
            parse_mode="html"
        )

    self.pending_message[(uid, "menu_prompt")] = msg


async def mentors_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    await event.edit(
        "🎓 منتورت رو انتخاب کن:",
        buttons=[
            [Button.inline("Micheal", b"mentor_micheal", style="primary")],
            [Button.inline("Arsham", b"place_holder", style="primary")],
            [Button.inline("Daye", b"mentor_daye", style="primary")],
            [Button.inline("Zeussy", b"mentor_zeussy", style="primary")],
            [Button.inline("Albrooks", b"mentor_albrooks", style="primary")],
            [Button.inline("🔙 بازگشت", b"back_to_main")]
        ]
    )


async def placeholder_cb(self: "TelegramBot", event: Any) -> None:
    await event.answer("", alert=False)


async def limit_countdown_cb(self: "TelegramBot", event: Any) -> None:
    """
    نمایش پاپآپ با زمان باقیمانده و پیشنهاد خرید اشتراک
    """
    from ..utils import get_time_until_reset
    
    time_left = get_time_until_reset()
    uid = event.sender_id
    sub_type = self.get_user_subscription(uid)
    
    if sub_type == "paid":
        # کاربر اشتراک ویژه دارد
        await event.answer(
            f"⏳ زمان باقیمانده تا ریست: {time_left}\n\n"
            f"شما از اشتراک ویژه استفاده میکنید.",
            alert=True
        )
    else:
        # کاربر رایگان است - پیشنهاد خرید اشتراک
        await event.answer(
            f"⏳ تا ریست: {time_left}\n\n"
            f"💎 اشتراک ویژه: محدودیت نداشته باش!\n"
            f"@OXYGPTxSupport",
            alert=True
        )


async def help_menu_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    not_joined = await self.check_user_joined(uid)
    if not_joined:
        await self.send_join_warning(event, not_joined)
        return

    random_tip = random.choice(TIPS_GENERAL)

    text = HELP_TEXT.format(tip=random_tip)
    buttons = [[Button.inline("🔙 بازگشت به منوی اصلی", b"back_to_main")]]
    try:
        await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        await event.respond(text, buttons=buttons, parse_mode="html")


async def generic_callback(self: "TelegramBot", event: Any) -> None:
    data = event.data
    if not data:
        return
    try:
        key = data.decode()
    except Exception:
        return

    uid = event.sender_id
    if key.startswith("mentor_"):
        if uid not in self.admin_ids and self.check_user_blocked(uid):
            await event.answer("⛔️ شما مسدود شده‌اید.", alert=True)
            return
        not_joined = await self.check_user_joined(uid)
        if not_joined:
            await self.send_join_warning(event, not_joined)
            return

        mentor_key = key.replace("mentor_", "").strip()

        is_limited, limit_msg = self.check_user_limits(uid)
        if is_limited:
            await self.send_limit_notification(event, limit_msg, is_callback=True)
            return

        state = self.db.get_pending_state(uid)
        if state["pending_question"]:
            await event.answer("یک مکالمه باز دارید.", alert=True)
            await event.respond(
                "یک مکالمه باز دارید.",
                buttons=[[Button.inline("❌ لغو مکالمه قبلی", b"cancel_pending", style="danger")]]
            )
            return

        role_prompt = self.mentor_prompts.get(mentor_key)
        if not role_prompt:
            await event.answer("منتور یافت نشد.", alert=True)
            return

        # Second-verify check: intercept if mentor idle > 5 hours
        mentor_win = self.db.get_mentor_window(uid, mentor_key)
        if should_verify(mentor_win, "mentor", mentor_key):
            from .verify import send_verify_mentor
            await send_verify_mentor(self, event, uid, mentor_key, entry_type="entry")
            return

        is_80, alert_msg = self.check_user_80_percent_limit(uid, trigger_alert=True)
        if is_80 and alert_msg:
            await event.answer(alert_msg, alert=True)
        else:
            await event.answer()

        self.db.save_pending_state(uid, pending_question=True, pending_role=role_prompt, pending_mentor_key=mentor_key)

        msg = await event.edit(
            f"🎓 منتور {mentor_key} انتخاب شد. پیامت رو ارسال کن.",
            buttons=[[Button.inline("🔙 انصراف", b"back_to_main")]]
        )

        self.pending_message[(uid, "ask_ai")] = msg


async def back_to_main_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    self.db.delete_pending_state(uid)
    self.processing_users.discard(uid)

    # ── Clear previous AI messages tracking ──
    # When user goes back to main menu, clear the tracking list.
    # Previous messages are already deleted below, so no need to restore.
    self.previous_ai_messages.pop(uid, None)

    prev_msg = self.pending_message.pop((uid, "ask_ai"), None)
    if prev_msg:
        try:
            await prev_msg.delete()
        except Exception:
            pass

    # Also clean up any stale verify message
    verify_msg = self.pending_message.pop((uid, "verify_msg"), None)
    if verify_msg:
        try:
            await verify_msg.delete()
        except Exception:
            pass

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
        ]
    ]
    if uid in self.admin_ids:
        buttons.append([Button.inline("⚙️ پنل ادمین", b"admin_panel")])

    text = """
 سلام رفیق! خوش اومدی 🚀

 اینجا یه دستیار هوشمند معاملاتی داری که قراره تو تحلیل بازار، یادگیری سبک‌های مختلف و کدنویسی استراتژی‌ها کمکت کنه. 

 از دکمه‌های زیر استفاده کن تا کار رو شروع کنیم. اگرم جایی گیج شدی، دکمه <b>ℹ️ راهنما</b> رو بزن تا کامل برات توضیح بدم داستان چیه.
    """

    try:
        await event.edit(text, buttons=buttons, parse_mode="html")
    except Exception:
        await event.respond(text, buttons=buttons, parse_mode="html")


async def _show_skill_selection_direct(event: Any, uid: int, active_skill: str = "default") -> Any:
    """Show skill selection buttons directly in the message context. Returns the message object."""
    from skills import SKILLS, get_skill
    from telethon_compat import Button  # v2: compat Button facade
    
    buttons = []
    row = []
    
    for skill_key, skill_data in SKILLS.items():
        if skill_key == "default":
            continue
        is_active = (skill_key == active_skill)
        btn_text = f"{'✅ ' if is_active else ''}{skill_data['icon']} {skill_data['name']}"
        btn_data = f"skill_toggle:{skill_key}"
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
        return msg
    except Exception:
        msg = await event.respond(text, buttons=buttons, parse_mode="html")
        return msg

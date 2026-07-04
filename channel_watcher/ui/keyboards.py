"""Inline keyboard builders for the Channel Watcher module.

Every callback data string uses the ``cw_`` prefix to avoid collisions
with other modules (trade journal uses ``tj_``, main bot uses bare
labels such as ``back_to_main``).
"""

from telethon import Button
from typing import Any, Dict, List, Optional


def main_menu_keyboard() -> List[List[Any]]:
    """Main menu for the Channel Watcher sub-module."""
    return [
        [Button.inline("➕ کانال جدید", "cw_new_monitor")],
        [Button.inline("📋 لیست مونیتورها", "cw_list_monitors")],
        [Button.inline("🔙 منوی اصلی", "back_to_main")],
    ]


def settings_keyboard(monitor: Dict[str, Any], sub_type: str) -> List[List[Any]]:
    """Settings panel keyboard for a single monitor.

    Paid users see interval, system prompt and importance filter
    controls; free users see lock icons for those features.
    """
    mid = monitor["id"]
    buttons: List[List[Any]] = []

    if sub_type == "paid":
        buttons.append([
            Button.inline(
                f"🕒 {monitor['interval_hours']}h",
                f"cw_interval_{mid}",
            )
        ])
    else:
        buttons.append([
            Button.inline("🔒 بازه تحلیل (اشتراک پولی)", "cw_upgrade_required"),
        ])

    if sub_type == "paid":
        prompt_status = "✅" if monitor.get("system_prompt") else "➕"
        buttons.append([
            Button.inline(f"🎯 علایق من {prompt_status}", f"cw_prompt_{mid}"),
        ])
    else:
        buttons.append([
            Button.inline("🔒 علایق من (اشتراک پولی)", "cw_upgrade_required"),
        ])

    if sub_type == "paid":
        imp_label = "🔴 فقط مهم" if monitor.get("importance_filter", "important") == "important" else "🔵 همه پیام‌ها"
        buttons.append([
            Button.inline(f"🔥 {imp_label}", f"cw_importance_{mid}"),
        ])
    else:
        buttons.append([
            Button.inline("🔒 فیلتر اهمیت (اشتراک پولی)", "cw_upgrade_required"),
        ])

    # Manual trigger — check the channel right now without waiting.
    # Paid-tier only: on-demand checks bypass the interval scheduling and
    # can be spammed by the user, so they're gated behind a subscription
    # like the other paid-only controls above.
    if sub_type == "paid":
        buttons.append([Button.inline("🔄 بررسی فوری", f"cw_check_now_{mid}")])
    else:
        buttons.append([Button.inline("🔒 بررسی فوری (اشتراک پولی)", "cw_upgrade_required")])

    # Insights row.
    buttons.append([
        Button.inline("📊 آمار کانال", f"cw_stats_{mid}"),
        Button.inline("📜 تحلیل‌های اخیر", f"cw_history_{mid}"),
    ])

    pause_text = "▶️ ادامه پایش" if monitor.get("is_paused") else "⏸ توقف موقت"
    buttons.append([Button.inline(pause_text, f"cw_toggle_pause_{mid}")])

    buttons.append([
        Button.inline("❌ حذف", f"cw_delete_{mid}"),
        Button.inline("🔙 لیست", "cw_list_monitors"),
    ])

    return buttons


def interval_presets_keyboard(monitor_id: int, current: int) -> List[List[Any]]:
    """Quick-pick interval presets (paid).  The active preset is ticked."""
    presets = [1, 2, 4, 6, 12, 24]
    row: List[Any] = []
    rows: List[List[Any]] = []
    for h in presets:
        label = ("✅ " if h == current else "") + (f"{h}h" if h < 24 else "۱ روز")
        row.append(Button.inline(label, f"cw_setint_{monitor_id}_{h}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Button.inline("✏️ عدد دلخواه", f"cw_interval_custom_{monitor_id}")])
    rows.append([Button.inline("🔙 برگشت به تنظیمات", f"cw_settings_{monitor_id}")])
    return rows


def history_keyboard(monitor_id: int) -> List[List[Any]]:
    return [[Button.inline("🔙 برگشت به تنظیمات", f"cw_settings_{monitor_id}")]]


def delivery_choice_keyboard() -> List[List[Any]]:
    return [
        [Button.inline("📨 ارسال در پیوی", "cw_delivery_pm")],
        [Button.inline("👥 ارسال در گروه", "cw_delivery_group")],
        [Button.inline("🔙 برگشت", "cw_back_main")],
    ]


def confirm_keyboard() -> List[List[Any]]:
    return [
        [Button.inline("✅ تایید و شروع", "cw_confirm_monitor")],
        [Button.inline("🔙 ویرایش", "cw_back_edit")],
        [Button.inline("❌ لغو", "cw_back_main")],
    ]


def delete_confirm_keyboard(monitor_id: int) -> List[List[Any]]:
    return [
        [Button.inline("✅ بله، حذف شود", f"cw_delete_confirm_{monitor_id}")],
        [Button.inline("🔙 خیر، برگشت", f"cw_settings_{monitor_id}")],
    ]


def ask_ai_keyboard(
    monitor_id: int,
    msg_id: Optional[int] = None,
    turn_count: int = 0,
    show_full_text: bool = False,
) -> List[List[Any]]:
    """Keyboard shown while a user is chatting with Ask AI about a post.

    ``msg_id`` is optional (kept for backward compatibility with the few
    call sites that only need the plain "exit" button, e.g. error
    replies) — when it's given, the full action row (clear / full text /
    exit) is rendered.
    """
    if msg_id is None:
        return [[Button.inline("🔙 خروج از حالت پرسش", f"cw_exit_ask_{monitor_id}")]]

    rows: List[List[Any]] = []

    action_row: List[Any] = []
    if turn_count > 0:
        action_row.append(Button.inline("🧹 پاک‌سازی گفتگو", f"cw_ask_clear_{monitor_id}_{msg_id}"))
    text_toggle = "🗒 پنهان کردن متن اصلی" if show_full_text else "🗒 نمایش متن کامل پست"
    action_row.append(Button.inline(text_toggle, f"cw_ask_fulltext_{monitor_id}_{msg_id}"))
    rows.append(action_row)

    rows.append([Button.inline("🔙 خروج از حالت پرسش", f"cw_exit_ask_{monitor_id}")])
    return rows


def ask_ai_suggestions(topics: Optional[List[str]] = None) -> List[tuple]:
    """Shared source of truth for the deterministic Ask AI quick-questions.

    Returns a list of ``(label, question)`` tuples. Kept as plain data
    (not tied to Telethon ``Button`` objects) so ``handlers/callbacks.py``
    can resolve a tapped suggestion's index back to its question text
    without importing UI widgets, and so the list only needs to be
    edited in one place.
    """
    suggestions = [
        ("🔍 تحلیل عمیق‌تر", "این پست را عمیق‌تر تحلیل کن و نکات پنهان را بگو."),
        ("⚠️ ریسک‌ها چیست؟", "ریسک‌ها یا نکات منفی احتمالی این پست چیست؟"),
        ("💡 نتیجه‌گیری عملی", "بر اساس این پست، چه اقدام عملی‌ای پیشنهاد می‌کنی؟"),
    ]
    if topics:
        suggestions.append(
            (f"🏷 بیشتر درباره «{topics[0]}»", f"درباره‌ی «{topics[0]}» در این پست بیشتر توضیح بده.")
        )
    return suggestions


def ask_ai_suggestions_keyboard(
    monitor_id: int, msg_id: int, topics: Optional[List[str]] = None
) -> List[List[Any]]:
    """One-tap follow-up question suggestions shown right after a post is
    opened in Ask AI mode, before the user has typed anything.

    Deterministic (no extra AI call): see :func:`ask_ai_suggestions`.
    Tapping a suggestion sends that exact question through the normal
    Ask AI pipeline via a short-lived callback (``cw_asksug_``), sparing
    the user from typing the most common questions by hand.
    """
    suggestions = ask_ai_suggestions(topics)

    rows: List[List[Any]] = []
    row: List[Any] = []
    for idx, (label, _question) in enumerate(suggestions):
        row.append(Button.inline(label, f"cw_asksug_{monitor_id}_{msg_id}_{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def analysis_card_keyboard_with_link(
    monitor_id: int, msg_id: int, channel_username: str, post_id: int
) -> List[List[Any]]:
    return [
        [
            Button.inline("🤖 سوال از هوش مصنوعی", f"cw_ask_{monitor_id}_{msg_id}"),
            Button.url("📎 مشاهده پست", f"https://t.me/{channel_username}/{post_id}"),
        ],
    ]


def back_keyboard(target: str) -> List[List[Any]]:
    return [[Button.inline("🔙 برگشت", target)]]


def empty_monitors_keyboard() -> List[List[Any]]:
    return [
        [Button.inline("➕ کانال جدید", "cw_new_monitor")],
        [Button.inline("🔙 منوی اصلی", "back_to_main")],
    ]


def saved_monitor_keyboard(monitor_id: int) -> List[List[Any]]:
    return [
        [Button.inline("⚙️ تنظیمات", f"cw_settings_{monitor_id}")],
        [Button.inline("📋 لیست مونیتورها", "cw_list_monitors")],
        [Button.inline("🔙 منوی اصلی", "back_to_main")],
    ]


def stats_back_keyboard(monitor_id: int) -> List[List[Any]]:
    return [[Button.inline("🔙 برگشت به تنظیمات", f"cw_settings_{monitor_id}")]]

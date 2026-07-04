"""Message formatters for the Channel Watcher module.

All functions return Telegram-HTML-safe strings.  Persian text uses
``U+200F`` (Right-to-Left Mark) where necessary to prevent Telegraph's
bidirectional renderer from flipping lines that start with non-Persian
characters.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
    tz_tehran = ZoneInfo("Asia/Tehran")
except ImportError:
    from datetime import timezone, timedelta
    tz_tehran = timezone(timedelta(hours=3, minutes=30))


def _ensure_rtl(text: str) -> str:
    """Prepend ``U+200F`` to lines that contain RTL script characters.

    Lines that start with an HTML tag (``<...>``) are left unchanged
    because inserting ``U+200F`` before the opening ``<`` can confuse
    Telegram's HTML parser.
    """
    if not text:
        return text
    lines = text.split("\n")
    result: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            result.append(line)
            continue
        if any("\u0600" <= c <= "\u06FF" or "\uFB50" <= c <= "\uFDFF" for c in stripped):
            if line.lstrip().startswith("<") and ">" in line:
                result.append(line)
                continue
            if not line.startswith("\u200F"):
                result.append("\u200F" + line)
            else:
                result.append(line)
        else:
            result.append(line)
    return "\n".join(result)


def _fmt_time(iso_str: str) -> str:
    """Convert ISO timestamp to Tehran time string (YYYY/MM/DD HH:MM)."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_tehran)
        else:
            dt = dt.astimezone(tz_tehran)
        return dt.strftime("%Y/%m/%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def _importance_badge(importance: str) -> str:
    return {"high": "🔴 بالا", "medium": "🟡 متوسط", "low": "🟢 پایین"}.get(importance, "⚪ نامشخص")


def format_analysis_card(
    monitor_title: str,
    channel_username: str,
    analysis: Dict[str, Any],
    index: Optional[int] = None,
    total: Optional[int] = None,
    post_id: Optional[int] = None,
) -> str:
    """Build a rich analysis card showing the AI-generated summary.

    Includes summary, key points, topic tags, importance badge, and a
    link to the original message.
    """
    summary = (analysis.get("summary") or "").strip()
    key_points: List[str] = analysis.get("key_points", [])
    topics: List[str] = analysis.get("topics", [])
    importance = analysis.get("importance", "medium")
    analyzed_at = analysis.get("analyzed_at", "")

    lines: List[str] = []
    lines.append(f"📡 <b>{monitor_title}</b>")
    lines.append(f"└ @{channel_username}")
    lines.append("━" * 25 + "\n")

    if summary:
        lines.append(f"📝 <b>خلاصه:</b>")
        lines.append(summary + "\n")

    if key_points:
        lines.append("📌 <b>نکات کلیدی:</b>")
        for p in key_points:
            lines.append(f"• {p}")
        lines.append("")

    if topics:
        safe_topics = ", ".join(topics[:5])
        lines.append(f"🏷 <b>موضوعات:</b> {safe_topics}")

    lines.append(f"🔥 <b>اهمیت:</b> {_importance_badge(importance)}")

    if analyzed_at:
        lines.append(f"⏰ {_fmt_time(analyzed_at)}")

    if post_id:
        lines.append(f"🔗 <a href=\"https://t.me/{channel_username}/{post_id}\">مشاهده پست اصلی</a>")

    if index is not None and total is not None and total > 1:
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━\n📄 پیام {index} از {total}")

    return _ensure_rtl("\n".join(lines))


def format_monitor_list_item(monitor: Dict[str, Any]) -> str:
    """Single-line summary for a monitor in the list view."""
    status_emoji = "⏸" if monitor.get("is_paused") else "📡"
    delivery_icon = "📨" if monitor.get("delivery_method") == "pm" else "👥"
    interval = monitor.get("interval_hours", 4)
    interval_str = f"هر {interval}h" if interval < 24 else f"هر {interval // 24}d"

    return (
        f"{status_emoji} <b>{monitor.get('channel_title', '?')}</b>\n"
        f"└ @{monitor.get('channel_username', '?')} | "
        f"{interval_str} | {delivery_icon}"
    )


def format_stats_text(
    monitor_title: str,
    stats: List[Dict[str, Any]],
    hourly_dist: List[int],
) -> str:
    """Build a statistics card for the past 7 days.

    Shows average daily posts, busiest hour (ASCII bar chart), hourly
    distribution (3-hour buckets), and daily post counts.
    """
    total_posts = sum(s.get("total_posts", 0) for s in stats)
    analyzed_posts = sum(s.get("analyzed_posts", 0) for s in stats)

    header = f"📊 <b>آمار کانال {monitor_title}</b>\n" + "━" * 22

    if not stats or total_posts == 0:
        return _ensure_rtl(
            f"{header}\n\n"
            "هنوز داده‌ای برای نمایش ثبت نشده است.\n"
            "پس از اولین بررسی کانال، آمار اینجا نمایش داده می‌شود."
        )

    avg = total_posts / max(len(stats), 1)
    peak_hour = max(range(24), key=lambda i: hourly_dist[i])
    peak_count = hourly_dist[peak_hour]
    max_bar = max(max(hourly_dist), 1)

    hourly_lines: List[str] = []
    for hour in range(0, 24, 3):
        count = hourly_dist[hour]
        bar_len = int(count / max_bar * 10) if max_bar > 0 else 0
        bar = "█" * bar_len if bar_len else "▏"
        hourly_lines.append(f"<code>{hour:02d}:00</code> {bar} {count}")

    lines: List[str] = [header, ""]
    lines.append("📅 <b>خلاصه ۷ روز اخیر</b>")
    lines.append(f"• کل پست‌ها: {total_posts}")
    lines.append(f"• میانگین روزانه: {avg:.1f}")
    lines.append(f"• تحلیل‌شده: {analyzed_posts}")
    lines.append(f"• پرترافیک‌ترین ساعت: {peak_hour:02d}:00 ({peak_count} پست)\n")
    lines.append("📈 <b>توزیع ساعتی</b>")
    lines.append("\n".join(hourly_lines) + "\n")
    lines.append("🗓 <b>پست‌های روزانه</b>")

    for s in stats:
        count = s.get("total_posts", 0)
        bar = "█" * min(count, 15) if count else "▏"
        date_str = s.get("date", "?")
        lines.append(f"<code>{date_str}</code> {bar} ({count})")

    return _ensure_rtl("\n".join(lines))


def format_history_text(monitor_title: str, analyses: List[Dict[str, Any]]) -> str:
    """Compact list of the most recent analyses for a monitor.

    ``analyses`` are raw ``analyzed_messages`` rows (``key_points`` and
    ``topics`` stored as JSON strings), newest-first.
    """
    header = f"📜 <b>تحلیل‌های اخیر — {monitor_title}</b>\n" + "━" * 22

    if not analyses:
        return _ensure_rtl(
            f"{header}\n\n"
            "هنوز تحلیلی برای این کانال ثبت نشده است.\n"
            "می‌توانید با «🔄 بررسی فوری» همین حالا کانال را بررسی کنید."
        )

    lines: List[str] = [header, ""]
    for a in analyses:
        badge = _importance_badge(a.get("importance", "medium"))
        when = _fmt_time(a.get("analyzed_at", ""))
        summary = (a.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 140:
            summary = summary[:137] + "…"
        lines.append(f"{badge} • <i>{when}</i>")
        lines.append(f"{summary}\n")

    return _ensure_rtl("\n".join(lines))


def format_settings_text(monitor: Dict[str, Any], next_check: str) -> str:
    """Settings panel header text."""
    is_paused = monitor.get("is_paused", False)
    status_emoji = "⏸" if is_paused else "✅"
    status_text = "متوقف" if is_paused else "فعال"
    interval = monitor.get("interval_hours", 4)
    importance = monitor.get("importance_filter", "important")
    delivery = monitor.get("delivery_method", "pm")

    interval_str = f"هر {interval}h" if interval < 24 else f"هر {interval // 24}d"
    imp_str = "🔴 فقط مهم" if importance == "important" else "🔵 همه"
    del_str = "📨 پیوی" if delivery == "pm" else "👥 گروه"
    prompt_str = "✅ تنظیم‌شده" if monitor.get("system_prompt") else "➖ تنظیم‌نشده"

    return _ensure_rtl(
        f"⚙️ <b>تنظیمات پایش</b>\n\n"
        f"📡 <b>{monitor.get('channel_title', '?')}</b>\n"
        f"└ @{monitor.get('channel_username', '?')}\n\n"
        f"⏱ وضعیت: {status_emoji} {status_text}\n"
        f"🕒 بازه: {interval_str}\n"
        f"🔥 فیلتر: {imp_str}\n"
        f"📨 تحویل: {del_str}\n"
        f"🎯 علایق: {prompt_str}\n\n"
        f"⏰ تحلیل بعدی: {next_check}"
    )


# ── Ask AI screen ──────────────────────────────────────────────────────

# Placeholder shown immediately after the user asks a question, while the
# model call is in flight (edited in-place with the real answer once it
# arrives). Paired with Telethon's native ``client.action(chat, "typing")``
# indicator in the handler, so the user gets two layers of feedback
# instead of a silent multi-second wait.
THINKING_TEXT = "🤖 <i>در حال فکر کردن…</i>"


def format_ask_ai_intro(analysis: Dict[str, Any], show_full_text: bool = False) -> str:
    """Header shown when a user opens Ask AI for a specific post.

    Shows the importance badge + a short preview of what the post is
    about, so the user has context before typing a question — without
    needing to scroll back to the original card. If ``show_full_text``
    is set, the full original message text is shown instead of the
    summary (used by the "🗒 نمایش متن کامل پست" toggle).
    """
    importance = analysis.get("importance", "medium")
    badge = _importance_badge(importance)
    summary = (analysis.get("summary") or "").strip()

    lines = [
        "🤖 <b>حالت پرسش‌وپاسخ فعال شد</b>",
        f"{badge}",
        "",
    ]
    if show_full_text:
        original = (analysis.get("message_text") or "").strip()
        lines.append("📄 <b>متن کامل پست:</b>")
        lines.append(original if original else "<i>متنی ثبت نشده است.</i>")
    else:
        lines.append("📝 <b>خلاصه پست:</b>")
        lines.append(summary if summary else "<i>خلاصه‌ای ثبت نشده است.</i>")

    lines.append("")
    lines.append("💬 سوال خودتان را تایپ کنید، یا یکی از پیشنهادهای زیر را بزنید:")
    return _ensure_rtl("\n".join(lines))


def format_ask_ai_answer(
    question: str, answer: str, turn_count: int, max_turns: int
) -> str:
    """Format a single Ask AI turn (question + answer) as one message.

    Echoing the question back keeps the conversation readable in a
    channel-style chat where the assistant's replies are separate
    messages from the user's own — without this, a reply appearing 5
    turns later is disorienting without seeing what it's answering.
    """
    remaining = max(0, max_turns - turn_count)
    footer = f"💬 پرسش {turn_count} از {max_turns}"
    if remaining <= 2 and remaining > 0:
        footer += f" — {remaining} سوال دیگر تا پاک‌سازی خودکار"
    elif remaining == 0:
        footer += " — به سقف گفتگو رسیدید؛ برای ادامه «🧹 پاک‌سازی گفتگو» را بزنید"

    return _ensure_rtl(
        f"🙋 <i>{question}</i>\n"
        f"{'─' * 20}\n"
        f"🤖 {answer}\n\n"
        f"<code>{footer}</code>"
    )

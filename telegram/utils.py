import re
import html
from datetime import datetime, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone
    tz_tehran = timezone(timedelta(hours=3, minutes=30))
else:
    tz_tehran = ZoneInfo("Asia/Tehran")


def clean_number_string(text: str) -> str:
    """Converts Persian/Arabic digits to English digits and removes non-numeric clutter except minus sign."""
    text_str = str(text)
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    # NOTE: the 7th character MUST be the Arabic-Indic seven ٧ (U+0667), not the
    # Persian seven ۷ (U+06F7). A previous copy used U+06F7 here, which left a
    # real Arabic ٧ untranslated (e.g. "١٢٣٤٥٦٧٨٩٠" -> "123456٧890").
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    translation_table = str.maketrans(persian_digits + arabic_digits, english_digits + english_digits)
    translated = text_str.translate(translation_table)
    cleaned = "".join(c for c in translated if c.isdigit() or c == '-')
    return cleaned


def get_time_until_reset() -> str:
    """Calculates the remaining hours and minutes until the next 12-hour reset (11:00 AM / 11:00 PM Iran Time)."""
    now = datetime.now(tz_tehran)
    c1 = now.replace(hour=11, minute=0, second=0, microsecond=0)
    c2 = now.replace(hour=23, minute=0, second=0, microsecond=0)
    c3 = (now + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)

    next_reset = None
    for c in [c1, c2, c3]:
        if c > now:
            next_reset = c
            break

    diff = next_reset - now
    hours, remainder = divmod(int(diff.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours} ساعت و {minutes} دقیقه"


def get_last_reset_time() -> datetime:
    """Calculates the datetime of the most recent 12-hour reset point."""
    now = datetime.now(tz_tehran)
    c1 = now.replace(hour=11, minute=0, second=0, microsecond=0)
    c2 = now.replace(hour=23, minute=0, second=0, microsecond=0)
    c3 = (now - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    c4 = (now - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)

    past_resets = [c for c in [c1, c2, c3, c4] if c <= now]
    return max(past_resets)


def make_progress_bar(current: int, maximum: int, length: int = 10) -> str:
    """Generates a visual progress bar string."""
    percent = min(100, max(0, int((current / maximum) * 100)))
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {percent}%"


def safe_html_truncate(text: str, max_length: int = 1024) -> str:
    """
    Safely truncate HTML text for use as a Telegram caption.
    Strips all HTML tags and decodes entities to prevent broken-HTML API errors.
    """
    clean = re.sub(r'<[^>]+>', '', text)
    clean = html.unescape(clean)
    clean = ' '.join(clean.split())
    if len(clean) <= max_length:
        return clean
    return clean[:max_length - 3] + '...'


# ── Window Panel UI Helpers ──────────────────────────────────────────

_DIVIDER = "━" * 22


def _format_window_entry(win: dict) -> str:
    """Format a single window as a list item with icon."""
    icon = "🟢" if win["is_active"] else "⚪️"
    marker = "  ←" if win["is_active"] else ""
    return f"\u200f{icon}  <b>{win['title']}</b>{marker}"

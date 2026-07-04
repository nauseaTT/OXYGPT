import asyncio
import re
from typing import Optional, Any

SPECIAL_CHARS = r'_*[]()~`>#+-=|{}.!\\'


def markdown_escape(text: str) -> str:
    result = []
    for ch in str(text):
        if ch in SPECIAL_CHARS:
            result.append('\\' + ch)
        else:
            result.append(ch)
    return ''.join(result)


def clean_number_string(text: str) -> str:
    if not text:
        return ""
    translation_table = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return text.translate(translation_table).strip().replace(',', '')


def is_valid_number(text: str) -> bool:
    try:
        cleaned = clean_number_string(text)
        float(cleaned)
        return True
    except (ValueError, AttributeError):
        return False


def parse_number(text: str) -> Optional[float]:
    try:
        cleaned = clean_number_string(text)
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def format_number(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    if value == int(value):
        return str(int(value))
    return f"{value:,.{decimals}f}"


def truncate_text(text: str, max_len: int = 20) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def get_callback_parts(data) -> list:
    """Safely decode callback data (bytes or str) and split by ':'"""
    if data is None:
        return []
    try:
        s = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        return []
    return s.split(":")


def parse_callback_int(data, idx: int = 1) -> Optional[int]:
    parts = get_callback_parts(data)
    if len(parts) <= idx:
        return None
    try:
        return int(parts[idx])
    except Exception:
        return None


def parse_callback_str(data, idx: int = 1) -> Optional[str]:
    parts = get_callback_parts(data)
    if len(parts) <= idx:
        return None
    return parts[idx]


async def auto_delete_message(client: Any, chat_id: int, msg_id: int, delay: int = 60) -> None:
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass

"""Design system constants for Modern Glassmorphism UI."""


class Colors:
    PRIMARY = "#1E3A8A"
    ACCENT = "#10B981"
    DANGER = "#F43F5E"
    WARNING = "#F59E0B"
    INFO = "#3B82F6"


class Box:
    TL = "╭"
    TR = "╮"
    BL = "╰"
    BR = "╯"
    H = "─"
    V = "│"
    T = "├"
    B = "┤"


def glass_panel(title: str, content: str, width: int = 32) -> str:
    lines = []
    lines.append(f"{Box.TL}{Box.H * width}{Box.TR}")
    lines.append(f"{Box.V} {title.center(width - 2)} {Box.V}")
    lines.append(f"{Box.T}{Box.H * width}{Box.B}")
    for line in content.split("\n"):
        lines.append(f"{Box.V} {line.ljust(width - 2)} {Box.V}")
    lines.append(f"{Box.BL}{Box.H * width}{Box.BR}")
    return "\n".join(lines)


def section_divider(title: str = "") -> str:
    if title:
        return f"━━━ {title} ━━━"
    return "━" * 28


def progress_bar(filled: int, total: int, length: int = 10) -> str:
    ratio = filled / total if total > 0 else 0
    filled_len = round(ratio * length)
    empty_len = length - filled_len
    return f"{'█' * filled_len}{'░' * empty_len}"


class Icons:
    WIN = "🟢"
    LOSS = "🔴"
    BREAKEVEN = "⚪"
    LONG = "📈"
    SHORT = "📉"
    CHECK = "✅"
    CROSS = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    STAR = "⭐"
    FIRE = "🔥"
    TROPHY = "🏆"
    CHART = "📊"
    MONEY = "💰"
    CALENDAR = "📅"
    CLOCK = "🕐"
    SYMBOL = "🏷️"
    TEMPLATE = "📋"
    SETTINGS = "⚙️"
    PHOTO = "🖼"
    NEW = "➕"
    DELETE = "🗑"
    EDIT = "✏️"
    BACK = "🔙"
    SEARCH = "🔍"
    EXPORT = "📤"
    NOTIFICATION = "🔔"


class Labels:
    JOURNAL_TITLE = "ژورنال معاملات"
    NEW_TRADE = "معامله جدید"
    TEMPLATES = "قالب‌ها"
    SYMBOLS = "نمادها"
    SETTINGS = "تنظیمات"
    STATS = "آمار"
    HISTORY = "تاریخچه"
    EXIT = "خروج"
    SYMBOL = "نماد"
    DIRECTION = "جهت"
    VOLUME = "حجم"
    ENTRY_PRICE = "قیمت ورود"
    STOPLOSS = "حد ضرر"
    EXIT_PRICE = "قیمت خروج"
    TRADE_DATE = "تاریخ معامله"
    TOTAL_TRADES = "کل معاملات"
    WINS = "بردها"
    LOSSES = "باختها"
    WIN_RATE = "نرخ برد"
    TOTAL_PNL = "سود/زیان کل"
    PROFIT_FACTOR = "فاکتور سود"
    BEST_TRADE = "بهترین معامله"
    WORST_TRADE = "بدترین معامله"
    CONFIRM = "تأیید"
    CANCEL = "لغو"
    SAVE = "ذخیره"
    DELETE = "حذف"
    EDIT = "ویرایش"
    BACK = "بازگشت"
    NEXT = "بعدی"
    PREVIOUS = "قبلی"
    SESSION_EXPIRED = "جلسه منقضی شده"
    NO_TRADES = "هیچ معامله‌ای ثبت نشده"
    TRADE_SAVED = "معامله با موفقیت ذخیره شد"
    TRADE_DELETED = "معامله حذف شد"

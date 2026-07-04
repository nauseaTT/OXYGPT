import logging
from typing import Any, Dict, List, Optional, Tuple

from ..utils import parse_number

logger = logging.getLogger(__name__)


def calculate_pnl(direction: str, entry_price: float, exit_price: float,
                  volume: float) -> Optional[float]:
    if entry_price is None or exit_price is None or volume is None:
        return None
    if direction == "LONG":
        return (exit_price - entry_price) * volume
    elif direction == "SHORT":
        return (entry_price - exit_price) * volume
    return None


def calculate_risk(direction: str, entry_price: float, stoploss: float,
                   volume: float) -> Optional[float]:
    if entry_price is None or stoploss is None or volume is None:
        return None
    if direction == "LONG":
        return (entry_price - stoploss) * volume
    elif direction == "SHORT":
        return (stoploss - entry_price) * volume
    return None


def calculate_rr_ratio(pnl: Optional[float], risk: Optional[float]) -> float:
    if pnl is None or risk is None or risk == 0:
        return 0.0
    return abs(pnl) / abs(risk)


def calculate_pip_diff(direction: str, entry_price: float, exit_price: float) -> Optional[float]:
    if entry_price is None or exit_price is None:
        return None
    diff = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    return diff * 10000 if diff and abs(diff) < 1 else diff * 100 if diff and abs(diff) < 10 else diff


def compute_trade_calculations(filled: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], float, Optional[float]]:
    direction = filled.get("direction")
    entry = parse_number(str(filled.get("entry_price", ""))) if filled.get("entry_price") else None
    exit_p = parse_number(str(filled.get("exit_price", ""))) if filled.get("exit_price") else None
    sl = parse_number(str(filled.get("stoploss", ""))) if filled.get("stoploss") else None
    vol = parse_number(str(filled.get("volume", ""))) if filled.get("volume") else None

    pnl = calculate_pnl(direction, entry, exit_p, vol) if all([direction, entry, exit_p, vol]) else None
    risk = calculate_risk(direction, entry, sl, vol) if all([direction, entry, sl, vol]) else None
    rr = calculate_rr_ratio(pnl, risk)
    pip_diff = calculate_pip_diff(direction, entry, exit_p) if all([direction, entry, exit_p]) else None

    return pnl, risk, rr, pip_diff


def validate_mandatory_fields(filled: Dict[str, Any]) -> List[str]:
    missing = []
    required = ["symbol", "direction", "volume", "entry_price", "stoploss", "exit_price", "trade_date"]
    for key in required:
        val = filled.get(key)
        if val is None or val == "":
            from ..ui.formatter import MANDATORY_FIELDS
            missing.append(MANDATORY_FIELDS.get(key, key))
    return missing


def validate_trade_values(filled: Dict[str, Any]) -> List[str]:
    errors = []
    direction = filled.get("direction")
    entry = filled.get("entry_price")
    sl = filled.get("stoploss")
    exit_p = filled.get("exit_price")
    vol = filled.get("volume")

    if vol is not None:
        try:
            v = float(str(vol).replace(",", ""))
            if v <= 0:
                errors.append("حجم باید بزرگتر از صفر باشد")
        except (ValueError, TypeError):
            errors.append("حجم معتبر نیست")

    if entry is not None:
        try:
            e = float(str(entry).replace(",", ""))
            if e <= 0:
                errors.append("قیمت ورود باید بزرگتر از صفر باشد")
        except (ValueError, TypeError):
            errors.append("قیمت ورود معتبر نیست")

    if sl is not None:
        try:
            s = float(str(sl).replace(",", ""))
            if s <= 0:
                errors.append("حد ضرر باید بزرگتر از صفر باشد")
        except (ValueError, TypeError):
            errors.append("حد ضرر معتبر نیست")

    if exit_p is not None:
        try:
            x = float(str(exit_p).replace(",", ""))
            if x <= 0:
                errors.append("قیمت خروج باید بزرگتر از صفر باشد")
        except (ValueError, TypeError):
            errors.append("قیمت خروج معتبر نیست")

    if direction and entry and sl:
        try:
            e = float(str(entry).replace(",", ""))
            s = float(str(sl).replace(",", ""))
            if direction == "LONG" and s >= e:
                errors.append("در پوزیشن LONG، حد ضرر باید پایین‌تر از قیمت ورود باشد")
            elif direction == "SHORT" and s <= e:
                errors.append("در پوزیشن SHORT، حد ضرر باید بالاتر از قیمت ورود باشد")
        except (ValueError, TypeError):
            pass

    return errors


def format_pnl(pnl: Optional[float]) -> str:
    if pnl is None:
        return "N/A"
    return f"{pnl:+.2f}"


def format_pnl_percent(pnl: Optional[float], margin: Optional[float] = None) -> str:
    if pnl is None:
        return ""
    if margin and margin > 0:
        pct = (pnl / margin) * 100
        return f" ({pct:+.2f}%)"
    return ""


def format_stats_text_farsi(stats: Dict[str, Any]) -> str:
    total = stats.get("total") or 0
    wins = stats.get("wins") or 0
    losses = stats.get("losses") or 0
    total_pnl = stats.get("total_pnl") or 0
    win_rate = stats.get("win_rate") or 0
    avg_win = stats.get("avg_win") or 0
    avg_loss = stats.get("avg_loss") or 0
    profit_factor = stats.get("profit_factor") or 0
    best_trade = stats.get("best_trade") or 0
    worst_trade = stats.get("worst_trade") or 0
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

    lines = []
    lines.append("╭─────────────────────┐")
    lines.append("│  📊 <b>آمار کلی معاملات</b>  │")
    lines.append("╰─────────────────────╯")
    lines.append("")
    lines.append(f"  🔢 کل معاملات: <b>{total}</b>")
    lines.append(f"  ✅ بردها: <b>{wins}</b>  ❌ باختها: <b>{losses}</b>")
    lines.append(f"  📈 نرخ برد: <b>{win_rate:.1f}%</b>")
    lines.append(f"  {pnl_emoji} سود/زیان کل: <b>{total_pnl:.2f}</b>")
    lines.append("")
    if wins > 0:
        lines.append(f"  🟢 میانگین سود: <b>{avg_win:.2f}</b>")
    if losses > 0:
        lines.append(f"  🔴 میانگین ضرر: <b>{avg_loss:.2f}</b>")
    if profit_factor > 0:
        lines.append(f"  📊 نسبت سود/ضرر: <b>{profit_factor:.2f}</b>")
    if best_trade > 0:
        lines.append(f"  🌟 بهترین معامله: <b>+{best_trade:.2f}</b>")
    if worst_trade < 0:
        lines.append(f"  💥 بدترین معامله: <b>{worst_trade:.2f}</b>")

    return "\n".join(lines)


def format_glass_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get("total") or 0
    wins = stats.get("wins") or 0
    losses = stats.get("losses") or 0
    total_pnl = stats.get("total_pnl") or 0
    win_rate = stats.get("win_rate") or 0
    avg_win = stats.get("avg_win") or 0
    avg_loss = stats.get("avg_loss") or 0
    profit_factor = stats.get("profit_factor") or 0
    best_trade = stats.get("best_trade") or 0
    worst_trade = stats.get("worst_trade") or 0
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

    B_H = "┌"
    B_V = "│"
    B_F = "└"
    H = "─"

    lines = []
    lines.append("📊 <b>داشبورد آمار شیشه‌ای</b>")
    lines.append(H * 25)
    lines.append("")
    lines.append(B_H + H * 3 + " کلیات " + H * 3)
    lines.append(B_V + "  🔢 کل معاملات:    <b>" + str(total) + "</b>")
    lines.append(B_V + "  ✅ بردها: <b>" + str(wins) + "</b>  ❌ باختها: <b>" + str(losses) + "</b>")
    lines.append(B_V + "  📈 نرخ برد:      <b>" + f"{win_rate:.1f}%" + "</b>")
    lines.append(B_V + "  " + pnl_emoji + " سود/زیان کل: <b>" + f"{total_pnl:.2f}" + "</b>")
    lines.append(B_F + H * 28)
    lines.append("")

    if wins > 0 or losses > 0:
        lines.append(B_H + H * 3 + " جزئیات " + H * 3)
        if wins > 0:
            lines.append(B_V + "  🟢 میانگین سود:   <b>" + f"{avg_win:.2f}" + "</b>")
        if losses > 0:
            lines.append(B_V + "  🔴 میانگین ضرر:   <b>" + f"{avg_loss:.2f}" + "</b>")
        if profit_factor > 0:
            lines.append(B_V + "  📊 نسبت سود/ضرر: <b>" + f"{profit_factor:.2f}" + "</b>")
        if best_trade > 0:
            lines.append(B_V + "  🌟 بهترین:       <b>+" + f"{best_trade:.2f}" + "</b>")
        if worst_trade < 0:
            lines.append(B_V + "  💥 بدترین:       <b>" + f"{worst_trade:.2f}" + "</b>")
        lines.append(B_F + H * 28)

    return "\n".join(lines)


def get_journal_hashtags(filled: Dict[str, Any], pnl: Optional[float] = None) -> str:
    tags = []
    symbol = filled.get("symbol", "")
    if symbol:
        tags.append(f"#{symbol}")
    direction = filled.get("direction", "")
    if direction == "LONG":
        tags.append("#خرید")
    elif direction == "SHORT":
        tags.append("#فروش")
    if pnl is not None:
        if pnl > 0:
            tags.append("#سود")
        elif pnl < 0:
            tags.append("#ضرر")
        else:
            tags.append("#بریک_ایون")
    strategy = filled.get("strategy", "")
    if strategy:
        clean = strategy.strip().replace(" ", "_")
        tags.append(f"#{clean}")
    tags.append("#ژورنال_معاملاتی")
    return "\n".join(tags)

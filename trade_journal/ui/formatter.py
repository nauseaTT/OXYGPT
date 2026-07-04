import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..utils import format_number
from .design_system import (
    glass_panel, section_divider, progress_bar,
    Icons, Labels, Box, Colors
)

logger = logging.getLogger(__name__)

MANDATORY_FIELDS = {
    "symbol": "نماد",
    "direction": "جهت",
    "volume": "حجم",
    "entry_price": "قیمت ورود",
    "stoploss": "حد ضرر",
    "exit_price": "قیمت خروج",
    "trade_date": "تاریخ معامله",
}

MANDATORY_KEYS = list(MANDATORY_FIELDS.keys())

SECTION_HEADERS = {
    "details": "📌 جزئیات معامله",
    "analysis": "🧠 تحلیل و دلایل ورود",
    "risk": "⚙️ مدیریت ریسک و سرمایه",
    "psychology": "🧘 وضعیت روانی و ذهنی",
    "result": "📈 نتیجه معامله",
    "review": "🔍 بازبینی و تحلیل نهایی",
    "attachments": "📎 پیوست‌ها",
    "custom": "📋 سایر",
}

SECTION_ORDER = ["details", "analysis", "risk", "psychology", "result", "review", "attachments", "custom"]


def build_status_text(filled: Dict[str, Any], custom_fields: List[Dict[str, Any]] = None) -> str:
    filled_count = sum(1 for k in MANDATORY_KEYS if filled.get(k) is not None and filled[k] != "")
    total = len(MANDATORY_KEYS)

    lines = []
    lines.append(f"{Box.TL}{Box.H * 30}{Box.TR}")
    lines.append(f"{Box.V}  📊 <b>پنل معامله زنده</b>  {Box.V}")
    lines.append(f"{Box.BL}{Box.H * 30}{Box.BR}")
    lines.append("")

    bar = progress_bar(filled_count, total)
    lines.append(f"  <code>{bar}</code> <b>{filled_count}/{total}</b>")
    lines.append("")

    for key, label in MANDATORY_FIELDS.items():
        val = filled.get(key)
        if val is not None and val != "":
            if key == "direction":
                arrow = Icons.LONG if val == "LONG" else Icons.SHORT
                lines.append(f"  {Icons.CHECK} <b>{label}:</b> {arrow} <code>{val}</code>")
            else:
                lines.append(f"  {Icons.CHECK} <b>{label}:</b> <code>{val}</code>")
        else:
            lines.append(f"  {Icons.WARNING} <b>{label}:</b> <i>وارد نشده</i>")

    if custom_fields:
        sections = {}
        for cf in custom_fields:
            sec = cf.get("field_section", "custom")
            if sec not in sections:
                sections[sec] = []
            sections[sec].append(cf)

        lines.append("")
        for sec_name in SECTION_ORDER:
            if sec_name in sections and sec_name != "custom":
                sec_fields = sections[sec_name]
                sec_header = SECTION_HEADERS.get(sec_name, sec_name)
                has_vals = any(filled.get(cf["field_key"]) for cf in sec_fields)
                if not has_vals:
                    continue
                lines.append(f"━━━ <b>{sec_header}</b> ━━━")
                for cf in sec_fields:
                    val = filled.get(cf["field_key"])
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"  {icon} <b>{cf['field_label']}</b>")
                    elif cf["field_type"] == "choice":
                        if val:
                            lines.append(f"  🎲 <b>{cf['field_label']}:</b> <code>{val}</code>")
                        else:
                            lines.append(f"  ⏳ <b>{cf['field_label']}:</b> <i>وارد نشده</i>")
                    else:
                        if val:
                            preview = str(val)[:30]
                            lines.append(f"  📝 <b>{cf['field_label']}:</b> <code>{preview}</code>")
                        else:
                            lines.append(f"  ⏳ <b>{cf['field_label']}:</b> <i>وارد نشده</i>")
                lines.append("")

        custom_fields_only = sections.get("custom", [])
        if custom_fields_only:
            has_vals = any(filled.get(cf["field_key"]) for cf in custom_fields_only)
            if has_vals:
                lines.append(f"━━━ <b>{SECTION_HEADERS['custom']}</b> ━━━")
                for cf in custom_fields_only:
                    val = filled.get(cf["field_key"])
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"  {icon} <b>{cf['field_label']}</b>")
                    elif cf["field_type"] == "choice":
                        if val:
                            lines.append(f"  🎲 <b>{cf['field_label']}:</b> <code>{val}</code>")
                        else:
                            lines.append(f"  ⏳ <b>{cf['field_label']}:</b> <i>وارد نشده</i>")
                    else:
                        if val:
                            preview = str(val)[:30]
                            lines.append(f"  📝 <b>{cf['field_label']}:</b> <code>{preview}</code>")
                        else:
                            lines.append(f"  ⏳ <b>{cf['field_label']}:</b> <i>وارد نشده</i>")

    return "\n".join(lines)


def generate_journal_text(filled: Dict[str, Any], pnl: Optional[float] = None,
                          risk: Optional[float] = None,
                          rr_ratio: Optional[float] = None,
                          pip_diff: Optional[float] = None,
                          multiple_risk: Optional[float] = None,
                          custom_fields: List[Dict[str, Any]] = None,
                          journal_number: int = 1) -> str:
    lines = []
    direction = filled.get("direction", "N/A")
    symbol = filled.get("symbol", "N/A")
    direction_emoji = Icons.LONG if direction == "LONG" else Icons.SHORT

    trade_date = filled.get("trade_date", "")
    
    lines.append(f"{Box.TL}{Box.H * 30}{Box.TR}")
    lines.append(f"{Box.V}  📊 ژورنال معامله #{journal_number:03d}  {Box.V}")
    lines.append(f"{Box.BL}{Box.H * 30}{Box.BR}")
    if trade_date:
        lines.append(f"  📅 {trade_date}")
    lines.append("")
    lines.append(section_divider())
    lines.append("")

    lines.append(f"  {direction_emoji} <b>{symbol}</b> — {direction}")
    if filled.get("volume"):
        lines.append(f"  📊 <b>{Labels.VOLUME}:</b> {filled['volume']}")
    if filled.get("entry_price"):
        lines.append(f"  ▶ <b>{Labels.ENTRY_PRICE}:</b> <code>{filled['entry_price']}</code>")
    if filled.get("stoploss"):
        lines.append(f"  ⚠️ <b>{Labels.STOPLOSS}:</b> <code>{filled['stoploss']}</code>")
    if filled.get("exit_price"):
        lines.append(f"  ◀ <b>{Labels.EXIT_PRICE}:</b> <code>{filled['exit_price']}</code>")

    lines.append("")
    lines.append(section_divider())
    lines.append("")

    if pnl is not None:
        pnl_icon = Icons.WIN if pnl >= 0 else Icons.LOSS
        result_text = "سود" if pnl > 0 else "ضرر" if pnl < 0 else "بریک‌ایون"
        lines.append(f"  {pnl_icon} <b>{result_text}:</b> <code>{format_number(pnl)}</code>")
    if pip_diff is not None:
        pip_sign = "+" if pip_diff >= 0 else ""
        lines.append(f"  📉 <b>پیپ:</b> <code>{pip_sign}{pip_diff:.1f}</code>")
    if multiple_risk is not None:
        mr_sign = "+" if multiple_risk >= 0 else ""
        lines.append(f"  🎯 <b>R Multiple:</b> <code>{mr_sign}{multiple_risk:.2f}R</code>")
    elif rr_ratio is not None and rr_ratio > 0:
        lines.append(f"  📐 <b>R:R:</b> <code>1:{rr_ratio:.2f}</code>")

    if custom_fields:
        sections = {}
        for cf in custom_fields:
            sec = cf.get("field_section", "custom")
            if sec not in sections:
                sections[sec] = []
            sections[sec].append(cf)

        analysis_fields = sections.get("analysis", [])
        if analysis_fields:
            has_any = any(filled.get(cf["field_key"]) for cf in analysis_fields)
            if has_any:
                lines.append("")
                lines.append(section_divider("تحلیل"))
                for cf in analysis_fields:
                    val = filled.get(cf["field_key"])
                    if val is None or val == "":
                        continue
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"{icon} {cf['field_label']}")
                    elif cf["field_type"] == "choice":
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")
                    else:
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")

        risk_fields = sections.get("risk", [])
        if risk_fields:
            has_any = any(filled.get(cf["field_key"]) for cf in risk_fields)
            if has_any:
                lines.append("")
                lines.append(section_divider("مدیریت ریسک"))
                for cf in risk_fields:
                    val = filled.get(cf["field_key"])
                    if val is None or val == "":
                        continue
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"{icon} {cf['field_label']}")
                    elif cf["field_type"] == "choice":
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")
                    else:
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")

        psy_fields = sections.get("psychology", [])
        if psy_fields:
            has_any = any(filled.get(cf["field_key"]) for cf in psy_fields)
            if has_any:
                lines.append("")
                lines.append(section_divider("روانشناسی"))
                for cf in psy_fields:
                    val = filled.get(cf["field_key"])
                    if val is None or val == "":
                        continue
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"{icon} {cf['field_label']}")
                    elif cf["field_type"] == "choice":
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")
                    else:
                        lines.append(f"{val}")

        result_fields = sections.get("result", [])
        if result_fields:
            has_any = any(filled.get(cf["field_key"]) for cf in result_fields)
            if has_any:
                lines.append("")
                lines.append(section_divider("نتیجه معامله"))
                for cf in result_fields:
                    val = filled.get(cf["field_key"])
                    if val is None or val == "":
                        continue
                    lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")

        review_fields = sections.get("review", [])
        if review_fields:
            has_any = any(filled.get(cf["field_key"]) for cf in review_fields)
            if has_any:
                lines.append("")
                lines.append(section_divider("بازبینی"))
                for cf in review_fields:
                    val = filled.get(cf["field_key"])
                    if val is None or val == "":
                        continue
                    if cf["field_type"] == "checkbox":
                        icon = "☑️" if val else "⬜"
                        lines.append(f"{icon} {cf['field_label']}")
                    elif cf["field_type"] == "choice":
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")
                    else:
                        lines.append(f"▶ <b>{cf['field_label']}:</b> {val}")

    return "\n".join(lines)


def format_trade_date_for_display(trade_date: str) -> str:
    try:
        from datetime import datetime
        if len(trade_date) >= 16:
            dt = datetime.strptime(trade_date[:16], "%d %B %H:%M")
            return dt.strftime("%d %b - %H:%M")
        return trade_date
    except (ValueError, IndexError):
        return trade_date

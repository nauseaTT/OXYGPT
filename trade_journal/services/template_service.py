import logging
import uuid
from typing import Any, Dict, List, Optional

from .. import database as db

logger = logging.getLogger(__name__)

MANDATORY_FIELD_DEFS = [
    {"field_key": "symbol", "field_label": "نماد", "field_type": "text", "field_section": "details"},
    {"field_key": "direction", "field_label": "جهت", "field_type": "text", "field_section": "details"},
    {"field_key": "volume", "field_label": "حجم", "field_type": "text", "field_section": "risk"},
    {"field_key": "entry_price", "field_label": "قیمت ورود", "field_type": "text", "field_section": "details"},
    {"field_key": "stoploss", "field_label": "حد ضرر", "field_type": "text", "field_section": "risk"},
    {"field_key": "exit_price", "field_label": "قیمت خروج", "field_type": "text", "field_section": "details"},
    {"field_key": "trade_date", "field_label": "تاریخ معامله", "field_type": "text", "field_section": "details"},
]

MANDATORY_KEYS = {f["field_key"] for f in MANDATORY_FIELD_DEFS}

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


async def create_default_template(user_id: int) -> int:
    template_id = await db.create_template(user_id, "پیش‌فرض")
    for i, fdef in enumerate(MANDATORY_FIELD_DEFS):
        await db.add_template_field(template_id, fdef["field_key"], fdef["field_label"],
                              fdef["field_type"], fdef.get("field_section", "custom"), sort_order=i)
    default_custom = [
        ("timeframe", "تایم‌فریم", "text", "details", 7),
        ("strategy", "استراتژی", "text", "details", 8),
        ("confluence_support", "حمایت/مقاومت", "text", "analysis", 9),
        ("confluence_pattern", "الگو", "text", "analysis", 10),
        ("confluence_indicator", "اندیکاتور", "text", "analysis", 11),
        ("confluence_fundamental", "فاندامنتال", "text", "analysis", 12),
        ("entry_trigger", "تریگر ورود", "text", "analysis", 13),
        ("risk_percent", "درصد ریسک از حساب", "text", "risk", 14),
        ("sl_pips", "حد ضرر (پیپ)", "text", "risk", 15),
        ("tp_pips", "حد سود (پیپ)", "text", "risk", 16),
        ("rr_ratio", "نسبت ریسک به ریوارد", "text", "risk", 17),
        ("mood_before", "روحیه قبل از معامله", "text", "psychology", 18),
        ("plan_adherence", "پایبندی به پلن", "checkbox", "psychology", 19),
        ("emotions_during", "احساسات حین معامله", "text", "psychology", 20),
        ("action_taken", "اقدام انجام‌شده", "text", "psychology", 21),
        ("pip_net", "پیپ خالص", "text", "result", 22),
        ("dollar_net", "دلار خالص", "text", "result", 23),
        ("multiple_risk", "چند برابر ریسک (R)", "text", "result", 24),
        ("what_went_well", "آنچه خوب پیش رفت", "text", "review", 25),
        ("what_could_be_better", "آنچه می‌توانست بهتر باشد", "text", "review", 26),
        ("entry_type", "نوع ورود", "choice", "details", 6),
        ("exit_reason", "علت خروج", "choice", "result", 21),
        ("score", "امتیاز کلی (از ۱۰)", "text", "review", 27),
        ("key_lesson", "درس کلیدی", "text", "review", 28),
    ]
    for fk, fl, ft, fs, so in default_custom:
        if ft == "choice":
            opts = {"entry_type": "Market,Limit,Stop", "exit_reason": "Take Profit,Stop Loss,Manual"}.get(fk)
        else:
            opts = None
        await db.add_template_field(template_id, fk, fl, ft, fs, sort_order=so, choice_options=opts)
    return template_id


async def get_or_create_default(user_id: int) -> int:
    templates = await db.get_templates(user_id)
    if templates:
        return templates[0]["id"]
    return await create_default_template(user_id)


async def get_all_fields(template_id: int) -> List[Dict[str, Any]]:
    return await db.get_template_fields(template_id)


async def get_custom_fields(template_id: int) -> List[Dict[str, Any]]:
    all_fields = await db.get_template_fields(template_id)
    return [f for f in all_fields if f["field_key"] not in MANDATORY_KEYS]


async def get_fields_by_section(template_id: int) -> Dict[str, List[Dict[str, Any]]]:
    return await db.get_template_fields_by_section(template_id)


async def add_custom_field(template_id: int, field_key: str, field_label: str,
                     field_type: str, field_section: str = "custom",
                     choice_options: str = None, is_required: int = 0) -> int:
    existing = await db.get_template_fields(template_id)
    max_order = max((f["sort_order"] for f in existing), default=0)
    return await db.add_template_field(template_id, field_key, field_label, field_type,
                                        field_section, max_order + 1, choice_options, is_required)


def generate_field_key(label: str) -> str:
    key = label.lower().strip().replace(" ", "_").replace("-", "_")
    safe = "".join(c for c in key if c.isalnum() or c == "_")
    if not safe:
        safe = "field"
    return f"custom_{safe}_{uuid.uuid4().hex[:6]}"


async def format_template_preview(template_id: int) -> str:
    template = await db.get_template(template_id)
    if not template:
        return "❌ قالب یافت نشد."

    fields = await db.get_template_fields(template_id)
    mandatory = [f for f in fields if f["field_key"] in MANDATORY_KEYS]
    custom = [f for f in fields if f["field_key"] not in MANDATORY_KEYS]
    sections = await db.get_template_fields_by_section(template_id)

    lines = []
    lines.append("📊 <b>قالب: " + template["name"] + "</b>")

    for sec_key in SECTION_ORDER:
        sec_fields = sections.get(sec_key, [])
        sec_fields = [f for f in sec_fields if f["field_key"] not in MANDATORY_KEYS]
        if not sec_fields:
            continue
        sec_name = SECTION_HEADERS.get(sec_key, sec_key)
        lines.append("‎" + sec_name + ":")
        for f in sec_fields:
            type_icon = {"checkbox": "☑️", "text": "📝", "choice": "🎲"}.get(f["field_type"], "📌")
            lines.append("‎  " + type_icon + " " + f["field_label"])

    if not mandatory and not custom:
        lines.append("‎  (هیچ فیلدی تعریف نشده)")
    else:
        lines.append("")

    info_parts = []
    if template.get("channel_title"):
        info_parts.append("📺 " + template["channel_title"])
    if template.get("is_default"):
        info_parts.append("⭐ پیش‌فرض")

    if info_parts:
        lines.append("‎" + " | ".join(info_parts))

    return "\n".join(lines)


async def build_initial_filled(template_id: int) -> Dict[str, Any]:
    filled = {}
    fields = await db.get_template_fields(template_id)
    for f in fields:
        if f["field_key"] == "direction":
            filled[f["field_key"]] = "LONG"
        elif f["field_key"] == "trade_date":
            from ..ui.keyboards import format_trade_date
            filled[f["field_key"]] = format_trade_date()
        elif f["field_type"] == "checkbox":
            filled[f["field_key"]] = False
        else:
            filled[f["field_key"]] = None
    return filled

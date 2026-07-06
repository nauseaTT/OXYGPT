from typing import Any, Dict, List, Optional
from datetime import datetime
# v2: v1 `telethon.tl.custom.Button` factory -> compat `Button` shim, which
# builds v2 `telethon.types.buttons.*` instances under the hood.
from telethon_compat import Button

from .formatter import MANDATORY_FIELDS


PERSIAN_MONTHS = {
    1: 'ژانویه', 2: 'فوریه', 3: 'مارس', 4: 'آوریل',
    5: 'مه', 6: 'ژوئن', 7: 'ژوئیه', 8: 'اوت',
    9: 'سپتامبر', 10: 'اکتبر', 11: 'نوامبر', 12: 'دسامبر'
}

SHORT_MONTHS = {
    1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
    7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'
}


def format_trade_date(dt=None) -> str:
    if dt is None:
        dt = datetime.now()
    month = PERSIAN_MONTHS.get(dt.month, '???')
    day = f"{dt.day:02d}"
    time = dt.strftime("%H:%M")
    return f"{day} {month} {time}"


def format_short_date(dt=None) -> str:
    if dt is None:
        dt = datetime.now()
    month = SHORT_MONTHS.get(dt.month, '???')
    day = f"{dt.day:02d}"
    time = dt.strftime("%H:%M")
    return f"{day}{month} - {time}"


def journal_reply_keyboard() -> List[List[Button]]:
    return [
        [Button.text("📊 ژورنال معاملات", resize=True)],
    ]


def exit_reply_keyboard():
    return Button.clear()


def group_inline_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("📊 آنالیز و وضعیت", b"tj_group_stats")],
        [Button.inline("📝 ثبت سریع معامله", b"tj_group_quick_trade", style="success")],
        [Button.inline("📋 قالب‌ها", b"tj_group_templates")],
        [Button.inline("🔍 جستجوی معاملات", b"tj_group_search")],
        [Button.inline("💡 راهنما", b"tj_group_help")],
    ]


def journal_header_button(text: str) -> List[Button]:
    return [Button.inline(f"── {text} ──", b"placeholder_header")]


def journal_inline_panel() -> List[List[Button]]:
    return [
        journal_header_button("📝 معامله"),
        [Button.inline("📝 معامله جدید", b"tj_new_trade", style="success")],
        journal_header_button("📋 مدیریت"),
        [Button.inline("📋 قالب‌ها", b"tj_templates")],
        [Button.inline("🏷️ نمادها", b"tj_symbols")],
        [Button.inline("⚙️ تنظیمات", b"tj_settings")],
        journal_header_button("📊 گزارشات"),
        [Button.inline("📊 آمار", b"tj_stats")],
        [Button.inline("📋 تاریخچه", b"tj_trades_list")],
        [Button.inline("❌ خروج", b"tj_exit", style="danger")],
    ]


def symbol_select_keyboard_for_form(symbols: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for s in symbols:
        rows.append([Button.inline(
            f"🏷️ {s['name']}",
            f"tj_sym_form:{s['name']}".encode()
        )])
    rows.append([Button.inline("➕ افزودن نماد جدید", b"tj_sym_add_form", style="success")])
    return rows


def volume_keyboard() -> List[List[Button]]:
    return [
        [
            Button.inline("0.01", b"tj_vol:0.01"),
            Button.inline("0.05", b"tj_vol:0.05"),
            Button.inline("0.1", b"tj_vol:0.1"),
        ],
        [
            Button.inline("0.5", b"tj_vol:0.5"),
            Button.inline("1.0", b"tj_vol:1.0"),
            Button.inline("2.0", b"tj_vol:2.0"),
        ],
        [
            Button.inline("5.0", b"tj_vol:5.0"),
            Button.inline("10.0", b"tj_vol:10.0"),
        ],
        [Button.inline("✏️ دستی...", b"tj_vol_custom")],
        [Button.inline("❌ لغو", b"tj_cancel_field_edit", style="danger")],
    ]


def live_panel_keyboard(filled: Dict[str, Any], custom_fields: List[Dict[str, Any]] = None) -> List[List[Button]]:
    rows = []

    mandatory_row = []
    for key, label in MANDATORY_FIELDS.items():
        val = filled.get(key)
        if val is not None and val != "":
            if key == "direction":
                arrow = "📈" if val == "LONG" else "📉"
                btn_text = f"✅ {arrow}"
            else:
                display = str(val)[:8]
                btn_text = f"✅ {display}"
        else:
            btn_text = f"⬜ {label[:3]}"
        mandatory_row.append(Button.inline(btn_text, f"tj_field:{key}".encode()))
        if len(mandatory_row) == 3:
            rows.append(mandatory_row)
            mandatory_row = []
    if mandatory_row:
        rows.append(mandatory_row)

    if custom_fields:
        sections = {}
        for cf in custom_fields:
            sec = cf.get("field_section", "custom")
            if sec not in sections:
                sections[sec] = []
            sections[sec].append(cf)

        from .formatter import SECTION_HEADERS, SECTION_ORDER
        for sec_name in SECTION_ORDER:
            if sec_name not in sections:
                continue
            sec_fields = sections[sec_name]
            sec_header = SECTION_HEADERS.get(sec_name, sec_name)
            rows.append([Button.inline(f"── {sec_header} ──", b"placeholder_section")])
            for cf in sec_fields:
                ft = cf["field_type"]
                fk = cf["field_key"]
                fl = cf["field_label"]
                val = filled.get(fk)
                if ft == "checkbox":
                    icon = "☑️" if val else "⬜"
                    rows.append([Button.inline(f"{icon} {fl}", f"tj_toggle:{fk}".encode())])
                elif ft == "choice":
                    if val:
                        btn_text = f"🎲 {str(val)[:12]}"
                    else:
                        btn_text = f"⬜ {fl[:8]}"
                    rows.append([Button.inline(btn_text, f"tj_text_field:{fk}".encode())])
                else:
                    if val:
                        preview = str(val)[:10]
                        btn_text = f"📝 {preview}"
                    else:
                        btn_text = f"⬜ {fl[:6]}"
                    rows.append([Button.inline(btn_text, f"tj_text_field:{fk}".encode())])

        from .formatter import SECTION_ORDER as ALL_SECTIONS
        for sec_name in ALL_SECTIONS:
            if sec_name in sections:
                continue
            sec_header = SECTION_HEADERS.get(sec_name, sec_name)
            template_id = filled.get("_template_id")
            if template_id:
                rows.append([
                    Button.inline(f"── {sec_header} ──", b"placeholder_section"),
                    Button.inline("➕", f"tj_quick_add_field:{template_id}:{sec_name}".encode()),
                ])

    all_filled = all(filled.get(k) is not None and filled.get(k) != "" for k in MANDATORY_FIELDS)
    photo_count = len(filled.get("_photos", []))
    photo_label = f"🖼 عکس ({photo_count}/3)" if photo_count > 0 else "🖼 عکس"
    rows.append([
        Button.inline(photo_label, b"tj_add_photo"),
        Button.inline("👁 پیش‌نمایش", b"tj_preview"),
        Button.inline("✅ ثبت", b"tj_finalize", style="success" if all_filled else "danger"),
    ])
    rows.append([Button.inline("❌ لغو", b"tj_cancel_trade", style="danger")])

    return rows


def template_select_keyboard(templates: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for t in templates:
        default_mark = " ⭐" if t.get("is_default") else ""
        rows.append([Button.inline(
            f"📋 {t['name']}{default_mark}",
            f"tj_template_select:{t['id']}".encode()
        )])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def template_list_keyboard(templates: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for t in templates:
        default_mark = " ⭐" if t.get("is_default") else ""
        ch_info = ""
        if t.get("channel_title"):
            ch_info = f" 📺"
        rows.append([
            Button.inline(f"📋 {t['name']}{default_mark}{ch_info}", f"tj_tpl_edit:{t['id']}".encode()),
            Button.inline("🗑", f"tj_tpl_delete:{t['id']}".encode(), style="danger"),
            Button.inline("📄", f"tj_tpl_copy:{t['id']}".encode()),
        ])
    if len(templates) < 5:
        rows.append([Button.inline("➕ ایجاد قالب جدید", b"tj_tpl_create", style="success")])
    else:
        rows.append([Button.inline("🚫 حداکثر 5 قالب", b"placeholder_limit", style="danger")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def template_edit_keyboard(template_id: int, fields: List[Dict[str, Any]], is_default: bool = False) -> List[List[Button]]:
    rows = []
    for f in fields:
        type_icon = "☑️" if f["field_type"] == "checkbox" else "📝"
        section_tag = ""
        sec = f.get("field_section", "custom")
        if sec and sec != "custom":
            from .formatter import SECTION_HEADERS
            section_tag = f" [{SECTION_HEADERS.get(sec, sec)[:4]}]"
        rows.append([
            Button.inline(
                f"{type_icon} {f['field_label']}{section_tag}",
                f"tj_tpl_field_info:{f['id']}".encode()
            ),
            Button.inline("🗑", f"tj_tpl_field_del:{template_id}:{f['id']}".encode(), style="danger"),
        ])
    rows.append([Button.inline("➕ افزودن فیلد", f"tj_tpl_field_add:{template_id}".encode(), style="success")])
    rows.append([Button.inline("📺 اتصال به کانال", f"tj_tpl_channel:{template_id}".encode())])
    if not is_default:
        rows.append([Button.inline("⭐ پیش‌فرض کردن", f"tj_set_default:{template_id}".encode())])
    else:
        rows.append([Button.inline("⭐ پیش‌فرض فعال", b"placeholder_default")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_templates")])
    return rows


def template_glass_panel(template_id: int, has_fields: bool = False, is_default: bool = False) -> List[List[Button]]:
    rows = [
        [Button.inline("➕ افزودن فیلد", f"tj_tpl_field_add:{template_id}".encode(), style="success")],
    ]
    if has_fields:
        rows.append([Button.inline("🗑 حذف فیلد", f"tj_tpl_field_del_list:{template_id}".encode(), style="danger")])
    rows.append([Button.inline("📺 اتصال به کانال", f"tj_tpl_channel:{template_id}".encode())])
    if not is_default:
        rows.append([Button.inline("⭐ پیش‌فرض کردن", f"tj_set_default:{template_id}".encode())])
    else:
        rows.append([Button.inline("⭐ پیش‌فرض فعال", b"placeholder_default")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_templates")])
    return rows


def template_field_list_keyboard(template_id: int, fields: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for f in fields:
        type_icon = "☑️" if f["field_type"] == "checkbox" else ("🎲" if f["field_type"] == "choice" else "📝")
        sec = f.get("field_section", "custom")
        rows.append([
            Button.inline(
                f"{type_icon} {f['field_label']}",
                f"tj_tpl_field_info:{f['id']}".encode()
            ),
            Button.inline("🗑", f"tj_tpl_field_del:{template_id}:{f['id']}".encode(), style="danger"),
        ])
    rows.append([Button.inline("🔙 بازگشت", f"tj_tpl_edit:{template_id}".encode())])
    return rows


def field_type_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("☑️ چک‌باکس (بله/خیر)", b"tj_tpl_field_type:checkbox")],
        [Button.inline("📝 متن (توضیحات)", b"tj_tpl_field_type:text")],
        [Button.inline("🎲 انتخابی (چند گزینه)", b"tj_tpl_field_type:choice")],
        [Button.inline("❌ لغو", b"tj_cancel_field_add", style="danger")],
    ]


def field_section_keyboard(template_id: int, field_type: str, field_label: str) -> List[List[Button]]:
    return [
        [Button.inline("📌 جزئیات معامله", f"tj_tpl_field_section:{template_id}:details:{field_type}:{field_label}".encode())],
        [Button.inline("🧠 تحلیل و دلایل ورود", f"tj_tpl_field_section:{template_id}:analysis:{field_type}:{field_label}".encode())],
        [Button.inline("⚙️ مدیریت ریسک", f"tj_tpl_field_section:{template_id}:risk:{field_type}:{field_label}".encode())],
        [Button.inline("🧘 وضعیت روانی", f"tj_tpl_field_section:{template_id}:psychology:{field_type}:{field_label}".encode())],
        [Button.inline("📈 نتیجه معامله", f"tj_tpl_field_section:{template_id}:result:{field_type}:{field_label}".encode())],
        [Button.inline("🔍 بازبینی نهایی", f"tj_tpl_field_section:{template_id}:review:{field_type}:{field_label}".encode())],
        [Button.inline("📋 سایر (دسته‌بندی نشده)", f"tj_tpl_field_section:{template_id}:custom:{field_type}:{field_label}".encode())],
        [Button.inline("❌ لغو", b"tj_cancel_field_add", style="danger")],
    ]


def confirm_delete_keyboard(entity: str, entity_id: int) -> List[List[Button]]:
    return [
        [
            Button.inline("✅ بله، حذف شود", f"tj_confirm_delete_{entity}:{entity_id}".encode(), style="danger"),
            Button.inline("❌ خیر، انصراف", f"tj_back_{entity}s".encode()),
        ]
    ]


def symbol_select_keyboard(symbols: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for s in symbols:
        rows.append([Button.inline(
            f"🏷️ {s['name']}",
            f"tj_sym_select:{s['name']}".encode()
        )])
    rows.append([Button.inline("➕ افزودن نماد جدید", b"tj_sym_new", style="success")])
    return rows


def symbol_manage_keyboard(symbols: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for s in symbols:
        rows.append([
            Button.inline(f"🏷️ {s['name']}", f"tj_sym_info:{s['id']}".encode()),
            Button.inline("🗑", f"tj_sym_del:{s['id']}".encode(), style="danger"),
        ])
    rows.append([Button.inline("➕ افزودن نماد", b"tj_sym_add", style="success")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def direction_keyboard() -> List[List[Button]]:
    return [
        [
            Button.inline("📈 خرید (LONG)", b"tj_dir:LONG"),
            Button.inline("📉 فروش (SHORT)", b"tj_dir:SHORT", style="danger"),
        ],
        [Button.inline("❌ لغو", b"tj_cancel_field_edit", style="danger")],
    ]


def date_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("⏰ همین الان", b"tj_date:now")],
        [Button.inline("❌ لغو", b"tj_cancel_field_edit", style="danger")],
    ]


def post_confirm_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("✅ تأیید و انتشار", b"tj_confirm_post", style="success")],
        [Button.inline("🖊️ ادامه ویرایش", b"tj_continue_edit")],
    ]


def settings_keyboard(has_channel: bool, has_margin: bool = False) -> List[List[Button]]:
    rows = []
    if has_channel:
        rows.append([Button.inline("📺 وضعیت کانال‌ها", b"tj_settings_channels")])
        rows.append([Button.inline("🔄 تغییر کانال اصلی", b"tj_settings_channel")])
    else:
        rows.append([Button.inline("📺 تنظیم کانال", b"tj_settings_channel")])
    if not has_margin:
        rows.append([Button.inline("💰 ثبت مارجین حساب", b"tj_settings_margin")])
    else:
        rows.append([Button.inline("💰 ویرایش مارجین", b"tj_settings_margin")])
    rows.append([Button.inline("🛡 حریم خصوصی گروه", b"tj_privacy_settings")])
    rows.append([Button.inline("📊 آمار کلی", b"tj_stats")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def stats_glass_keyboard() -> List[List[Button]]:
    rows = []
    rows.append([
        Button.inline("▶ تحلیل جامع", b"tj_detailed_analysis"),
    ])
    rows.append([
        Button.inline("📄 دریافت PDF", b"tj_export_pdf"),
        Button.inline("📤 خروجی JSON", b"tj_export"),
    ])
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def channel_setup_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("🔄 تلاش مجدید", b"tj_settings_channel")],
        [Button.inline("❌ لغو", b"tj_cancel_setup", style="danger")],
    ]


def trade_list_keyboard(trades: List[Dict[str, Any]], page: int = 0, total: int = 0) -> List[List[Button]]:
    rows = []
    for t in trades:
        direction_emoji = "📈" if t.get("direction") == "LONG" else "📉"
        pnl = t.get("pnl")
        pnl_icon = "🟢" if (pnl or 0) >= 0 else "🔴"
        symbol = t.get("symbol", "?")

        from .formatter import format_trade_date_for_display
        trade_date = t.get("trade_date", "")
        display_date = format_trade_date_for_display(trade_date)

        pnl_str = f"{pnl:+.2f}" if pnl is not None else "N/A"
        pnl_pct = ""
        if pnl is not None:
            entry = t.get("entry_price")
            if entry and entry > 0:
                pct = (pnl / ((t.get("volume") or 1) * entry)) * 100
                pnl_pct = f" ({pct:+.1f}%)"

        btn_text = f"{symbol} | {display_date}"
        rows.append([
            Button.inline(btn_text, f"tj_trade_view:{t['id']}".encode()),
            Button.inline("✏️", f"tj_trade_edit:{t['id']}".encode()),
            Button.inline("🗑", f"tj_trade_del:{t['id']}".encode(), style="danger"),
        ])

    nav_row = []
    per_page = 5
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = page + 1

    if page > 0:
        nav_row.append(Button.inline("◀ قبلی", f"tj_trades_page:{page - 1}".encode()))
    nav_row.append(Button.inline(f"📊 {current_page}/{total_pages}", b"placeholder_trade_info"))
    start = page * per_page + len(trades)
    if start < total:
        nav_row.append(Button.inline("بعدی ▶", f"tj_trades_page:{page + 1}".encode()))
    if len(nav_row) > 1:
        rows.append(nav_row)
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def trade_detail_keyboard(trade_id: int, channel_message_id: int = None, channel_chat_id: int = None) -> List[List[Button]]:
    rows = []
    rows.append([Button.inline("✏️ ویرایش معامله", f"tj_trade_edit:{trade_id}".encode())])
    rows.append([Button.inline("🗑 حذف معامله", f"tj_trade_del:{trade_id}".encode(), style="danger")])
    rows.append([Button.inline("🔙 بازگشت به لیست", b"tj_trades_list")])
    return rows


def channel_post_keyboard(trade_id: int, channel_message_id: int, channel_chat_id: int) -> List[List[Button]]:
    return [
        [
            Button.inline("✏️ ادیت", f"tj_ch_edit:{trade_id}:{channel_chat_id}:{channel_message_id}".encode()),
            Button.inline("🗑 حذف", f"tj_ch_delete:{trade_id}:{channel_chat_id}:{channel_message_id}".encode(), style="danger"),
        ],
        [Button.inline("🧠 آنالیز با ای", f"tj_ch_analysis:{trade_id}".encode())],
    ]


def channels_list_keyboard(channels: List[Dict[str, Any]]) -> List[List[Button]]:
    rows = []
    for ch in channels:
        status = "✅" if ch.get("is_active") else "⚠️"
        rows.append([
            Button.inline(
                f"{status} {ch.get('channel_title', 'N/A')}",
                f"tj_channel_info:{ch['channel_id']}".encode()
            ),
            Button.inline("🗑", f"tj_channel_del:{ch['channel_id']}".encode(), style="danger"),
        ])
    rows.append([Button.inline("➕ افزودن کانال جدید", b"tj_settings_channel", style="success")])
    rows.append([Button.inline("🔙 بازگشت", b"tj_settings")])
    return rows


def template_channel_select_keyboard(templates: List[Dict[str, Any]]) -> List[List[Button]]:
    from .. import database as db
    rows = []
    for t in templates:
        ch_title = t.get("channel_title") or "تنظیم نشده"
        ch_icon = "📺" if t.get("channel_id") else "⚠️"
        rows.append([
            Button.inline(
                f"📋 {t['name']} | {ch_icon} {ch_title}",
                f"tj_tpl_channel:{t['id']}".encode()
            ),
        ])
    rows.append([Button.inline("🔙 بازگشت", b"tj_settings")])
    return rows


def privacy_settings_keyboard(current_mode: str = "off", current_level: str = "medium") -> List[List[Button]]:
    levels = [
        ("off", "✖ غیرفعال", "هیچ محدودیتی ندارد"),
        ("low", "🟨 راحت", "تنها اطلاعات کلی نمایش داده می‌شود"),
        ("medium", "🟠 متوسط", "وین‌ریت و برآیند مخفی می‌شود"),
        ("high", "🔴 شدید", "تقریباً هر چیز مخفی می‌شود"),
    ]
    rows = []
    row = []
    for key, label, _ in levels:
        mark = "✔️ " if key == current_mode else ""
        row.append(Button.inline(f"{mark}{label}", f"tj_privacy_mode:{key}".encode()))
    rows.append(row)
    rows.append([Button.inline("🔙 بازگشت", b"tj_back_journal")])
    return rows


def scoring_keyboard() -> List[List[Button]]:
    row = []
    for i in range(1, 7):
        row.append(Button.inline(str(i), f"tj_score:{i}".encode()))
    return [row, [Button.inline("🚫 رد کردن", b"tj_skip_scoring", style="danger")]]


def choice_options_keyboard(options: List[str], field_key: str) -> List[List[Button]]:
    rows = []
    for opt in options:
        rows.append([Button.inline(opt, f"tj_choice:{field_key}:{opt}".encode())])
    rows.append([Button.inline("❌ لغو", b"tj_cancel_field_edit", style="danger")])
    return rows


def photo_management_keyboard(photos: list) -> List[List[Button]]:
    rows = []
    for i, _ in enumerate(photos):
        rows.append([
            Button.inline(f"🖼 عکس #{i+1}", b"placeholder_photo"),
            Button.inline("✏️", f"tj_photo_replace:{i}".encode()),
            Button.inline("🗑", f"tj_photo_delete:{i}".encode(), style="danger"),
        ])
    if len(photos) < 3:
        rows.append([Button.inline("➕ اضافه عکس", b"tj_add_photo", style="success")])
    rows.append([Button.inline("❌ بازگشت", b"tj_cancel_field_edit", style="danger")])
    return rows


def field_required_keyboard(template_id: int, field_type: str, field_label: str) -> List[List[Button]]:
    return [
        [Button.inline("⚠️ اجباری", f"tj_tpl_field_required:{template_id}:{field_type}:{field_label}:1".encode(), style="danger")],
        [Button.inline("❓ اختیاری", f"tj_tpl_field_required:{template_id}:{field_type}:{field_label}:0".encode())],
    ]


def search_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("🏷️ جستجو بر اساس نماد", b"tj_search_symbol")],
        [Button.inline("📅 جستجو بر اساس تاریخ", b"tj_search_date")],
        [Button.inline("💰 جستجو بر اساس سود/زیان", b"tj_search_pnl")],
        [Button.inline("🔄 نمایش همه", b"tj_trades_list")],
        [Button.inline("🔙 بازگشت", b"tj_back_journal")],
    ]


def bulk_operations_keyboard(selected_count: int = 0) -> List[List[Button]]:
    return [
        [Button.inline("✅ انتخاب/لغو انتخاب", b"tj_bulk_select_toggle")],
        [Button.inline(f"🗑 حذف انتخاب شده ({selected_count})", b"tj_bulk_delete", style="danger")],
        [Button.inline("📤 خروجی انتخاب شده", b"tj_bulk_export")],
        [Button.inline("🔙 بازگشت", b"tj_back_journal")],
    ]


def notification_keyboard() -> List[List[Button]]:
    return [
        [Button.inline("🔔 اعلان‌ها", b"tj_notifications")],
        [Button.inline("📊 خلاصه روز", b"tj_daily_summary")],
        [Button.inline("🔙 بازگشت", b"tj_back_journal")],
    ]

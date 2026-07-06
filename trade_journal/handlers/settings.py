import asyncio
import logging
from typing import Any

# v2: `from telethon import TelegramClient, events` -> compat re-exports.
# The compat layer aliases `TelegramClient` to v2's `Client`, provides the
# v2 `events` module, `filters`, and the `data_regex` helper that reproduces
# v1's `events.CallbackQuery(pattern=...)` regex-matching semantics.
from telethon_compat import TelegramClient, events, Button, filters, data_regex

from .. import database as db
from ..states import set_state, clear_state, IDLE, SHOWING_SETTINGS, AWAIT_CHANNEL_FORWARD
from ..ui.keyboards import settings_keyboard, journal_reply_keyboard, channel_setup_keyboard, channels_list_keyboard, template_channel_select_keyboard
from ..services.channel_service import check_channel_validity
from ..utils import parse_callback_int, get_callback_parts, auto_delete_message

logger = logging.getLogger(__name__)


def register_settings_handlers(client: TelegramClient) -> None:
    def wrap(handler):
        async def wrapper(event):
            tg_bot = getattr(event.client, "tg_bot", None)
            if tg_bot:
                if not await tg_bot.enforce_mandatory_join(event):
                    return
            return await handler(event)
        return wrapper

    client.on(events.ButtonCallback, filters.Data(b"tj_stats"))(wrap(_handle_stats))
    client.on(events.ButtonCallback, filters.Data(b"tj_settings_channels"))(wrap(_handle_settings_channels))
    client.on(events.ButtonCallback, data_regex(r"tj_channel_info:"))(wrap(_handle_channel_info))
    client.on(events.ButtonCallback, data_regex(r"tj_channel_del:"))(wrap(_handle_channel_delete))
    client.on(events.ButtonCallback, filters.Data(b"tj_privacy_settings"))(wrap(_handle_privacy_settings))
    client.on(events.ButtonCallback, data_regex(r"tj_privacy_mode:"))(wrap(_handle_privacy_mode_set))
    client.on(events.ButtonCallback, data_regex(r"tj_privacy_level:"))(wrap(_handle_privacy_level_set))
    client.on(events.ButtonCallback, filters.Data(b"tj_export_pdf"))(wrap(_handle_export_pdf))


async def _handle_stats(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    from ..database import get_trade_stats
    from ..services.trade_service import format_glass_stats_text
    stats = await get_trade_stats(uid)
    text = format_glass_stats_text(stats)

    from ..ui.keyboards import stats_glass_keyboard
    try:
        await event.edit(text, buttons=stats_glass_keyboard(), parse_mode="html")
    except Exception:
        await event.respond(text, buttons=stats_glass_keyboard(), parse_mode="html")


async def _handle_settings_channels(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    channels = await db.get_user_channels(uid)

    if not channels:
        await event.edit(
            "📺 <b>کانال‌های شما</b>\n\n"
            "هیچ کانالی ثبت نشده است.",
            buttons=[
                [Button.inline("📺 افزودن کانال", b"tj_settings_channel")],
                [Button.inline("🔙 بازگشت", b"tj_settings")],
            ],
            parse_mode="html"
        )
        return

    await event.edit(
        f"📺 <b>کانال‌های شما ({len(channels)})</b>\n\n"
        "برای مشاهده وضعیت یا حذف کانال روی هر گزینه کلیک کنید:",
        buttons=channels_list_keyboard(channels),
        parse_mode="html"
    )


async def _handle_channel_info(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    channel_id = parse_callback_int(event.data)

    is_valid, msg = await check_channel_validity(event.client, channel_id)
    user = await db.get_user(uid)
    is_main = user and user.get("channel_id") == channel_id

    channel_info = ""
    channels = await db.get_user_channels(uid)
    for ch in channels:
        if ch["channel_id"] == channel_id:
            channel_info = ch.get("channel_title", "N/A")
            break

    templates = await db.get_templates(uid)
    template_info = None
    for t in templates:
        if t.get("channel_id") == channel_id:
            template_info = t
            break

    lines = []
    lines.append(f"<b>Channel:</b> {channel_info}")
    lines.append(f"<b>ID:</b> <code>{channel_id}</code>")
    lines.append(f"<b>Status:</b> {'Active' if is_valid else 'Error'}")
    if msg:
        lines.append(f"<b>Details:</b> {msg}")
    lines.append(f"<b>Main Channel:</b> {'Yes' if is_main else 'No'}")

    await event.edit(
        "\n".join(lines),
        buttons=[
            [Button.inline("Delete Channel", f"tj_ch_delete:{channel_id}".encode(), style="danger")],
            [Button.inline("Back", b"tj_settings_channels")]
        ],
        parse_mode="html"
    )


async def _handle_channel_delete(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    channel_id = parse_callback_int(event.data)

    user = await db.get_user(uid)
    is_main = user and user.get("channel_id") == channel_id

    if is_main:
        await db.update_user(uid, channel_id=None, channel_title=None)

    await db.delete_user_channel(uid, channel_id)

    channels = await db.get_user_channels(uid)
    if channels:
        await event.edit(
            f"✅ کانال حذف شد.",
            buttons=channels_list_keyboard(channels),
            parse_mode="html"
        )
    else:
        await event.edit(
            "✅ کانال حذف شد.\n\nهیچ کانالی ندارید.",
            buttons=[
                [Button.inline("📺 افزودن کانال", b"tj_settings_channel")],
                [Button.inline("🔙 بازگشت", b"tj_settings")],
            ],
            parse_mode="html"
        )


async def _handle_export_pdf(event: Any) -> None:
    uid = event.sender_id
    await event.answer("📄 در حال تهیه گزارش PDF...", alert=False)
    try:
        from ..services.ai_analysis import export_trades_json
        data = await export_trades_json(uid)
        stats = await db.get_trade_stats(uid)
        user = await db.get_user(uid)

        from fpdf import FPDF
        from fpdf.bidi import BidiParagraph
        import os, tempfile, datetime

        FONT_DIR = "/usr/share/fonts/truetype/dejavu"

        def bidi_text(text: str) -> str:
            return BidiParagraph(text).get_reordered_string()

        class PDF(FPDF):
            def header(self):
                if self.page_no() == 1:
                    return
                self.set_font("DejaVu", "B", 10)
                self.set_text_color(100, 100, 100)
                self.cell(0, 8, bidi_text("Trade Journal Report"), align="C", new_x="LMARGIN", new_y="NEXT")
                self.ln(2)

            def footer(self):
                self.set_y(-15)
                self.set_font("DejaVu", "", 8)
                self.set_text_color(128, 128, 128)
                self.cell(0, 10, bidi_text(f"صفحه {self.page_no()}/{{nb}}"), align="C")

        pdf = PDF(orientation="P", unit="mm", format="A4")
        pdf.alias_nb_pages()
        pdf.add_font("DejaVu", "", f"{FONT_DIR}/DejaVuSans.ttf")
        pdf.add_font("DejaVu", "B", f"{FONT_DIR}/DejaVuSans-Bold.ttf")
        pdf.add_font("DejaVuMono", "", f"{FONT_DIR}/DejaVuSansMono.ttf")
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()

        page_w = pdf.w - 2 * pdf.l_margin

        pdf.set_fill_color(30, 60, 110)
        pdf.rect(0, 0, pdf.w, 50, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("DejaVu", "B", 22)
        pdf.set_y(12)
        pdf.cell(0, 12, bidi_text("گزارش ژورنال معاملات"), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 11)
        pdf.set_y(28)
        username = user.get("username") or user.get("first_name") or "کاربر"
        pdf.cell(0, 8, bidi_text(f"کاربر: {username}"), align="C", new_x="LMARGIN", new_y="NEXT")
        today_str = datetime.datetime.now().strftime("%Y/%m/%d - %H:%M")
        pdf.set_font("DejaVu", "", 9)
        pdf.cell(0, 7, bidi_text(f"تاریخ گزارش: {today_str}"), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_y(55)

        total = stats.get("total") or 0
        wins = stats.get("wins") or 0
        losses = stats.get("losses") or 0
        win_rate = stats.get("win_rate") or 0
        total_pnl = stats.get("total_pnl") or 0

        col_w = page_w / 2

        pdf.set_fill_color(240, 240, 245)
        pdf.set_draw_color(180, 180, 200)
        pdf.set_font("DejaVu", "B", 12)
        pdf.cell(page_w, 9, bidi_text("خلاصه آمار"), border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 10)
        pdf.ln(1)

        label_color = (80, 80, 100)
        val_color = (0, 0, 0)
        rows = [
            (bidi_text("تعداد کل معاملات"), str(total)),
            (bidi_text("تعداد برد"), str(wins)),
            (bidi_text("تعداد باخت"), str(losses)),
            (bidi_text("نرخ برد"), f"{win_rate:.1f}%"),
        ]
        for i, (label, val) in enumerate(rows):
            fill = i % 2 == 0
            if fill:
                pdf.set_fill_color(248, 248, 252)
            pdf.set_text_color(*label_color)
            pdf.set_font("DejaVu", "", 10)
            pdf.cell(col_w, 8, label, border=1, fill=fill, align="R")
            pdf.set_text_color(*val_color)
            pdf.set_font("DejaVu", "B", 10)
            pdf.cell(col_w, 8, val, border=1, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.set_fill_color(245, 245, 250)
        pnl_color = (0, 150, 0) if total_pnl >= 0 else (200, 0, 0)
        pdf.set_text_color(*label_color)
        pdf.set_font("DejaVu", "", 10)
        pdf.cell(col_w, 9, bidi_text("سود/زیان کل"), border=1, fill=True, align="R")
        pdf.set_text_color(*pnl_color)
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(col_w, 9, f"{total_pnl:+.2f}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(6)

        trades_data = data.get("trades", [])
        if not trades_data:
            pdf.set_font("DejaVu", "", 11)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 10, bidi_text("هیچ معامله‌ای ثبت نشده است."), align="C", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_fill_color(240, 240, 245)
            pdf.set_draw_color(180, 180, 200)
            pdf.set_font("DejaVu", "B", 12)
            pdf.cell(page_w, 9, bidi_text("لیست معاملات"), border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

            headers = ["#", bidi_text("نماد"), bidi_text("نوع"), bidi_text("حجم"), bidi_text("ورود"), bidi_text("خروج"), "P&L", "R:R", bidi_text("تاریخ")]
            col_widths = [7, 16, 12, 14, 20, 20, 18, 14, page_w - 121]

            pdf.set_font("DejaVu", "B", 8)
            pdf.set_fill_color(50, 80, 140)
            pdf.set_text_color(255, 255, 255)
            for hdr, cw in zip(headers, col_widths):
                pdf.cell(cw, 7, hdr, border=1, fill=True, align="C")
            pdf.ln()

            pdf.set_text_color(0, 0, 0)
            pdf.set_font("DejaVu", "", 7)
            for i, t in enumerate(trades_data):
                if pdf.get_y() > 260:
                    pdf.add_page()
                    pdf.set_font("DejaVu", "B", 8)
                    pdf.set_fill_color(50, 80, 140)
                    pdf.set_text_color(255, 255, 255)
                    for hdr, cw in zip(headers, col_widths):
                        pdf.cell(cw, 7, hdr, border=1, fill=True, align="C")
                    pdf.ln()
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("DejaVu", "", 7)

                fill = i % 2 == 0
                if fill:
                    pdf.set_fill_color(248, 248, 252)
                else:
                    pdf.set_fill_color(255, 255, 255)

                direction = t.get("direction", "?")
                is_long = direction == "LONG"
                dir_symbol = "▲ L" if is_long else "▼ S"
                dir_color = (0, 150, 0) if is_long else (200, 0, 0)

                pnl_val = t.get("pnl")
                pnl_str = f"{pnl_val:+.2f}" if pnl_val is not None else "-"
                pnl_color = (0, 150, 0) if (pnl_val or 0) >= 0 else (200, 0, 0)
                rr = t.get("risk_reward_ratio")
                rr_str = f"{rr:.2f}" if rr is not None else "-"
                trade_date = t.get("trade_date", "")

                row_data = [
                    (str(t.get("id", "")), None),
                    (t.get("symbol", "?"), None),
                    (dir_symbol, dir_color),
                    (str(t.get("volume", "")), None),
                    (str(t.get("entry_price", "")), None),
                    (str(t.get("exit_price", "")), None),
                    (pnl_str, pnl_color),
                    (rr_str, None),
                    (trade_date, None),
                ]

                for (txt, clr), cw in zip(row_data, col_widths):
                    if clr:
                        pdf.set_text_color(*clr)
                    else:
                        pdf.set_text_color(0, 0, 0)
                    pdf.cell(cw, 6, txt, border=1, fill=fill, align="C")
                pdf.ln()

            pdf.set_text_color(0, 0, 0)
            pdf.ln(4)

            pdf.set_font("DejaVu", "", 9)
            pdf.set_fill_color(245, 245, 250)
            total_trades = len(trades_data)
            total_wins = sum(1 for t in trades_data if (t.get("pnl") or 0) > 0)
            total_losses = total_trades - total_wins
            net_pnl = sum((t.get("pnl") or 0) for t in trades_data)
            summary_text = bidi_text(f"جمع: {total_trades} معامله | {total_wins} برد | {total_losses} باخت | سود/زیان خالص: {net_pnl:+.2f}")
            pdf.cell(page_w, 8, summary_text, border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

        pdf_path = os.path.join(tempfile.gettempdir(), f"trade_journal_{uid}.pdf")
        pdf.output(pdf_path)

        try:
            me = await event.client.get_me()
            await event.client.send_file(uid, pdf_path, caption="📄 گزارش PDF ژورنال معاملات", parse_mode="html")
            await event.edit("✅ گزارش PDF برای شما ارسال شد.")
        except Exception as e:
            logger.error(f"Error sending PDF: {e}")
            await event.answer("❌ خطا در ارسال PDF", alert=True)

        try:
            os.remove(pdf_path)
        except Exception:
            pass
    except ImportError:
        await event.answer("❌ کتابخانه PDF نصب نیست", alert=True)
    except Exception as e:
        logger.error(f"PDF export error: {e}", exc_info=True)
        await event.answer("❌ خطا در تهیه PDF", alert=True)


async def _handle_privacy_settings(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    if not event.is_private:
        try:
            me = await event.client.get_me()
            username = me.username or "Bot"
            msg = await event.respond(
                "🛡 <b>حریم خصوصی</b>\n\n"
                f"<a href='https://t.me/{username}'>📨 باز کردن در پیوی ربات</a>",
                parse_mode="html"
            )
            asyncio.ensure_future(auto_delete_message(event.client, event.chat_id, msg.id, 60))
        except Exception as e:
            logger.error(f"Error redirecting to PM: {e}")
        return
    from ..ui.keyboards import settings_keyboard
    privacy = await db.get_privacy(uid)
    mode = privacy.get("mode", "off")
    await event.edit(
        f"<b>Privacy Settings:</b>\n\nCurrent mode: <b>{mode}</b>",
        buttons=[
            [Button.inline("Low", b"tj_privacy_mode:low"), Button.inline("Medium", b"tj_privacy_mode:medium"), Button.inline("High", b"tj_privacy_mode:high")],
            [Button.inline("Off", b"tj_privacy_mode:off")],
            [Button.inline("Back", b"tj_settings")]
        ],
        parse_mode="html"
    )


async def _handle_privacy_mode_set(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    from ..utils import parse_callback_str
    mode = parse_callback_str(event.data)
    if mode not in ("off", "low", "medium", "high"):
        return
    level = "medium" if mode == "off" else mode
    await db.set_privacy(uid, mode, level)
    await _handle_privacy_settings(event)


async def _handle_privacy_level_set(event: Any) -> None:
    uid = event.sender_id
    await event.answer()
    await _handle_privacy_settings(event)


async def _show_settings(event: Any, uid: int) -> None:
    from ..ui.design_system import Box, Icons, Labels
    user = await db.get_user(uid)
    has_channel = bool(user and user.get("channel_id"))
    has_margin = bool(user and user.get("account_margin", 0) > 0)

    channel_info = ""
    if has_channel:
        is_valid, validity_msg = await check_channel_validity(event.client, user["channel_id"])
        status = Icons.CHECK if is_valid else Icons.WARNING
        channel_info = f"\n\n📺 <b>کانال:</b> {status} {user.get('channel_title', 'N/A')}"

    channel_count = len(await db.get_user_channels(uid))
    channels_info = f"\n📺 کانال‌ها: <b>{channel_count}</b>" if channel_count > 0 else ""

    templates_count = await db.get_template_count(uid)
    trades_count = await db.get_user_trades_count(uid)

    margin_info = ""
    if has_margin:
        margin_info = f"\n💰 مارجین: <b>{user['account_margin']:.2f}</b>"

    text = (
        f"{Box.TL}{Box.H * 28}{Box.TR}\n"
        f"{Box.V}  ⚙️ <b>{Labels.SETTINGS}</b>  {Box.V}\n"
        f"{Box.BL}{Box.H * 28}{Box.BR}"
        f"{channel_info}"
        f"{channels_info}"
        f"{margin_info}"
        f"\n📋 قالب‌ها: <b>{templates_count}</b>"
        f"\n📊 کل معاملات: <b>{trades_count}</b>"
    )

    await event.edit(
        text,
        buttons=settings_keyboard(has_channel, has_margin),
        parse_mode="html"
    )


async def _show_settings_msg(event: Any, uid: int) -> None:
    user = await db.get_user(uid)
    has_channel = bool(user and user.get("channel_id"))
    has_margin = bool(user and user.get("account_margin", 0) > 0)

    channel_info = ""
    if has_channel:
        is_valid, validity_msg = await check_channel_validity(event.client, user["channel_id"])
        status = "✅" if is_valid else "⚠️"
        channel_info = f"\n\n📺 <b>کانال:</b> {status} {user.get('channel_title', 'N/A')}"

    templates_count = await db.get_template_count(uid)
    trades_count = await db.get_user_trades_count(uid)

    set_state(uid, SHOWING_SETTINGS)
    await event.reply(
        f"╭─────────────────────┐\n"
        f"│  ⚙️ <b>تنظیمات</b>  │\n"
        f"╰─────────────────────╯"
        f"{channel_info}"
        f"\n📋 قالب‌ها: <b>{templates_count}</b>"
        f"\n📊 کل معاملات: <b>{trades_count}</b>",
        buttons=settings_keyboard(has_channel, has_margin),
        parse_mode="html"
    )

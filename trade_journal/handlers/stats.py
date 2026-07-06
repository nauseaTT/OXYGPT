import logging
from typing import Any

# v2: v1 `telethon.tl.custom.Button` factory -> compat `Button` shim, which
# builds v2 `telethon.types.buttons.*` instances under the hood.
from telethon_compat import Button

from ..database import get_trade_stats, get_detailed_analysis
from ..services.trade_service import format_glass_stats_text
from ..ui.design_system import Icons, Labels, section_divider, progress_bar

logger = logging.getLogger(__name__)


async def handle_mystats(event: Any, uid: int) -> None:
    stats = await get_trade_stats(uid)
    analysis = await get_detailed_analysis(uid)

    if analysis["total_trades"] == 0:
        text = "📊 <b>هنوز معامله‌ای ثبت نشده</b>\n\nبا ثبت اولین معامله، آمار شما نمایش داده می‌شود."
    else:
        lines = []

        lines.append(f"{Icons.CHART} <b>{Labels.STATS}</b>")
        lines.append("━" * 28)
        lines.append("")

        lines.append(f"🔢 <b>{Labels.TOTAL_TRADES}:</b> {analysis['total_trades']}")

        win_rate = analysis['win_rate']
        bar = progress_bar(int(win_rate), 100, 15)
        lines.append(f"📈 <b>{Labels.WIN_RATE}:</b> <code>{bar}</code> {win_rate:.1f}%")
        lines.append("")

        pnl_icon = Icons.WIN if analysis['total_pnl'] >= 0 else Icons.LOSS
        lines.append(f"{pnl_icon} <b>{Labels.TOTAL_PNL}:</b> <code>{analysis['total_pnl']:+.2f}</code>")
        lines.append("")

        lines.append(f"🟢 <b>{Labels.WINS}:</b> {analysis['wins']}")
        lines.append(f"🔴 <b>{Labels.LOSSES}:</b> {analysis['losses']}")
        lines.append(f"⚪ <b>بریک‌ایون:</b> {analysis['breakevens']}")
        lines.append("")

        if analysis['profit_factor'] > 0:
            lines.append(f"📊 <b>{Labels.PROFIT_FACTOR}:</b> <code>{analysis['profit_factor']:.2f}</code>")

        if analysis['best_trade']:
            lines.append(f"🌟 <b>{Labels.BEST_TRADE}:</b> <code>{analysis['best_trade']['pnl']:+.2f}</code> ({analysis['best_trade']['symbol']})")

        if analysis['worst_trade']:
            lines.append(f"💥 <b>{Labels.WORST_TRADE}:</b> <code>{analysis['worst_trade']['pnl']:+.2f}</code> ({analysis['worst_trade']['symbol']})")

        lines.append("")
        lines.append("━" * 28)

        if analysis['symbol_breakdown']:
            lines.append(f"<b>برترین نمادها</b>")
            sorted_syms = sorted(analysis['symbol_breakdown'].items(),
                               key=lambda x: x[1]['total_pnl'], reverse=True)[:3]
            for sym, data in sorted_syms:
                pnl_icon = Icons.WIN if data['total_pnl'] >= 0 else Icons.LOSS
                lines.append(f"  {pnl_icon} <b>{sym}</b>: {data['total_pnl']:+.2f}")

        text = "\n".join(lines)

    buttons = [
        [Button.inline("📈 تحلیل جامع", b"tj_detailed_analysis")],
        [Button.inline("🔔 اعلان‌ها", b"tj_notifications")],
        [Button.inline("📊 خلاصه روز", b"tj_daily_summary")],
        [Button.inline("🔙 بازگشت", b"tj_back_journal")],
    ]

    await event.reply(text, buttons=buttons, parse_mode="html")

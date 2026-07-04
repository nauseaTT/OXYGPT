import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from .. import database as db

logger = logging.getLogger(__name__)


async def export_trades_json(user_id: int) -> Dict[str, Any]:
    trades = await db.get_user_trades(user_id)
    user = await db.get_user(user_id)
    stats = await db.get_trade_stats(user_id)

    export = {
        "user_id": user_id,
        "username": user.get("username") if user else None,
        "export_date": datetime.now().isoformat(),
        "summary": {
            "total_trades": stats.get("total", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "win_rate": stats.get("win_rate", 0),
            "total_pnl": stats.get("total_pnl", 0),
        },
        "trades": []
    }

    for t in trades:
        trade = {
            "id": t["id"],
            "symbol": t["symbol"],
            "direction": t["direction"],
            "volume": t["volume"],
            "entry_price": t["entry_price"],
            "stoploss_price": t["stoploss_price"],
            "exit_price": t["exit_price"],
            "trade_date": t["trade_date"],
            "pnl": t["pnl"],
            "pip_diff": t.get("pip_diff"),
            "dollar_pnl": t.get("dollar_pnl"),
            "multiple_risk": t.get("multiple_risk"),
            "risk_reward_ratio": t["risk_reward_ratio"],
            "custom_data": t.get("custom_data", {}),
            "created_at": t["created_at"],
        }
        export["trades"].append(trade)

    return export


async def format_trades_for_llm(user_id: int, max_trades: int = 50) -> str:
    data = await export_trades_json(user_id)
    summary = data["summary"]
    trades = data["trades"][:max_trades]

    lines = []
    lines.append(f"## Trading Journal Data for User {user_id}")
    lines.append(f"Export Date: {data['export_date']}")
    lines.append("")
    lines.append("### Summary")
    lines.append(f"- Total Trades: {summary['total_trades']}")
    lines.append(f"- Wins: {summary['wins']}")
    lines.append(f"- Losses: {summary['losses']}")
    lines.append(f"- Win Rate: {summary['win_rate']:.1f}%")
    lines.append(f"- Total P&L: {(summary['total_pnl'] or 0):.2f}")
    lines.append("")

    if not trades:
        lines.append("No trades recorded yet.")
        return "\n".join(lines)

    lines.append("### Trade History")
    lines.append("")

    for t in trades:
        direction_emoji = "📈" if t["direction"] == "LONG" else "📉"
        pnl_emoji = "🟢" if (t["pnl"] or 0) >= 0 else "🔴"

        lines.append(f"**Trade #{t['id']}** - {t['trade_date'] or 'N/A'}")
        lines.append(f"- Symbol: {t['symbol']} | Direction: {direction_emoji} {t['direction']}")
        lines.append(f"- Volume: {t['volume']} | Entry: {t['entry_price']} | SL: {t['stoploss_price']} | Exit: {t['exit_price']}")
        if t['pnl'] is not None:
            lines.append(f"- {pnl_emoji} P&L: {t['pnl']:.2f} | R:R: {t['risk_reward_ratio']:.2f}")
        else:
            lines.append("- P&L: N/A")
        if t.get("custom_data"):
            for k, v in t["custom_data"].items():
                lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines)


async def generate_analysis_prompt(user_id: int) -> str:
    trade_data = await format_trades_for_llm(user_id)

    prompt = f"""You are a professional trading analyst. Analyze the following trading journal data and provide insights.

{trade_data}

Please provide:
1. **Performance Summary**: Overall assessment of trading performance
2. **Strengths**: What the trader does well
3. **Weaknesses**: Areas for improvement
4. **Pattern Analysis**: Any patterns in winning/losing trades
5. **Risk Management Review**: Assessment of risk management practices
6. **Recommendations**: Specific actionable suggestions for improvement
7. **Symbol Analysis**: Performance breakdown by symbol
8. **Direction Bias**: Analysis of LONG vs SHORT performance

Be specific, data-driven, and actionable in your analysis."""

    return prompt


async def get_symbol_breakdown(user_id: int) -> Dict[str, Any]:
    trades = await db.get_user_trades(user_id)
    breakdown = {}

    for t in trades:
        symbol = t["symbol"]
        if symbol not in breakdown:
            breakdown[symbol] = {
                "total": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "long_count": 0, "short_count": 0
            }
        breakdown[symbol]["total"] += 1
        pnl = t.get("pnl") or 0
        breakdown[symbol]["total_pnl"] += pnl
        if pnl > 0:
            breakdown[symbol]["wins"] += 1
        elif pnl < 0:
            breakdown[symbol]["losses"] += 1
        if t["direction"] == "LONG":
            breakdown[symbol]["long_count"] += 1
        else:
            breakdown[symbol]["short_count"] += 1

    for symbol in breakdown:
        total = breakdown[symbol]["total"]
        if total > 0:
            breakdown[symbol]["win_rate"] = breakdown[symbol]["wins"] / total * 100
        else:
            breakdown[symbol]["win_rate"] = 0

    return breakdown


async def get_direction_breakdown(user_id: int) -> Dict[str, Any]:
    trades = await db.get_user_trades(user_id)
    breakdown = {
        "LONG": {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0},
        "SHORT": {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0},
    }

    for t in trades:
        d = t["direction"]
        if d not in breakdown:
            continue
        breakdown[d]["total"] += 1
        pnl = t.get("pnl") or 0
        breakdown[d]["total_pnl"] += pnl
        if pnl > 0:
            breakdown[d]["wins"] += 1
        elif pnl < 0:
            breakdown[d]["losses"] += 1

    for d in breakdown:
        total = breakdown[d]["total"]
        breakdown[d]["win_rate"] = breakdown[d]["wins"] / total * 100 if total > 0 else 0

    return breakdown


async def format_analysis_text(user_id: int) -> str:
    analysis = await db.get_detailed_analysis(user_id)
    if analysis["total_trades"] == 0:
        return "📊 <b>هنوز معامله‌ای ثبت نشده است.</b>\n\nبا ثبت اولین معامله، تحلیل‌های جامع در این بخش نمایش داده می‌شوند."

    lines = []
    lines.append("📊 <b>داشبورد تحلیلی جامع</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    total = analysis["total_trades"]
    wins = analysis["wins"]
    losses = analysis["losses"]
    breakevens = analysis["breakevens"]
    total_pnl = analysis["total_pnl"]
    win_rate = analysis["win_rate"]

    pnl_symbol = "🟢" if total_pnl >= 0 else "🔴"

    lines.append(f"📈 <b>برآیند کلی</b>")
    lines.append(f"  {pnl_symbol} سود/زیان کل: <b>{total_pnl:+.2f}</b>")
    lines.append(f"  🔢 تعداد کل: <b>{total}</b>")
    lines.append(f"  ✅ برد: <b>{wins}</b>  |  ❌ باخت: <b>{losses}</b>  |  ⚪ بریک‌ایون: <b>{breakevens}</b>")
    lines.append(f"  📈 نرخ برد: <b>{win_rate:.1f}%</b>")
    lines.append(f"  📊 فاکتور سود: <b>{analysis['profit_factor']:.2f}</b>")
    lines.append(f"  🌟 بهترین: <b>{analysis['best_trade']['pnl']:+.2f}</b> ({analysis['best_trade']['symbol']})")
    lines.append(f"  💥 بدترین: <b>{analysis['worst_trade']['pnl']:+.2f}</b> ({analysis['worst_trade']['symbol']})")
    lines.append(f"  🎯 میانگین R: <b>{analysis['avg_r_multiple']:.2f}R</b>")
    if analysis["max_consecutive_wins"] > 0:
        lines.append(f"  🔥 بیشترین برد متوالی: <b>{analysis['max_consecutive_wins']}</b>")
    if analysis["max_consecutive_losses"] > 0:
        lines.append(f"  💥 بیشترین باخت متوالی: <b>{analysis['max_consecutive_losses']}</b>")
    lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<b>بررسی تفکیکی نمادها</b>")
    lines.append("")
    sym_breakdown = analysis["symbol_breakdown"]
    sorted_syms = sorted(sym_breakdown.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    for sym, data in sorted_syms:
        wr = data.get("win_rate", 0)
        sp = "🟢" if data["total_pnl"] >= 0 else "🔴"
        lines.append(f"  {sp} <b>{sym}</b>")
        lines.append(f"     📊 {data['total']} معامله | نرخ برد: {wr:.0f}% | P&L: {data['total_pnl']:+.2f}")
    lines.append("")

    dir_breakdown = analysis["direction_breakdown"]
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<b>تفکیک جهت معاملات</b>")
    lines.append("")
    for d_name, d_data in dir_breakdown.items():
        if d_data["total"] > 0:
            d_emoji = "📈" if d_name == "LONG" else "📉"
            dp = "🟢" if d_data["total_pnl"] >= 0 else "🔴"
            lines.append(f"  {d_emoji} <b>{'خرید' if d_name == 'LONG' else 'فروش'}</b> {dp}")
            lines.append(f"     {d_data['total']} معامله | نرخ برد: {d_data.get('win_rate', 0):.0f}% | P&L: {d_data['total_pnl']:+.2f}")
    lines.append("")

    best_day = analysis.get("best_day")
    worst_day = analysis.get("worst_day")
    if best_day or worst_day:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>تحلیل روزانه</b>")
        lines.append("")
        if best_day:
            lines.append(f"  🌟 پرسودترین روز: <b>{best_day['day']}</b> ({best_day['pnl']:+.2f} در {best_day['trades']} معامله)")
        if worst_day:
            lines.append(f"  💥 پرزیان‌ترین روز: <b>{worst_day['day']}</b> ({worst_day['pnl']:+.2f} در {worst_day['trades']} معامله)")

    hourly = analysis.get("hourly_pnl")
    if hourly:
        best_hour = max(hourly.items(), key=lambda x: x[1]["total_pnl"])
        worst_hour = min(hourly.items(), key=lambda x: x[1]["total_pnl"])
        lines.append(f"  🕐 بهترین ساعت: <b>{best_hour[0]}:00</b> ({best_hour[1]['total_pnl']:+.2f})")
        lines.append(f"  🕐 بدترین ساعت: <b>{worst_hour[0]}:00</b> ({worst_hour[1]['total_pnl']:+.2f})")
    lines.append("")

    user = await db.get_user(user_id)
    margin = user.get("account_margin") if user else None
    if margin and margin > 0:
        roi = (total_pnl / margin) * 100
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>بازده سرمایه (ROI)</b>")
        lines.append("")
        lines.append(f"  💰 سرمایه: <b>{margin:.2f}</b>")
        lines.append(f"  {pnl_symbol} بازده کل: <b>{roi:+.2f}%</b>")
        lines.append("")

    return "\n".join(lines)

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from .. import database as db

logger = logging.getLogger(__name__)


async def check_loss_streak(user_id: int, threshold: int = 3) -> Optional[str]:
    trades = await db.get_user_trades(user_id)
    if not trades:
        return None

    recent = trades[:threshold]
    consecutive_losses = 0

    for t in recent:
        if (t.get("pnl") or 0) < 0:
            consecutive_losses += 1
        else:
            break

    if consecutive_losses >= threshold:
        return (
            f"⚠️ <b>هشدار ریسک</b>\n\n"
            f"شما {consecutive_losses} باخت متوالی دارید.\n"
            f"لطفاً استراتژی خود را بررسی کنید."
        )
    return None


async def check_daily_goal(user_id: int) -> Optional[str]:
    trades = await db.get_user_trades(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    today_trades = [t for t in trades if t.get("trade_date", "").startswith(today)]
    if not today_trades:
        return None

    daily_pnl = sum(t.get("pnl") or 0 for t in today_trades)
    daily_trades = len(today_trades)

    wins = sum(1 for t in today_trades if (t.get("pnl") or 0) > 0)
    win_rate = (wins / daily_trades * 100) if daily_trades > 0 else 0

    messages = []

    if daily_pnl > 0:
        messages.append(f"💰 <b>روز سودده!</b>\nP&L امروز: {daily_pnl:+.2f}")

    if win_rate >= 70 and daily_trades >= 3:
        messages.append(f"🔥 <b>عملکرد عالی!</b>\nWin Rate: {win_rate:.0f}%")

    if daily_trades >= 5:
        messages.append(f"📊 <b>تعداد معاملات بالا</b>\n{daily_trades} معامله امروز")

    return "\n\n".join(messages) if messages else None


async def check_trade_frequency(user_id: int) -> Optional[str]:
    trades = await db.get_user_trades(user_id)
    if len(trades) < 2:
        return None

    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)

    recent_count = 0
    for t in trades[:10]:
        if t.get("created_at"):
            try:
                created = datetime.fromisoformat(t["created_at"])
                if created > one_hour_ago:
                    recent_count += 1
            except Exception:
                pass

    if recent_count >= 5:
        return (
            f"⚠️ <b>تعداد معاملات بالا</b>\n\n"
            f"شما {recent_count} معامله در یک ساعت اخیر داشته‌اید.\n"
            f"لطفاً از overtrading اجتناب کنید."
        )
    return None


async def generate_daily_summary(user_id: int) -> str:
    trades = await db.get_user_trades(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    today_trades = [t for t in trades if t.get("trade_date", "").startswith(today)]

    if not today_trades:
        return "📊 <b>خلاصه روز</b>\n\nامروز معامله‌ای ثبت نشده است."

    total_pnl = sum(t.get("pnl") or 0 for t in today_trades)
    wins = sum(1 for t in today_trades if (t.get("pnl") or 0) > 0)
    losses = len(today_trades) - wins

    win_rate = (wins / len(today_trades) * 100) if today_trades else 0
    pnl_icon = "🟢" if total_pnl >= 0 else "🔴"

    lines = [
        "📊 <b>خلاصه عملکرد امروز</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🔢 تعداد معاملات: <b>{len(today_trades)}</b>",
        f"🟢 بردها: <b>{wins}</b>",
        f"🔴 باخت‌ها: <b>{losses}</b>",
        f"📈 Win Rate: <b>{win_rate:.0f}%</b>",
        "",
        f"{pnl_icon} P&L امروز: <b>{total_pnl:+.2f}</b>",
    ]

    return "\n".join(lines)

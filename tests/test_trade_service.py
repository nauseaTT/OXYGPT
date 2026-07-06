"""Tests for `trade_journal/services/trade_service.py` — PnL / risk math."""

import pytest

from trade_journal.services import trade_service as TS


# ── calculate_pnl ──────────────────────────────────────────────────────
class TestCalculatePnl:
    def test_long_profit(self):
        assert TS.calculate_pnl("LONG", 100, 110, 2) == 20

    def test_long_loss(self):
        assert TS.calculate_pnl("LONG", 100, 90, 1) == -10

    def test_short_profit(self):
        assert TS.calculate_pnl("SHORT", 100, 90, 2) == 20

    def test_short_loss(self):
        assert TS.calculate_pnl("SHORT", 100, 110, 1) == -10

    def test_none_entry(self):
        assert TS.calculate_pnl("LONG", None, 110, 2) is None

    def test_none_exit(self):
        assert TS.calculate_pnl("LONG", 100, None, 2) is None

    def test_none_volume(self):
        assert TS.calculate_pnl("LONG", 100, 110, None) is None

    def test_unknown_direction(self):
        assert TS.calculate_pnl("SIDEWAYS", 100, 110, 2) is None

    def test_breakeven(self):
        assert TS.calculate_pnl("LONG", 100, 100, 5) == 0


# ── calculate_risk ─────────────────────────────────────────────────────
class TestCalculateRisk:
    def test_long(self):
        assert TS.calculate_risk("LONG", 100, 90, 2) == 20

    def test_short(self):
        assert TS.calculate_risk("SHORT", 100, 110, 2) == 20

    def test_none_stoploss(self):
        assert TS.calculate_risk("LONG", 100, None, 2) is None

    def test_unknown_direction(self):
        assert TS.calculate_risk("X", 100, 90, 2) is None


# ── calculate_rr_ratio ─────────────────────────────────────────────────
class TestRRRatio:
    def test_basic(self):
        assert TS.calculate_rr_ratio(30, 10) == 3.0

    def test_uses_abs(self):
        assert TS.calculate_rr_ratio(-30, 10) == 3.0
        assert TS.calculate_rr_ratio(30, -10) == 3.0

    def test_zero_risk(self):
        assert TS.calculate_rr_ratio(30, 0) == 0.0

    def test_none_pnl(self):
        assert TS.calculate_rr_ratio(None, 10) == 0.0

    def test_none_risk(self):
        assert TS.calculate_rr_ratio(30, None) == 0.0


# ── calculate_pip_diff ─────────────────────────────────────────────────
class TestPipDiff:
    def test_none_entry(self):
        assert TS.calculate_pip_diff("LONG", None, 110) is None

    def test_forex_scale(self):
        # diff = 0.005 < 1 → *10000
        assert TS.calculate_pip_diff("LONG", 1.1000, 1.1050) == pytest.approx(50, rel=1e-3)

    def test_mid_scale(self):
        # diff = 5 (<10 but >=1) → *100
        assert TS.calculate_pip_diff("LONG", 100, 105) == 500

    def test_large_scale_unchanged(self):
        # diff = 100 → unchanged
        assert TS.calculate_pip_diff("LONG", 1000, 1100) == 100

    def test_short_direction(self):
        assert TS.calculate_pip_diff("SHORT", 105, 100) == 500


# ── compute_trade_calculations ─────────────────────────────────────────
class TestComputeTradeCalculations:
    def test_full_long(self):
        filled = {
            "direction": "LONG", "entry_price": "100", "exit_price": "110",
            "stoploss": "90", "volume": "2",
        }
        pnl, risk, rr, pip = TS.compute_trade_calculations(filled)
        assert pnl == 20
        assert risk == 20
        assert rr == 1.0
        assert pip is not None

    def test_missing_exit(self):
        filled = {"direction": "LONG", "entry_price": "100", "volume": "2"}
        pnl, risk, rr, pip = TS.compute_trade_calculations(filled)
        assert pnl is None
        assert pip is None

    def test_empty_dict(self):
        pnl, risk, rr, pip = TS.compute_trade_calculations({})
        assert pnl is None and risk is None and rr == 0.0 and pip is None

    def test_persian_numbers(self):
        filled = {
            "direction": "LONG", "entry_price": "۱۰۰", "exit_price": "۱۱۰",
            "stoploss": "۹۰", "volume": "۲",
        }
        pnl, risk, rr, pip = TS.compute_trade_calculations(filled)
        assert pnl == 20


# ── validate_trade_values ──────────────────────────────────────────────
class TestValidateTradeValues:
    def test_valid_long(self):
        filled = {
            "direction": "LONG", "entry_price": "100",
            "stoploss": "90", "exit_price": "110", "volume": "1",
        }
        assert TS.validate_trade_values(filled) == []

    def test_negative_volume(self):
        errors = TS.validate_trade_values({"volume": "-1"})
        assert any("حجم" in e for e in errors)

    def test_zero_entry(self):
        errors = TS.validate_trade_values({"entry_price": "0"})
        assert any("ورود" in e for e in errors)

    def test_invalid_volume_string(self):
        errors = TS.validate_trade_values({"volume": "abc"})
        assert any("معتبر" in e for e in errors)

    def test_long_stoploss_above_entry(self):
        filled = {"direction": "LONG", "entry_price": "100", "stoploss": "110"}
        errors = TS.validate_trade_values(filled)
        assert any("LONG" in e for e in errors)

    def test_short_stoploss_below_entry(self):
        filled = {"direction": "SHORT", "entry_price": "100", "stoploss": "90"}
        errors = TS.validate_trade_values(filled)
        assert any("SHORT" in e for e in errors)

    def test_valid_short(self):
        filled = {"direction": "SHORT", "entry_price": "100", "stoploss": "110"}
        assert TS.validate_trade_values(filled) == []


# ── format_pnl / format_pnl_percent ────────────────────────────────────
class TestFormatPnl:
    def test_none(self):
        assert TS.format_pnl(None) == "N/A"

    def test_positive_sign(self):
        assert TS.format_pnl(12.5) == "+12.50"

    def test_negative_sign(self):
        assert TS.format_pnl(-12.5) == "-12.50"

    def test_zero(self):
        assert TS.format_pnl(0) == "+0.00"

    def test_percent_none(self):
        assert TS.format_pnl_percent(None) == ""

    def test_percent_no_margin(self):
        assert TS.format_pnl_percent(50) == ""

    def test_percent_with_margin(self):
        assert TS.format_pnl_percent(50, 1000) == " (+5.00%)"

    def test_percent_zero_margin(self):
        assert TS.format_pnl_percent(50, 0) == ""


# ── format_stats_text_farsi / glass ────────────────────────────────────
class TestFormatStats:
    def _stats(self):
        return {
            "total": 10, "wins": 6, "losses": 4, "total_pnl": 150.5,
            "win_rate": 60.0, "avg_win": 40.0, "avg_loss": -20.0,
            "profit_factor": 2.0, "best_trade": 80.0, "worst_trade": -30.0,
        }

    def test_farsi_contains_total(self):
        out = TS.format_stats_text_farsi(self._stats())
        assert "10" in out and "آمار" in out

    def test_farsi_green_when_profit(self):
        out = TS.format_stats_text_farsi(self._stats())
        assert "🟢" in out

    def test_farsi_red_when_loss(self):
        s = self._stats(); s["total_pnl"] = -50
        out = TS.format_stats_text_farsi(s)
        assert "🔴" in out

    def test_farsi_empty_stats(self):
        out = TS.format_stats_text_farsi({})
        assert "آمار" in out

    def test_glass_contains_dashboard(self):
        out = TS.format_glass_stats_text(self._stats())
        assert "داشبورد" in out

    def test_glass_empty_stats(self):
        out = TS.format_glass_stats_text({})
        assert isinstance(out, str)


# ── get_journal_hashtags ───────────────────────────────────────────────
class TestJournalHashtags:
    def test_symbol_and_direction_long(self):
        out = TS.get_journal_hashtags({"symbol": "EURUSD", "direction": "LONG"})
        assert "#EURUSD" in out
        assert "#خرید" in out

    def test_direction_short(self):
        out = TS.get_journal_hashtags({"direction": "SHORT"})
        assert "#فروش" in out

    def test_profit_tag(self):
        out = TS.get_journal_hashtags({}, pnl=100)
        assert "#سود" in out

    def test_loss_tag(self):
        out = TS.get_journal_hashtags({}, pnl=-100)
        assert "#ضرر" in out

    def test_breakeven_tag(self):
        out = TS.get_journal_hashtags({}, pnl=0)
        assert "#بریک_ایون" in out

    def test_strategy_underscored(self):
        out = TS.get_journal_hashtags({"strategy": "break out"})
        assert "#break_out" in out

    def test_always_has_journal_tag(self):
        out = TS.get_journal_hashtags({})
        assert "#ژورنال_معاملاتی" in out

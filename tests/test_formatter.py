"""Tests for `channel_watcher/ui/formatter.py`."""

import pytest

from channel_watcher.ui import formatter as F


# ── _ensure_rtl ───────────────────────────────────────────────────────────
class TestEnsureRtl:
    def test_prepends_rlm_to_persian(self):
        out = F._ensure_rtl("سلام")
        assert out.startswith("\u200F")

    def test_ascii_line_unchanged(self):
        out = F._ensure_rtl("hello world")
        assert not out.startswith("\u200F")

    def test_html_tag_line_not_prefixed(self):
        out = F._ensure_rtl("<b>سلام</b>")
        assert not out.startswith("\u200F")

    def test_empty_passthrough(self):
        assert F._ensure_rtl("") == ""

    def test_code_fence_unchanged(self):
        out = F._ensure_rtl("```\nسلام\n```")
        lines = out.split("\n")
        assert lines[0] == "```"

    def test_idempotent(self):
        once = F._ensure_rtl("سلام")
        twice = F._ensure_rtl(once)
        assert once == twice

    def test_mixed_lines(self):
        out = F._ensure_rtl("hello\nسلام")
        lines = out.split("\n")
        assert not lines[0].startswith("\u200F")
        assert lines[1].startswith("\u200F")


# ── _fmt_time ─────────────────────────────────────────────────────────────
class TestFmtTime:
    def test_valid_iso(self):
        out = F._fmt_time("2024-01-15T10:30:00")
        assert "2024/01/15" in out

    def test_invalid_returns_input(self):
        assert F._fmt_time("not-a-date") == "not-a-date"

    def test_none_returns_input(self):
        assert F._fmt_time(None) is None

    def test_has_time_component(self):
        out = F._fmt_time("2024-06-01T08:05:00")
        assert ":" in out


# ── _importance_badge ─────────────────────────────────────────────────────
class TestImportanceBadge:
    def test_high(self):
        assert "بالا" in F._importance_badge("high")

    def test_medium(self):
        assert "متوسط" in F._importance_badge("medium")

    def test_low(self):
        assert "پایین" in F._importance_badge("low")

    def test_unknown(self):
        assert "نامشخص" in F._importance_badge("bogus")


# ── format_analysis_card ──────────────────────────────────────────────────
class TestAnalysisCard:
    def _analysis(self):
        return {
            "summary": "خلاصه مهم",
            "key_points": ["نکته ۱", "نکته ۲"],
            "topics": ["کریپتو"],
            "importance": "high",
            "analyzed_at": "2024-01-15T10:00:00",
        }

    def test_contains_title(self):
        out = F.format_analysis_card("مانیتور", "chan", self._analysis())
        assert "مانیتور" in out

    def test_contains_summary(self):
        out = F.format_analysis_card("m", "chan", self._analysis())
        assert "خلاصه مهم" in out

    def test_contains_key_points(self):
        out = F.format_analysis_card("m", "chan", self._analysis())
        assert "نکته ۱" in out

    def test_contains_post_link(self):
        out = F.format_analysis_card("m", "chan", self._analysis(), post_id=42)
        assert "t.me/chan/42" in out

    def test_pagination_line(self):
        out = F.format_analysis_card("m", "chan", self._analysis(), index=1, total=3)
        assert "۱" in out or "1" in out

    def test_no_pagination_when_single(self):
        out = F.format_analysis_card("m", "chan", self._analysis(), index=1, total=1)
        assert "پیام 1 از 1" not in out

    def test_empty_summary_omitted(self):
        a = self._analysis(); a["summary"] = ""
        out = F.format_analysis_card("m", "chan", a)
        assert "خلاصه:" not in out


# ── format_monitor_list_item ──────────────────────────────────────────────
class TestMonitorListItem:
    def test_active_icon(self):
        out = F.format_monitor_list_item({"channel_title": "T", "is_paused": False})
        assert "📡" in out

    def test_paused_icon(self):
        out = F.format_monitor_list_item({"channel_title": "T", "is_paused": True})
        assert "⏸" in out

    def test_hours_interval(self):
        out = F.format_monitor_list_item({"interval_hours": 4})
        assert "4h" in out

    def test_days_interval(self):
        out = F.format_monitor_list_item({"interval_hours": 48})
        assert "2d" in out

    def test_pm_delivery(self):
        out = F.format_monitor_list_item({"delivery_method": "pm"})
        assert "📨" in out


# ── format_stats_text ─────────────────────────────────────────────────────
class TestStatsText:
    def test_empty_stats_message(self):
        out = F.format_stats_text("T", [], [0] * 24)
        assert "هنوز داده" in out

    def test_with_data(self):
        stats = [{"date": "2024-01-01", "total_posts": 10, "analyzed_posts": 5}]
        hourly = [0] * 24
        hourly[9] = 8
        out = F.format_stats_text("T", stats, hourly)
        assert "کل پست‌ها: 10" in out
        assert "09:00" in out


# ── format_history_text ───────────────────────────────────────────────────
class TestHistoryText:
    def test_empty(self):
        out = F.format_history_text("T", [])
        assert "هنوز تحلیلی" in out

    def test_with_analyses(self):
        analyses = [{"importance": "high", "analyzed_at": "2024-01-01T10:00:00", "summary": "خلاصه"}]
        out = F.format_history_text("T", analyses)
        assert "خلاصه" in out

    def test_long_summary_truncated(self):
        analyses = [{"summary": "x" * 200, "importance": "low", "analyzed_at": ""}]
        out = F.format_history_text("T", analyses)
        assert "…" in out


# ── format_settings_text ──────────────────────────────────────────────────
class TestSettingsText:
    def test_active(self):
        out = F.format_settings_text({"is_paused": False}, "بعدی")
        assert "فعال" in out

    def test_paused(self):
        out = F.format_settings_text({"is_paused": True}, "بعدی")
        assert "متوقف" in out

    def test_important_filter(self):
        out = F.format_settings_text({"importance_filter": "important"}, "x")
        assert "فقط مهم" in out


# ── format_ask_ai_intro / answer ──────────────────────────────────────────
class TestAskAi:
    def test_intro_summary_mode(self):
        out = F.format_ask_ai_intro({"summary": "خلاصه", "importance": "high"})
        assert "خلاصه" in out

    def test_intro_full_text_mode(self):
        out = F.format_ask_ai_intro(
            {"message_text": "متن کامل", "importance": "low"}, show_full_text=True
        )
        assert "متن کامل" in out

    def test_answer_contains_qa(self):
        out = F.format_ask_ai_answer("سوال؟", "جواب.", turn_count=1, max_turns=5)
        assert "سوال؟" in out and "جواب." in out

    def test_answer_warns_near_limit(self):
        out = F.format_ask_ai_answer("q", "a", turn_count=4, max_turns=5)
        assert "سوال دیگر" in out

    def test_answer_at_limit(self):
        out = F.format_ask_ai_answer("q", "a", turn_count=5, max_turns=5)
        assert "سقف" in out

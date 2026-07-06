"""Tests for `telegram/utils.py` — pure formatting/number helpers."""

import re
from datetime import datetime, timedelta

import pytest

from telegram import utils as U


# ── clean_number_string ────────────────────────────────────────────────
class TestCleanNumberString:
    def test_english_passthrough(self):
        assert U.clean_number_string("1234567890") == "1234567890"

    def test_persian_digits(self):
        assert U.clean_number_string("۰۱۲۳۴۵۶۷۸۹") == "0123456789"

    def test_arabic_digits(self):
        # This is the regression case for the U+06F7 vs U+0667 bug.
        assert U.clean_number_string("١٢٣٤٥٦٧٨٩٠") == "1234567890"

    def test_arabic_seven_is_translated(self):
        # ٧ is U+0667; must map to "7".
        assert U.clean_number_string("٧") == "7"

    def test_persian_seven_is_translated(self):
        # ۷ is U+06F7; must map to "7".
        assert U.clean_number_string("۷") == "7"

    def test_mixed_scripts(self):
        assert U.clean_number_string("۱2٣4") == "1234"

    def test_strips_non_numeric(self):
        assert U.clean_number_string("price: $12,345.67") == "1234567"

    def test_keeps_minus_sign(self):
        assert U.clean_number_string("-42") == "-42"

    def test_keeps_minus_with_persian(self):
        assert U.clean_number_string("-۴۲") == "-42"

    def test_empty_string(self):
        assert U.clean_number_string("") == ""

    def test_only_letters(self):
        assert U.clean_number_string("abcXYZ") == ""

    def test_accepts_int_input(self):
        assert U.clean_number_string(12345) == "12345"

    def test_accepts_float_input(self):
        # "." is stripped, "-" kept.
        assert U.clean_number_string(-12.5) == "-125"

    def test_whitespace_stripped(self):
        assert U.clean_number_string("  1 2 3  ") == "123"

    def test_persian_thousand_separator(self):
        assert U.clean_number_string("۱٬۰۰۰") == "1000"

    def test_every_arabic_digit_individually(self):
        arabic = "٠١٢٣٤٥٦٧٨٩"
        for i, ch in enumerate(arabic):
            assert U.clean_number_string(ch) == str(i)

    def test_every_persian_digit_individually(self):
        persian = "۰۱۲۳۴۵۶۷۸۹"
        for i, ch in enumerate(persian):
            assert U.clean_number_string(ch) == str(i)


# ── make_progress_bar ──────────────────────────────────────────────────
class TestMakeProgressBar:
    def test_zero(self):
        bar = U.make_progress_bar(0, 100)
        assert bar.endswith("0%")
        assert "█" not in bar

    def test_full(self):
        bar = U.make_progress_bar(100, 100)
        assert bar.endswith("100%")
        assert "░" not in bar

    def test_half(self):
        bar = U.make_progress_bar(50, 100)
        assert bar.endswith("50%")

    def test_length_respected(self):
        bar = U.make_progress_bar(50, 100, length=20)
        block_part = bar.split(" ")[0]
        assert len(block_part) == 20

    def test_over_max_clamped_to_100(self):
        bar = U.make_progress_bar(200, 100)
        assert bar.endswith("100%")

    def test_negative_current_clamped_to_zero(self):
        bar = U.make_progress_bar(-50, 100)
        assert bar.endswith("0%")

    def test_default_length_is_ten(self):
        bar = U.make_progress_bar(100, 100)
        assert len(bar.split(" ")[0]) == 10

    def test_percent_is_integer_format(self):
        bar = U.make_progress_bar(33, 100)
        m = re.search(r"(\d+)%$", bar)
        assert m is not None
        assert m.group(1) == "33"

    @pytest.mark.parametrize("cur,mx,expected", [
        (25, 100, "25%"),
        (75, 100, "75%"),
        (1, 3, "33%"),
        (2, 3, "66%"),
    ])
    def test_various_percentages(self, cur, mx, expected):
        assert U.make_progress_bar(cur, mx).endswith(expected)


# ── safe_html_truncate ─────────────────────────────────────────────────
class TestSafeHtmlTruncate:
    def test_strips_tags(self):
        assert U.safe_html_truncate("<b>hi</b>") == "hi"

    def test_unescapes_entities(self):
        assert U.safe_html_truncate("a &amp; b") == "a & b"

    def test_collapses_whitespace(self):
        assert U.safe_html_truncate("a    b\n\nc") == "a b c"

    def test_short_text_unchanged(self):
        assert U.safe_html_truncate("hello", max_length=100) == "hello"

    def test_truncates_long_text(self):
        text = "x" * 2000
        out = U.safe_html_truncate(text, max_length=100)
        assert len(out) == 100
        assert out.endswith("...")

    def test_exact_length_not_truncated(self):
        text = "x" * 50
        out = U.safe_html_truncate(text, max_length=50)
        assert out == text

    def test_nested_tags_removed(self):
        assert U.safe_html_truncate("<div><span>x</span></div>") == "x"

    def test_default_max_length(self):
        text = "y" * 5000
        out = U.safe_html_truncate(text)
        assert len(out) == 1024

    def test_empty(self):
        assert U.safe_html_truncate("") == ""

    def test_link_tag_stripped_keeps_text(self):
        assert U.safe_html_truncate('<a href="x">link</a>') == "link"


# ── time helpers ───────────────────────────────────────────────────────
class TestTimeHelpers:
    def test_get_time_until_reset_format(self):
        out = U.get_time_until_reset()
        assert "ساعت" in out and "دقیقه" in out

    def test_get_last_reset_returns_datetime(self):
        assert isinstance(U.get_last_reset_time(), datetime)

    def test_last_reset_is_in_past(self):
        last = U.get_last_reset_time()
        now = datetime.now(U.tz_tehran)
        assert last <= now

    def test_last_reset_hour_is_11_or_23(self):
        assert U.get_last_reset_time().hour in (11, 23)

    def test_reset_within_12_hours(self):
        last = U.get_last_reset_time()
        now = datetime.now(U.tz_tehran)
        assert (now - last) <= timedelta(hours=12, minutes=1)


# ── _format_window_entry ───────────────────────────────────────────────
class TestFormatWindowEntry:
    def test_active_window_has_green(self):
        out = U._format_window_entry({"title": "W1", "is_active": True})
        assert "🟢" in out
        assert "←" in out

    def test_inactive_window_has_white(self):
        out = U._format_window_entry({"title": "W2", "is_active": False})
        assert "⚪️" in out
        assert "←" not in out

    def test_title_is_bolded(self):
        out = U._format_window_entry({"title": "MyWin", "is_active": False})
        assert "<b>MyWin</b>" in out

    def test_rtl_mark_present(self):
        out = U._format_window_entry({"title": "x", "is_active": True})
        assert "\u200f" in out

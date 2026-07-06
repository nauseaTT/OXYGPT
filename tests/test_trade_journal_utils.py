"""Tests for `trade_journal/utils.py`."""

import pytest

from trade_journal import utils as TU


# ── markdown_escape ────────────────────────────────────────────────────
class TestMarkdownEscape:
    @pytest.mark.parametrize("ch", list(r'_*[]()~`>#+-=|{}.!\\'))
    def test_special_chars_escaped(self, ch):
        assert TU.markdown_escape(ch) == "\\" + ch

    def test_plain_text_unchanged(self):
        assert TU.markdown_escape("hello world") == "hello world"

    def test_mixed(self):
        assert TU.markdown_escape("a.b") == "a\\.b"

    def test_non_str_input(self):
        assert TU.markdown_escape(123) == "123"

    def test_empty(self):
        assert TU.markdown_escape("") == ""


# ── clean_number_string ────────────────────────────────────────────────
class TestCleanNumberString:
    def test_persian(self):
        assert TU.clean_number_string("۱۲۳") == "123"

    def test_arabic(self):
        assert TU.clean_number_string("١٢٣") == "123"

    def test_arabic_full_set(self):
        assert TU.clean_number_string("٠١٢٣٤٥٦٧٨٩") == "0123456789"

    def test_removes_commas(self):
        assert TU.clean_number_string("1,000,000") == "1000000"

    def test_strips_whitespace(self):
        assert TU.clean_number_string("  42  ") == "42"

    def test_empty_returns_empty(self):
        assert TU.clean_number_string("") == ""

    def test_none_returns_empty(self):
        assert TU.clean_number_string(None) == ""


# ── is_valid_number ────────────────────────────────────────────────────
class TestIsValidNumber:
    @pytest.mark.parametrize("val", ["42", "-3.14", "۱۲۳", "1,000", "0"])
    def test_valid(self, val):
        assert TU.is_valid_number(val) is True

    @pytest.mark.parametrize("val", ["abc", "", "12abc", "--5"])
    def test_invalid(self, val):
        assert TU.is_valid_number(val) is False


# ── parse_number ───────────────────────────────────────────────────────
class TestParseNumber:
    def test_basic(self):
        assert TU.parse_number("42") == 42.0

    def test_float(self):
        assert TU.parse_number("3.14") == 3.14

    def test_persian(self):
        assert TU.parse_number("۱۲۳") == 123.0

    def test_comma(self):
        assert TU.parse_number("1,500") == 1500.0

    def test_negative(self):
        assert TU.parse_number("-5") == -5.0

    def test_invalid_returns_none(self):
        assert TU.parse_number("abc") is None

    def test_empty_returns_none(self):
        assert TU.parse_number("") is None


# ── format_number ──────────────────────────────────────────────────────
class TestFormatNumber:
    def test_none(self):
        assert TU.format_number(None) == "N/A"

    def test_integer_value(self):
        assert TU.format_number(42.0) == "42"

    def test_decimal_value(self):
        assert TU.format_number(3.14159) == "3.14"

    def test_custom_decimals(self):
        assert TU.format_number(3.14159, decimals=4) == "3.1416"

    def test_thousand_separator(self):
        assert TU.format_number(1234.5) == "1,234.50"

    def test_zero(self):
        assert TU.format_number(0) == "0"

    def test_negative_integer(self):
        assert TU.format_number(-10.0) == "-10"


# ── truncate_text ──────────────────────────────────────────────────────
class TestTruncateText:
    def test_short_unchanged(self):
        assert TU.truncate_text("abc", 20) == "abc"

    def test_exact_unchanged(self):
        assert TU.truncate_text("x" * 20, 20) == "x" * 20

    def test_long_truncated(self):
        out = TU.truncate_text("x" * 50, 20)
        assert len(out) == 20
        assert out.endswith("...")

    def test_default_max(self):
        out = TU.truncate_text("y" * 100)
        assert len(out) == 20


# ── callback parsers ───────────────────────────────────────────────────
class TestCallbackParsers:
    def test_get_parts_str(self):
        assert TU.get_callback_parts("a:b:c") == ["a", "b", "c"]

    def test_get_parts_bytes(self):
        assert TU.get_callback_parts(b"x:y") == ["x", "y"]

    def test_get_parts_none(self):
        assert TU.get_callback_parts(None) == []

    def test_parse_int(self):
        assert TU.parse_callback_int("act:42") == 42

    def test_parse_int_bytes(self):
        assert TU.parse_callback_int(b"act:7") == 7

    def test_parse_int_out_of_range(self):
        assert TU.parse_callback_int("act") is None

    def test_parse_int_non_numeric(self):
        assert TU.parse_callback_int("act:xyz") is None

    def test_parse_int_custom_idx(self):
        assert TU.parse_callback_int("a:b:99", idx=2) == 99

    def test_parse_str(self):
        assert TU.parse_callback_str("act:hello") == "hello"

    def test_parse_str_out_of_range(self):
        assert TU.parse_callback_str("act") is None

    def test_parse_str_custom_idx(self):
        assert TU.parse_callback_str("a:b:c", idx=2) == "c"


# ── auto_delete_message ────────────────────────────────────────────────
class TestAutoDeleteMessage:
    async def test_deletes_after_delay(self):
        calls = []

        class FakeClient:
            async def delete_messages(self, chat, mid):
                calls.append((chat, mid))

        await TU.auto_delete_message(FakeClient(), 10, 20, delay=0)
        assert calls == [(10, 20)]

    async def test_swallows_exceptions(self):
        class FakeClient:
            async def delete_messages(self, chat, mid):
                raise RuntimeError("boom")

        # Should not raise.
        await TU.auto_delete_message(FakeClient(), 1, 2, delay=0)

"""Tests for `channel_watcher/services/analyzer.py` pure functions."""

import pytest

from channel_watcher.services import analyzer as A


# ── sanitize_interests ───────────────────────────────────────────────────
class TestSanitizeInterests:
    def test_none(self):
        assert A.sanitize_interests(None) is None

    def test_empty(self):
        assert A.sanitize_interests("") is None

    def test_whitespace_only_returns_none(self):
        assert A.sanitize_interests("   \n  ") is None

    def test_strips_fence_tokens(self):
        out = A.sanitize_interests("crypto <<< inject >>> more")
        assert "<<<" not in out and ">>>" not in out

    def test_collapses_spaces(self):
        assert A.sanitize_interests("a    b") == "a b"

    def test_collapses_blank_lines(self):
        out = A.sanitize_interests("a\n\n\n\nb")
        assert out == "a\n\nb"

    def test_length_cap(self):
        out = A.sanitize_interests("x" * 1000)
        assert len(out) <= A.MAX_INTERESTS_LEN

    def test_keeps_newlines_and_tabs(self):
        out = A.sanitize_interests("line1\nline2")
        assert "\n" in out

    def test_removes_control_chars(self):
        out = A.sanitize_interests("a\x00\x07b")
        assert "\x00" not in out and "\x07" not in out


# ── build_user_prompt ────────────────────────────────────────────────────
class TestBuildUserPrompt:
    def test_contains_base(self):
        out = A.build_user_prompt(None, "all")
        assert "تحلیلگر" in out

    def test_appends_interests_block(self):
        out = A.build_user_prompt("crypto trading", "all")
        assert A._FENCE_OPEN in out
        assert "crypto trading" in out

    def test_no_interests_block_when_empty(self):
        out = A.build_user_prompt(None, "all")
        assert A._FENCE_OPEN not in out

    def test_important_filter_adds_instruction(self):
        out = A.build_user_prompt(None, "important")
        assert "high" in out

    def test_always_ends_with_contract(self):
        out = A.build_user_prompt("x", "important")
        assert out.rstrip().endswith(A._OUTPUT_CONTRACT_REMINDER.strip()[-20:])


# ── sanitize_for_ask_ai_fence ────────────────────────────────────────────
class TestSanitizeAskAi:
    def test_empty_returns_empty_string(self):
        assert A.sanitize_for_ask_ai_fence(None) == ""

    def test_strips_fences(self):
        out = A.sanitize_for_ask_ai_fence("post <<<x>>> end")
        assert "<<<" not in out and ">>>" not in out

    def test_truncates_with_ellipsis(self):
        out = A.sanitize_for_ask_ai_fence("x" * 3000, max_len=100)
        assert len(out) <= 101
        assert out.endswith("…")

    def test_short_no_ellipsis(self):
        out = A.sanitize_for_ask_ai_fence("short", max_len=100)
        assert not out.endswith("…")

    def test_collapses_whitespace(self):
        assert A.sanitize_for_ask_ai_fence("a    b") == "a b"


# ── _parse_response ───────────────────────────────────────────────────────
class TestParseResponse:
    def _analyzer(self):
        return A.ChannelAnalyzer(bot_instance=None)

    def test_direct_json(self):
        out = self._analyzer()._parse_response(
            '{"summary": "s", "key_points": ["a"], "importance": "high", "topics": ["t"]}'
        )
        assert out["summary"] == "s"
        assert out["importance"] == "high"

    def test_json_in_code_fence(self):
        out = self._analyzer()._parse_response(
            '```json\n{"summary": "s", "importance": "low"}\n```'
        )
        assert out["summary"] == "s"
        assert out["importance"] == "low"

    def test_defaults_when_missing_fields(self):
        out = self._analyzer()._parse_response('{"summary": "only"}')
        assert out["importance"] == "medium"
        assert out["key_points"] == []

    def test_regex_fallback(self):
        text = 'garbage "summary": "extracted" more "importance": "high" junk'
        out = self._analyzer()._parse_response(text)
        assert out is not None
        assert out["summary"] == "extracted"

    def test_returns_none_on_garbage(self):
        assert self._analyzer()._parse_response("total nonsense no json") is None


# ── _extract_with_regex ───────────────────────────────────────────────────
class TestExtractWithRegex:
    def _analyzer(self):
        return A.ChannelAnalyzer(bot_instance=None)

    def test_extracts_key_points(self):
        text = '"summary": "s", "key_points": ["p1", "p2"]'
        out = self._analyzer()._extract_with_regex(text)
        assert out["key_points"] == ["p1", "p2"]

    def test_extracts_topics(self):
        text = '"summary": "s", "topics": ["t1"]'
        out = self._analyzer()._extract_with_regex(text)
        assert out["topics"] == ["t1"]

    def test_importance_only_valid_values(self):
        text = '"summary": "s", "importance": "high"'
        out = self._analyzer()._extract_with_regex(text)
        assert out["importance"] == "high"

    def test_none_without_summary(self):
        assert self._analyzer()._extract_with_regex('"importance": "high"') is None


# ── ChannelClassifier init ────────────────────────────────────────────────
class TestClassifierInit:
    def test_sanitizes_interests(self):
        c = A.ChannelClassifier(bot_instance=None, model_name="m", interests="  crypto  ")
        assert c.interests == "crypto"

    def test_none_interests(self):
        c = A.ChannelClassifier(bot_instance=None, model_name="m")
        assert c.interests is None

    def test_stores_model(self):
        c = A.ChannelClassifier(bot_instance=None, model_name="gemini-x")
        assert c.model_name == "gemini-x"

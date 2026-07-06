"""Tests for `telegram/constants.py` — data integrity + ToolEvent dataclass.

Also guards the oxygpt-workflow house rule that the forbidden two-letter
"AI" token never appears in the user-facing HELP_TEXT.
"""

import re

import pytest

from telegram import constants as K


# ── ToolEvent dataclass ───────────────────────────────────────────────────
class TestToolEvent:
    def test_minimal_construction(self):
        e = K.ToolEvent(tool="search", label="Searching")
        assert e.tool == "search"
        assert e.label == "Searching"

    def test_defaults(self):
        e = K.ToolEvent(tool="image", label="x")
        assert e.query == ""
        assert e.sources == []
        assert e.progress == 0.0
        assert e.stage == ""
        assert e.detail == ""

    def test_sources_independent_lists(self):
        a = K.ToolEvent(tool="t", label="l")
        b = K.ToolEvent(tool="t", label="l")
        a.sources.append({"uri": "x"})
        assert b.sources == []

    def test_full_construction(self):
        e = K.ToolEvent(
            tool="market", label="Market", query="BTC",
            sources=[{"uri": "u", "title": "t"}], progress=0.5,
            stage="fetch", detail="symbol",
        )
        assert e.progress == 0.5
        assert e.detail == "symbol"


# ── Tips pools ─────────────────────────────────────────────────────────────
class TestTips:
    @pytest.mark.parametrize("pool", [
        "TIPS_QUICKASK", "TIPS_CODING", "TIPS_LEARN", "TIPS_DEEPTHINK", "TIPS_GENERAL",
    ])
    def test_pool_is_nonempty_list(self, pool):
        val = getattr(K, pool)
        assert isinstance(val, list)
        assert len(val) > 0
        assert all(isinstance(s, str) and s for s in val)

    def test_mentor_tips_is_dict(self):
        assert isinstance(K.TIPS_MENTOR, dict)
        assert len(K.TIPS_MENTOR) > 0

    def test_mentor_tips_values_are_lists(self):
        for key, val in K.TIPS_MENTOR.items():
            assert isinstance(val, list) and len(val) > 0

    def test_expected_mentor_keys(self):
        for k in ("micheal", "daye", "zeussy", "albrooks"):
            assert k in K.TIPS_MENTOR


# ── Other constant structures ──────────────────────────────────────────────
class TestOtherConstants:
    def test_patience_messages_dict(self):
        assert isinstance(K.PATIENCE_MESSAGES, dict)

    def test_tool_durations_positive_ints(self):
        for k, v in K.TOOL_EXPECTED_DURATIONS.items():
            assert isinstance(v, int) and v > 0

    def test_phase_icons_dict(self):
        assert isinstance(K.PHASE_ICONS, dict)
        assert len(K.PHASE_ICONS) > 0

    def test_verify_seconds(self):
        assert K.QUICK_ASK_VERIFY_SECONDS == 30 * 60

    def test_verify_max_words(self):
        assert K.VERIFY_MAX_WORDS_PREVIEW == 15


# ── HELP_TEXT compliance ─────────────────────────────────────────────────
class TestHelpText:
    def test_is_nonempty_string(self):
        assert isinstance(K.HELP_TEXT, str)
        assert len(K.HELP_TEXT) > 0

    def test_mentions_oxygpt(self):
        assert "OxyGPT" in K.HELP_TEXT or "دستیار" in K.HELP_TEXT

    def test_no_forbidden_two_letter_token(self):
        # oxygpt-workflow house rule: the standalone uppercase two-letter
        # "artificial intelligence" token must never appear in user-facing text.
        forbidden = "A" + "I"
        assert not re.search(rf"\b{forbidden}\b", K.HELP_TEXT)

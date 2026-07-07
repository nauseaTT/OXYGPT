"""Offline unit tests for the Support Assistant mode.

These cover the pure/UI logic in `telegram/handlers/support.py` and the
static content in `support_knowledge.py` — no Telegram client, no network,
no database file is required. The `self`/`ai_service` collaborators are
replaced with lightweight fakes so the turn-cap, button-building, and
window-clearing behaviour can be asserted in isolation.
"""

import support_knowledge as sk
from telegram.handlers import support as S


# ── static knowledge content ─────────────────────────────────────────────
class TestSupportKnowledge:
    def test_system_prompt_is_nonempty_text(self):
        assert isinstance(sk.SUPPORT_SYSTEM_PROMPT, str)
        assert sk.SUPPORT_SYSTEM_PROMPT.strip()

    def test_welcome_text_is_nonempty(self):
        assert isinstance(sk.SUPPORT_WELCOME_TEXT, str)
        assert sk.SUPPORT_WELCOME_TEXT.strip()

    def test_suggested_questions_shape(self):
        assert len(sk.SUPPORT_SUGGESTED_QUESTIONS) > 0
        for item in sk.SUPPORT_SUGGESTED_QUESTIONS:
            # Each entry is a (label, full_question) tuple of non-empty text.
            assert isinstance(item, tuple) and len(item) == 2
            label, question = item
            assert isinstance(label, str) and label.strip()
            assert isinstance(question, str) and question.strip()


# ── _count_user_turns ─────────────────────────────────────────────────────
class _FakeAIService:
    def __init__(self, history=None, window_id=None, history_version=0):
        self.history = list(history or [])
        self.window_id = window_id
        self._history_version = history_version


class TestCountUserTurns:
    def test_empty_history(self):
        assert S._count_user_turns(_FakeAIService()) == 0

    def test_missing_history_attr(self):
        class Bare:
            pass
        assert S._count_user_turns(Bare()) == 0

    def test_counts_only_user_role(self):
        svc = _FakeAIService(history=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "system", "content": "d"},
        ])
        assert S._count_user_turns(svc) == 2


# ── _suggested_question_buttons ───────────────────────────────────────────
class TestSuggestedQuestionButtons:
    def test_returns_display_count_plus_exit_row(self):
        rows = S._suggested_question_buttons()
        # SUPPORT_SUGGESTED_DISPLAY_COUNT question rows + 1 exit row.
        expected_q_rows = min(
            S.SUPPORT_SUGGESTED_DISPLAY_COUNT, len(sk.SUPPORT_SUGGESTED_QUESTIONS)
        )
        assert len(rows) == expected_q_rows + 1

    def test_last_row_is_exit_button(self):
        rows = S._suggested_question_buttons()
        exit_row = rows[-1]
        assert len(exit_row) == 1
        assert exit_row[0].data == b"support_exit"

    def test_question_callbacks_encode_pool_index(self):
        rows = S._suggested_question_buttons()
        for row in rows[:-1]:
            data = row[0].data.decode()
            assert data.startswith("support_suggested:")
            idx = int(data.split(":", 1)[1])
            assert 0 <= idx < len(sk.SUPPORT_SUGGESTED_QUESTIONS)


# ── build_support_turn_buttons + turn cap ─────────────────────────────────
class _FakeDB:
    def __init__(self):
        self.saved_sessions = []
        self.deleted_pending = []

    def save_window_session(self, window_id, history, a, b, c):
        self.saved_sessions.append((window_id, history, a, b, c))

    def delete_pending_state(self, uid):
        self.deleted_pending.append(uid)


class _FakeBot:
    def __init__(self):
        self.db = _FakeDB()
        self.sessions = {}
        self.pending_message = {}


class TestBuildSupportTurnButtons:
    def test_below_cap_offers_continue_button(self):
        bot = _FakeBot()
        svc = _FakeAIService(history=[{"role": "user", "content": "x"}])
        buttons, closing = S.build_support_turn_buttons(bot, uid=42, ai_service=svc, tokens_used=1234)
        assert closing == ""
        # First button re-arms the same conversation via Asupport_<uid>.
        assert buttons[0][0].data == b"Asupport_42"
        # Nothing was cleared while still under the cap.
        assert bot.db.saved_sessions == []
        assert svc.history == [{"role": "user", "content": "x"}]

    def test_at_cap_closes_and_returns_note(self):
        bot = _FakeBot()
        history = [{"role": "user", "content": "q"}] * S.SUPPORT_MAX_TURNS
        svc = _FakeAIService(history=history, window_id=7, history_version=3)
        bot.sessions[42] = svc
        bot.pending_message[(42, "ask_ai")] = object()

        buttons, closing = S.build_support_turn_buttons(bot, uid=42, ai_service=svc, tokens_used=0)

        # A closing note is appended and no "continue" button is offered.
        assert closing.strip()
        assert buttons[-1][0].data == b"back_to_main"
        # The window was wiped and the in-memory session dropped.
        assert bot.db.saved_sessions == [(7, [], 0, 0, 0)]
        assert svc.history == []
        assert 42 not in bot.sessions
        assert (42, "ask_ai") not in bot.pending_message
        assert 42 in bot.db.deleted_pending

    def test_close_bumps_history_version(self):
        """_close_support_window must bump _history_version so a racing
        background summarization task detects the change and skips its write."""
        bot = _FakeBot()
        svc = _FakeAIService(history=[{"role": "user", "content": "q"}], window_id=1, history_version=5)
        S._close_support_window(bot, uid=1, ai_service=svc)
        assert svc._history_version == 6

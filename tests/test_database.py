"""Tests for `database.py` — the synchronous `DatabaseManager`.

Each test uses the `db` fixture (temp-file SQLite) from conftest.
"""

import json

import pytest


# ── init / schema ──────────────────────────────────────────────────────
class TestInit:
    def test_creates_file_and_tables(self, db):
        # get_locks touches the locks table; empty DB should return [].
        assert db.get_locks() == []

    def test_default_usage_empty(self, db):
        usage = db.get_user_usage(1)
        assert usage["total_requests"] == 0
        assert usage["total_input_tokens"] == 0


# ── conversation windows ────────────────────────────────────────────────
class TestWindows:
    def test_get_active_creates_default(self, db):
        win = db.get_active_window(100)
        assert win["is_active"] == 1
        assert win["user_id"] == 100

    def test_active_window_persists(self, db):
        w1 = db.get_active_window(100)
        w2 = db.get_active_window(100)
        assert w1["window_id"] == w2["window_id"]

    def test_create_window(self, db):
        db.get_active_window(100)  # window #1
        assert db.create_window(100, "Second") is True
        assert len(db.get_user_windows(100)) == 2

    def test_max_five_windows(self, db):
        db.get_active_window(100)
        for i in range(4):
            assert db.create_window(100, f"W{i}") is True
        # 6th should fail (limit 5)
        assert db.create_window(100, "TooMany") is False
        assert len(db.get_user_windows(100)) == 5

    def test_is_window_owner(self, db):
        win = db.get_active_window(100)
        assert db.is_window_owner(100, win["window_id"]) is True
        assert db.is_window_owner(999, win["window_id"]) is False

    def test_rename_window(self, db):
        win = db.get_active_window(100)
        assert db.rename_window(100, win["window_id"], "New Name") is True
        windows = db.get_user_windows(100)
        assert windows[0]["title"] == "New Name"

    def test_rename_nonexistent_returns_false(self, db):
        assert db.rename_window(100, 99999, "X") is False

    def test_delete_window(self, db):
        db.get_active_window(100)
        db.create_window(100, "Second")
        windows = db.get_user_windows(100)
        wid = windows[1]["window_id"]
        assert db.delete_window(100, wid) is True
        assert len(db.get_user_windows(100)) == 1

    def test_delete_active_promotes_sibling(self, db):
        w1 = db.get_active_window(100)
        db.create_window(100, "Second")
        db.delete_window(100, w1["window_id"])
        # A remaining window must be active
        remaining = db.get_user_windows(100)
        assert any(w["is_active"] for w in remaining)

    def test_delete_nonexistent_returns_false(self, db):
        assert db.delete_window(100, 99999) is False

    def test_set_active_window(self, db):
        db.get_active_window(100)
        db.create_window(100, "Second")
        windows = db.get_user_windows(100)
        target = windows[1]["window_id"]
        assert db.set_active_window(100, target) is True
        active = db.get_active_window(100)
        assert active["window_id"] == target

    def test_set_active_wrong_owner(self, db):
        win = db.get_active_window(100)
        assert db.set_active_window(999, win["window_id"]) is False

    def test_save_window_session_truncates_history(self, db):
        win = db.get_active_window(100)
        history = [{"role": "user", "parts": [{"text": str(i)}]} for i in range(60)]
        db.save_window_session(win["window_id"], history, 1, 10, 20)
        session = db.get_session(100)
        stored = json.loads(session["history"])
        assert len(stored) == 40

    def test_quick_ask_windows_filter(self, db):
        db.get_active_window(100, default_mode="quick_ask")
        qa = db.get_quick_ask_windows(100)
        assert all(w["mode"] == "quick_ask" for w in qa)

    def test_mentor_window(self, db):
        db.create_window(100, "Mentor Micheal", mode="mentor", mentor_key="micheal")
        win = db.get_mentor_window(100, "micheal")
        assert win is not None
        assert win["mentor_key"] == "micheal"

    def test_mentor_window_none(self, db):
        assert db.get_mentor_window(100, "nobody") is None


# ── get_last_user_message ───────────────────────────────────────────────
class TestGetLastUserMessage:
    def test_extracts_last_user_text(self, db):
        history = [
            {"role": "user", "parts": [{"text": "first"}]},
            {"role": "model", "parts": [{"text": "answer"}]},
            {"role": "user", "parts": [{"text": "second"}]},
        ]
        assert db.get_last_user_message(history) == "second"

    def test_truncates_to_100(self, db):
        history = [{"role": "user", "parts": [{"text": "x" * 200}]}]
        assert len(db.get_last_user_message(history)) == 100

    def test_none_when_no_user(self, db):
        history = [{"role": "model", "parts": [{"text": "hi"}]}]
        assert db.get_last_user_message(history) is None

    def test_non_list_returns_none(self, db):
        assert db.get_last_user_message("not a list") is None

    def test_empty_returns_none(self, db):
        assert db.get_last_user_message([]) is None


# ── usage tracking ──────────────────────────────────────────────────────
class TestUsage:
    def test_increment_user_usage(self, db):
        db.increment_user_usage(1, 2, 100, 200)
        db.increment_user_usage(1, 3, 50, 60)
        usage = db.get_user_usage(1)
        assert usage["total_requests"] == 5
        assert usage["total_input_tokens"] == 150
        assert usage["total_output_tokens"] == 260

    def test_image_usage_roundtrip(self, db):
        db.increment_image_usage(1, 3)
        assert db.get_image_usage(1) == 3
        db.decrement_image_usage(1, 1)
        assert db.get_image_usage(1) == 2

    def test_image_usage_no_negative(self, db):
        db.increment_image_usage(1, 1)
        db.decrement_image_usage(1, 5)
        assert db.get_image_usage(1) == 0

    def test_html_usage_roundtrip(self, db):
        db.increment_html_usage(1, 4)
        assert db.get_html_usage(1) == 4
        db.decrement_html_usage(1, 2)
        assert db.get_html_usage(1) == 2

    def test_html_no_negative(self, db):
        db.increment_html_usage(1, 1)
        db.decrement_html_usage(1, 10)
        assert db.get_html_usage(1) == 0

    def test_image_usage_default_zero(self, db):
        assert db.get_image_usage(999) == 0

    def test_reset_usage_metrics(self, db):
        db.increment_user_usage(1, 5, 100, 200)
        db.reset_usage_metrics()
        assert db.get_user_usage(1)["total_requests"] == 0

    def test_top_users_by_token(self, db):
        db.increment_user_usage(1, 1, 100, 100)
        db.increment_user_usage(2, 1, 500, 500)
        top = db.get_top_users_by_token(limit=2)
        assert top[0]["user_id"] == 2


# ── group usage ─────────────────────────────────────────────────────────
class TestGroupUsage:
    def test_increment_and_get(self, db):
        db.increment_group_usage(-100, 2, 50, 60)
        usage = db.get_group_usage(-100)
        assert usage["total_requests"] == 2

    def test_default_group_usage(self, db):
        usage = db.get_group_usage(-999)
        assert usage["total_requests"] == 0

    def test_reset_group_usage(self, db):
        db.increment_group_usage(-100, 2, 50, 60)
        db.reset_group_usage()
        assert db.get_group_usage(-100)["total_requests"] == 0

    def test_top_groups(self, db):
        db.increment_group_usage(-1, 1, 10, 10)
        db.increment_group_usage(-2, 1, 100, 100)
        top = db.get_top_groups_by_token(limit=1)
        assert top[0]["group_id"] == -2


# ── settings ─────────────────────────────────────────────────────────────
class TestSettings:
    def test_default(self, db):
        assert db.get_setting("missing", "fallback") == "fallback"

    def test_save_and_get(self, db):
        db.save_setting("model", "gemini-3.5-flash")
        assert db.get_setting("model", "x") == "gemini-3.5-flash"

    def test_overwrite(self, db):
        db.save_setting("k", "v1")
        db.save_setting("k", "v2")
        assert db.get_setting("k", "x") == "v2"


# ── AI services ─────────────────────────────────────────────────────────
class TestServices:
    def test_empty(self, db):
        assert db.get_services() == []

    def test_save_and_get(self, db):
        db.save_service({"id": "s1", "name": "OpenAI"})
        assert db.get_service("s1")["name"] == "OpenAI"

    def test_update_existing(self, db):
        db.save_service({"id": "s1", "name": "A"})
        db.save_service({"id": "s1", "name": "B"})
        assert db.get_service("s1")["name"] == "B"
        assert len(db.get_services()) == 1

    def test_delete(self, db):
        db.save_service({"id": "s1"})
        assert db.delete_service("s1") is True
        assert db.get_service("s1") is None

    def test_delete_missing(self, db):
        assert db.delete_service("nope") is False

    def test_window_service_id(self, db):
        win = db.get_active_window(100)
        db.set_window_service(win["window_id"], "svc1")
        assert db.get_window_service_id(win["window_id"]) == "svc1"

    def test_clear_all_windows_service(self, db):
        win = db.get_active_window(100)
        db.set_window_service(win["window_id"], "svc1")
        affected = db.clear_all_windows_service_id()
        assert affected == 1
        assert db.get_window_service_id(win["window_id"]) is None

    def test_assign_service_to_unassigned(self, db):
        db.get_active_window(100)
        affected = db.assign_service_to_unassigned_windows("svc2")
        assert affected >= 1


# ── pending states ───────────────────────────────────────────────────────
class TestPendingStates:
    def test_default(self, db):
        state = db.get_pending_state(1)
        assert state["pending_question"] is False

    def test_save_and_get(self, db):
        db.save_pending_state(1, True, pending_role="role", pending_action="act")
        state = db.get_pending_state(1)
        assert state["pending_question"] is True
        assert state["pending_role"] == "role"
        assert state["pending_action"] == "act"

    def test_delete(self, db):
        db.save_pending_state(1, True)
        db.delete_pending_state(1)
        assert db.get_pending_state(1)["pending_question"] is False


# ── locks / joins ────────────────────────────────────────────────────────
class TestLocks:
    def test_add_and_get(self, db):
        db.add_lock(-100123, "Chan", "https://t.me/x")
        locks = db.get_locks()
        assert len(locks) == 1
        assert locks[0]["title"] == "Chan"

    def test_remove(self, db):
        db.add_lock(-100123, "Chan", "link")
        db.remove_lock(-100123)
        assert db.get_locks() == []

    def test_log_join_and_stats(self, db):
        db.log_join(1, -100123)
        db.log_join(2, -100123)
        stats = db.get_join_stats(-100123)
        assert stats["total"] == 2
        assert stats["last_24h"] == 2

    def test_log_join_idempotent(self, db):
        db.log_join(1, -100123)
        db.log_join(1, -100123)  # duplicate ignored
        assert db.get_join_stats(-100123)["total"] == 1


# ── blocked users / groups ───────────────────────────────────────────────
class TestBlocking:
    def test_block_unblock_user(self, db):
        db.block_user(5, blocked_by=1, reason="spam")
        assert db.is_user_blocked(5) is True
        db.unblock_user(5)
        assert db.is_user_blocked(5) is False

    def test_get_blocked_users(self, db):
        db.block_user(5, blocked_by=1)
        assert len(db.get_blocked_users()) == 1

    def test_block_unblock_group(self, db):
        db.block_group(-100, blocked_by=1)
        assert db.is_group_blocked(-100) is True
        db.unblock_group(-100)
        assert db.is_group_blocked(-100) is False

    def test_not_blocked_by_default(self, db):
        assert db.is_user_blocked(999) is False
        assert db.is_group_blocked(-999) is False


# ── 503 auto-recovery state ──────────────────────────────────────────────
class Test503State:
    def test_default_state(self, db):
        state = db.get_503_state()
        assert state["is_downgraded"] == 0
        assert state["original_models"] is None

    def test_set_downgrade(self, db):
        db.set_503_downgrade({"quick_ask_model": "gemini-3.5-flash"})
        state = db.get_503_state()
        assert state["is_downgraded"] == 1
        assert state["original_models"] == {"quick_ask_model": "gemini-3.5-flash"}
        assert state["downgrade_time"] is not None

    def test_clear_downgrade_returns_backup(self, db):
        db.set_503_downgrade({"m": "gemini-3.5-flash"})
        backup = db.clear_503_downgrade()
        assert backup == {"m": "gemini-3.5-flash"}
        assert db.get_503_state()["is_downgraded"] == 0

    def test_downgrade_gemini_models(self, db):
        db.save_setting("quick_ask_model", "gemini-3.5-flash")
        db.save_setting("other", "keep-me")
        changed = db.downgrade_gemini_models()
        assert changed == {"quick_ask_model": "gemini-3.5-flash"}
        assert db.get_setting("quick_ask_model", "") == "gemini-3.1-flash-lite"
        assert db.get_setting("other", "") == "keep-me"

    def test_restore_gemini_models(self, db):
        db.save_setting("quick_ask_model", "gemini-3.1-flash-lite")
        db.restore_gemini_models({"quick_ask_model": "gemini-3.5-flash"})
        assert db.get_setting("quick_ask_model", "") == "gemini-3.5-flash"

    def test_downgrade_time_is_isoformat(self, db):
        from datetime import datetime
        db.set_503_downgrade({"m": "gemini-3.5-flash"})
        ts = db.get_503_state()["downgrade_time"]
        # Should parse without raising.
        datetime.fromisoformat(ts)

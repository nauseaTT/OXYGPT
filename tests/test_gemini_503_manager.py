"""Tests for `gemini_503_manager.py`."""

import asyncio

import pytest

import gemini_503_manager as G503
from gemini_503_manager import (
    Gemini503Manager,
    init_global_503_manager,
    get_global_503_manager,
)


class FakeClient:
    """Records send_message calls instead of hitting Telegram."""

    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, parse_mode=None):
        self.sent.append((uid, text, parse_mode))


# ── counter / basic behaviour ───────────────────────────────────────────
class TestCounter:
    async def test_first_503_returns_fallback(self, manager_503):
        result = await manager_503.handle_503_error("gemini-3.5-flash", 1)
        assert result == "gemini-3.1-flash-lite"
        assert manager_503._503_count == 1

    async def test_no_downgrade_on_first(self, manager_503):
        await manager_503.handle_503_error("gemini-3.5-flash", 1)
        assert manager_503.db_manager.get_503_state()["is_downgraded"] == 0

    async def test_reset_counter(self, manager_503):
        await manager_503.handle_503_error("m", 1)
        manager_503.reset_counter()
        assert manager_503._503_count == 0

    async def test_reset_counter_when_zero_noop(self, manager_503):
        manager_503.reset_counter()
        assert manager_503._503_count == 0


# ── downgrade trigger ────────────────────────────────────────────────────
class TestDowngrade:
    async def test_two_errors_trigger_downgrade(self, manager_503):
        # Seed a settings row so downgrade_gemini_models finds something.
        manager_503.db_manager.save_setting("quick_ask_model", "gemini-3.5-flash")
        manager_503.set_bot_client(FakeClient())
        await manager_503.handle_503_error("gemini-3.5-flash", 1)
        await manager_503.handle_503_error("gemini-3.5-flash", 1)
        state = manager_503.db_manager.get_503_state()
        assert state["is_downgraded"] == 1
        # cleanup scheduled recovery task
        if manager_503._recovery_task:
            manager_503._recovery_task.cancel()

    async def test_downgrade_sends_notification(self, manager_503):
        manager_503.db_manager.save_setting("quick_ask_model", "gemini-3.5-flash")
        client = FakeClient()
        manager_503.set_bot_client(client)
        await manager_503.handle_503_error("m", 1)
        await manager_503.handle_503_error("m", 1)
        assert len(client.sent) >= 1
        assert client.sent[0][2] == "html"
        if manager_503._recovery_task:
            manager_503._recovery_task.cancel()

    async def test_no_downgrade_when_no_matching_models(self, manager_503):
        manager_503.set_bot_client(FakeClient())
        # No gemini-3.5-flash settings -> nothing to downgrade
        await manager_503.handle_503_error("m", 1)
        await manager_503.handle_503_error("m", 1)
        assert manager_503.db_manager.get_503_state()["is_downgraded"] == 0

    async def test_downgrade_idempotent(self, manager_503):
        manager_503.db_manager.save_setting("quick_ask_model", "gemini-3.5-flash")
        manager_503.set_bot_client(FakeClient())
        await manager_503._trigger_database_downgrade()
        # already downgraded; a second call should be a no-op
        await manager_503._trigger_database_downgrade()
        assert manager_503.db_manager.get_503_state()["is_downgraded"] == 1
        if manager_503._recovery_task:
            manager_503._recovery_task.cancel()


# ── notification safety ──────────────────────────────────────────────────
class TestNotification:
    async def test_no_client_no_crash(self, manager_503):
        # No bot client set -> should log and return without raising.
        await manager_503._send_admin_notification("hi")

    async def test_client_exception_swallowed(self, manager_503):
        class BadClient:
            async def send_message(self, *a, **kw):
                raise RuntimeError("network down")
        manager_503.set_bot_client(BadClient())
        await manager_503._send_admin_notification("hi")  # must not raise


# ── get_stats ────────────────────────────────────────────────────────────
class TestStats:
    def test_stats_shape(self, manager_503):
        stats = manager_503.get_stats()
        assert set(stats.keys()) == {
            "503_count", "is_downgraded", "downgrade_time",
            "recovery_scheduled", "recovery_task_active",
        }

    def test_stats_initial(self, manager_503):
        stats = manager_503.get_stats()
        assert stats["503_count"] == 0
        assert stats["recovery_task_active"] is False


# ── singleton ────────────────────────────────────────────────────────────
class TestSingleton:
    def test_init_and_get(self, db, monkeypatch):
        monkeypatch.setattr(G503, "_global_manager", None)
        init_global_503_manager(db, admin_user_id=42)
        mgr = get_global_503_manager()
        assert isinstance(mgr, Gemini503Manager)
        assert mgr.admin_user_id == 42

    def test_init_twice_keeps_first(self, db, monkeypatch):
        monkeypatch.setattr(G503, "_global_manager", None)
        init_global_503_manager(db, admin_user_id=1)
        init_global_503_manager(db, admin_user_id=2)
        assert get_global_503_manager().admin_user_id == 1

    def test_get_when_uninitialized(self, monkeypatch):
        monkeypatch.setattr(G503, "_global_manager", None)
        assert get_global_503_manager() is None

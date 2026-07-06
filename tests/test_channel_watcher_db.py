"""Tests for the async `channel_watcher/database.py` module.

Uses the `cw_db` fixture (temp file + patched globals) from conftest.
"""

import json

import pytest


# ── monitors ──────────────────────────────────────────────────────────────
class TestMonitors:
    async def test_create_and_get(self, cw_db):
        mid = await cw_db.create_monitor(
            user_id=1, chat_id=-100, channel_username="chan", channel_title="Chan",
        )
        mon = await cw_db.get_monitor(mid)
        assert mon["channel_username"] == "chan"
        assert mon["user_id"] == 1

    async def test_get_missing_returns_none(self, cw_db):
        assert await cw_db.get_monitor(9999) is None

    async def test_user_monitors(self, cw_db):
        await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.create_monitor(1, -2, "b", "B")
        await cw_db.create_monitor(2, -3, "c", "C")
        mons = await cw_db.get_user_monitors(1)
        assert len(mons) == 2

    async def test_monitor_count(self, cw_db):
        await cw_db.create_monitor(1, -1, "a", "A")
        assert await cw_db.get_monitor_count(1) == 1

    async def test_active_monitors(self, cw_db):
        await cw_db.create_monitor(1, -1, "a", "A")
        active = await cw_db.get_active_monitors()
        assert len(active) == 1

    async def test_update_monitor(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.update_monitor(mid, interval_hours=12)
        mon = await cw_db.get_monitor(mid)
        assert mon["interval_hours"] == 12

    async def test_update_noop_when_empty(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.update_monitor(mid)  # no kwargs — should not raise
        assert await cw_db.get_monitor(mid) is not None

    async def test_delete_soft(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.delete_monitor(mid)
        assert await cw_db.get_monitor_count(1) == 0

    async def test_toggle_pause(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        assert await cw_db.toggle_pause(mid) is True
        assert await cw_db.toggle_pause(mid) is False

    async def test_toggle_pause_missing(self, cw_db):
        assert await cw_db.toggle_pause(9999) is False

    async def test_set_last_message_id_moves_forward(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.set_last_message_id(mid, 100)
        mon = await cw_db.get_monitor(mid)
        assert mon["last_message_id"] == 100

    async def test_set_last_message_id_no_regress(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.set_last_message_id(mid, 100)
        await cw_db.set_last_message_id(mid, 50)  # should be ignored
        mon = await cw_db.get_monitor(mid)
        assert mon["last_message_id"] == 100

    async def test_set_last_checked(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.set_last_checked(mid, "2024-01-01T00:00:00")
        mon = await cw_db.get_monitor(mid)
        assert mon["last_checked_at"] == "2024-01-01T00:00:00"


# ── analyses ────────────────────────────────────────────────────────────
class TestAnalyses:
    async def _monitor(self, cw_db):
        return await cw_db.create_monitor(1, -1, "a", "A")

    async def test_add_and_get(self, cw_db):
        mid = await self._monitor(cw_db)
        aid = await cw_db.add_analysis(
            monitor_id=mid, channel_post_id=10, message_text="text",
            summary="sum", key_points=["p"], importance="high", topics=["t"],
        )
        assert aid > 0
        got = await cw_db.get_analysis_by_id(aid)
        assert got["summary"] == "sum"

    async def test_duplicate_returns_zero(self, cw_db):
        mid = await self._monitor(cw_db)
        await cw_db.add_analysis(mid, 10, "t", "s", [], "high", [])
        dup = await cw_db.add_analysis(mid, 10, "t", "s", [], "high", [])
        assert dup == 0

    async def test_is_message_analyzed(self, cw_db):
        mid = await self._monitor(cw_db)
        await cw_db.add_analysis(mid, 10, "t", "s", [], "high", [])
        assert await cw_db.is_message_analyzed(mid, 10) is True
        assert await cw_db.is_message_analyzed(mid, 99) is False

    async def test_get_analysis_by_message(self, cw_db):
        mid = await self._monitor(cw_db)
        await cw_db.add_analysis(mid, 10, "t", "s", [], "high", [])
        got = await cw_db.get_analysis_by_message(mid, 10)
        assert got is not None

    async def test_get_analyses_list(self, cw_db):
        mid = await self._monitor(cw_db)
        await cw_db.add_analysis(mid, 10, "t", "s1", [], "high", [])
        await cw_db.add_analysis(mid, 11, "t", "s2", [], "low", [])
        rows = await cw_db.get_analyses(mid)
        assert len(rows) == 2

    async def test_key_points_serialized(self, cw_db):
        mid = await self._monitor(cw_db)
        aid = await cw_db.add_analysis(mid, 10, "t", "s", ["x", "y"], "high", [])
        got = await cw_db.get_analysis_by_id(aid)
        assert json.loads(got["key_points"]) == ["x", "y"]


# ── chat sessions ─────────────────────────────────────────────────────────
class TestSessions:
    async def _session(self, cw_db):
        """Create a real monitor + analysis so FK constraints are satisfied.

        chat_sessions.monitor_id -> monitors(id) and
        chat_sessions.message_id -> analyzed_messages(id), so both must exist.
        """
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        aid = await cw_db.add_analysis(mid, 3, "t", "s", [], "high", [])
        return await cw_db.create_session(1, mid, aid)

    async def test_create_and_get(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        aid = await cw_db.add_analysis(mid, 3, "t", "s", [], "high", [])
        sid = await cw_db.create_session(1, mid, aid)
        assert sid > 0
        sess = await cw_db.get_session(1, mid, aid)
        assert sess is not None

    async def test_append_and_get_history(self, cw_db):
        sid = await self._session(cw_db)
        await cw_db.append_to_history(sid, "user", "q1")
        await cw_db.append_to_history(sid, "model", "a1")
        history = await cw_db.get_history(sid)
        assert len(history) == 2
        assert history[0]["content"] == "q1"

    async def test_count_turns(self, cw_db):
        sid = await self._session(cw_db)
        await cw_db.append_to_history(sid, "user", "q1")
        await cw_db.append_to_history(sid, "model", "a1")
        await cw_db.append_to_history(sid, "user", "q2")
        assert await cw_db.count_turns(sid) == 2

    async def test_append_to_missing_session_noop(self, cw_db):
        await cw_db.append_to_history(9999, "user", "x")  # must not raise
        assert await cw_db.get_history(9999) == []

    async def test_reset_session_keeps_system(self, cw_db):
        sid = await self._session(cw_db)
        await cw_db.append_to_history(sid, "system", "context")
        await cw_db.append_to_history(sid, "user", "q1")
        await cw_db.reset_session_history(sid)
        history = await cw_db.get_history(sid)
        assert all(m["role"] == "system" for m in history)

    async def test_history_trim_at_max(self, cw_db):
        sid = await self._session(cw_db)
        for i in range(30):
            await cw_db.append_to_history(sid, "user", f"m{i}")
        history = await cw_db.get_history(sid)
        assert len(history) <= cw_db.MAX_HISTORY_LENGTH


# ── stats ─────────────────────────────────────────────────────────────────
class TestStats:
    def _today(self, cw_db):
        from datetime import datetime
        return datetime.now(cw_db.tz_tehran).strftime("%Y-%m-%d")

    async def test_increment_post_count(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.increment_post_count(mid, self._today(cw_db), 10)
        stats = await cw_db.get_stats(mid, days=30)
        assert any(s["total_posts"] >= 1 for s in stats)

    async def test_hourly_distribution_length(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        dist = await cw_db.get_hourly_distribution(mid)
        assert len(dist) == 24

    async def test_hourly_distribution_counts(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.increment_post_count(mid, self._today(cw_db), 10)
        await cw_db.increment_post_count(mid, self._today(cw_db), 10)
        dist = await cw_db.get_hourly_distribution(mid)
        assert dist[10] == 2

    async def test_increment_analyzed_count(self, cw_db):
        mid = await cw_db.create_monitor(1, -1, "a", "A")
        await cw_db.increment_post_count(mid, self._today(cw_db), 10)
        await cw_db.increment_analyzed_count(mid, self._today(cw_db))
        # should not raise; stats retrievable
        assert isinstance(await cw_db.get_stats(mid), list)

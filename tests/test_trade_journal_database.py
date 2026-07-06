"""Tests for the Trade-Journal async persistence layer (`trade_journal.database`).

Everything runs against a throw-away SQLite file provided by the ``tj_db``
fixture in ``conftest.py``. The schema enforces foreign keys, so any trade
test first creates a user (``ensure_user``) and a template
(``create_template``) before saving trades.
"""

import json

import pytest


# ── Users ──────────────────────────────────────────────────────────────────
class TestUsers:
    async def test_ensure_user_creates_row(self, tj_db):
        user = await tj_db.ensure_user(1, "alice", "Alice")
        assert user["user_id"] == 1
        assert user["username"] == "alice"
        assert user["first_name"] == "Alice"

    async def test_ensure_user_idempotent(self, tj_db):
        first = await tj_db.ensure_user(2, "bob", "Bob")
        # Second call with different values must NOT overwrite existing row.
        second = await tj_db.ensure_user(2, "changed", "Changed")
        assert second["user_id"] == first["user_id"]
        assert second["username"] == "bob"

    async def test_get_user_none_when_absent(self, tj_db):
        assert await tj_db.get_user(999) is None

    async def test_get_user_returns_dict(self, tj_db):
        await tj_db.ensure_user(3, "carol")
        user = await tj_db.get_user(3)
        assert user is not None
        assert user["user_id"] == 3

    async def test_update_user(self, tj_db):
        await tj_db.ensure_user(4, "dave")
        await tj_db.update_user(4, first_name="David")
        user = await tj_db.get_user(4)
        assert user["first_name"] == "David"

    async def test_update_user_multiple_fields(self, tj_db):
        await tj_db.ensure_user(5, "eve")
        await tj_db.update_user(5, username="eve2", first_name="Eve")
        user = await tj_db.get_user(5)
        assert user["username"] == "eve2"
        assert user["first_name"] == "Eve"


# ── User channels ────────────────────────────────────────────────────────
class TestUserChannels:
    async def test_set_and_get_channel(self, tj_db):
        await tj_db.ensure_user(10)
        await tj_db.set_user_channel(10, -100123, "My Channel")
        chans = await tj_db.get_user_channels(10)
        assert len(chans) == 1
        assert chans[0]["channel_id"] == -100123
        assert chans[0]["channel_title"] == "My Channel"

    async def test_set_channel_updates_user_row(self, tj_db):
        await tj_db.ensure_user(11)
        await tj_db.set_user_channel(11, -100999, "Ch")
        user = await tj_db.get_user(11)
        assert user["channel_id"] == -100999
        assert user["channel_title"] == "Ch"

    async def test_get_user_channel_single(self, tj_db):
        await tj_db.ensure_user(12)
        await tj_db.set_user_channel(12, -1, "A")
        got = await tj_db.get_user_channel(12, -1)
        assert got is not None
        assert got["channel_title"] == "A"

    async def test_get_user_channel_absent(self, tj_db):
        await tj_db.ensure_user(13)
        assert await tj_db.get_user_channel(13, -55) is None

    async def test_delete_user_channel(self, tj_db):
        await tj_db.ensure_user(14)
        await tj_db.set_user_channel(14, -2, "B")
        await tj_db.delete_user_channel(14, -2)
        assert await tj_db.get_user_channels(14) == []

    async def test_set_channel_idempotent_insert(self, tj_db):
        await tj_db.ensure_user(15)
        await tj_db.set_user_channel(15, -3, "C")
        await tj_db.set_user_channel(15, -3, "C")
        chans = await tj_db.get_user_channels(15)
        assert len(chans) == 1


# ── Symbols ────────────────────────────────────────────────────────────────
class TestSymbols:
    async def test_add_symbol_uppercases(self, tj_db):
        await tj_db.ensure_user(20)
        sid = await tj_db.add_symbol(20, "eurusd")
        assert isinstance(sid, int)
        syms = await tj_db.get_symbols(20)
        assert syms[0]["name"] == "EURUSD"

    async def test_get_symbols_sorted(self, tj_db):
        await tj_db.ensure_user(21)
        await tj_db.add_symbol(21, "gbpusd")
        await tj_db.add_symbol(21, "audusd")
        names = [s["name"] for s in await tj_db.get_symbols(21)]
        assert names == sorted(names)

    async def test_get_symbol_by_name_uppercases_query(self, tj_db):
        await tj_db.ensure_user(22)
        await tj_db.add_symbol(22, "XAUUSD")
        got = await tj_db.get_symbol_by_name(22, "xauusd")
        assert got is not None
        assert got["name"] == "XAUUSD"

    async def test_get_symbol_by_name_absent(self, tj_db):
        await tj_db.ensure_user(23)
        assert await tj_db.get_symbol_by_name(23, "NONE") is None

    async def test_delete_symbol(self, tj_db):
        await tj_db.ensure_user(24)
        sid = await tj_db.add_symbol(24, "btcusd")
        await tj_db.delete_symbol(sid)
        assert await tj_db.get_symbols(24) == []

    async def test_symbols_scoped_per_user(self, tj_db):
        await tj_db.ensure_user(25)
        await tj_db.ensure_user(26)
        await tj_db.add_symbol(25, "aaa")
        assert await tj_db.get_symbols(26) == []


# ── Templates ────────────────────────────────────────────────────────────
class TestTemplates:
    async def test_create_template(self, tj_db):
        await tj_db.ensure_user(30)
        tid = await tj_db.create_template(30, "Scalping")
        assert isinstance(tid, int)
        tpl = await tj_db.get_template(tid)
        assert tpl["name"] == "Scalping"

    async def test_get_templates_list(self, tj_db):
        await tj_db.ensure_user(31)
        await tj_db.create_template(31, "T1")
        await tj_db.create_template(31, "T2")
        tpls = await tj_db.get_templates(31)
        assert len(tpls) == 2

    async def test_get_template_absent(self, tj_db):
        assert await tj_db.get_template(9999) is None

    async def test_delete_template(self, tj_db):
        await tj_db.ensure_user(32)
        tid = await tj_db.create_template(32, "Del")
        await tj_db.delete_template(tid)
        assert await tj_db.get_template(tid) is None

    async def test_set_template_default(self, tj_db):
        await tj_db.ensure_user(33)
        tid = await tj_db.create_template(33, "Def")
        await tj_db.set_template_default(tid, True)
        tpl = await tj_db.get_template(tid)
        assert tpl["is_default"] == 1

    async def test_clear_all_defaults(self, tj_db):
        await tj_db.ensure_user(34)
        t1 = await tj_db.create_template(34, "A")
        t2 = await tj_db.create_template(34, "B")
        await tj_db.set_template_default(t1, True)
        await tj_db.set_template_default(t2, True)
        await tj_db.clear_all_defaults(34)
        assert (await tj_db.get_template(t1))["is_default"] == 0
        assert (await tj_db.get_template(t2))["is_default"] == 0

    async def test_set_template_channel(self, tj_db):
        await tj_db.ensure_user(35)
        tid = await tj_db.create_template(35, "Ch")
        await tj_db.set_template_channel(tid, -777, "ChanTitle")
        tpl = await tj_db.get_template(tid)
        assert tpl["channel_id"] == -777
        assert tpl["channel_title"] == "ChanTitle"

    async def test_get_template_count(self, tj_db):
        await tj_db.ensure_user(36)
        await tj_db.create_template(36, "X")
        await tj_db.create_template(36, "Y")
        assert await tj_db.get_template_count(36) == 2

    async def test_copy_template(self, tj_db):
        await tj_db.ensure_user(37)
        src = await tj_db.create_template(37, "Source")
        await tj_db.add_template_field(src, "k1", "Label 1", "text")
        new_id = await tj_db.copy_template(src, 37)
        assert new_id != src
        fields = await tj_db.get_template_fields(new_id)
        assert any(f["field_key"] == "k1" for f in fields)


# ── Template fields ────────────────────────────────────────────────────────
class TestTemplateFields:
    async def _template(self, tj_db, uid=40):
        await tj_db.ensure_user(uid)
        return await tj_db.create_template(uid, "Fields")

    async def test_add_and_get_field(self, tj_db):
        tid = await self._template(tj_db, 40)
        fid = await tj_db.add_template_field(tid, "entry", "Entry", "text")
        assert isinstance(fid, int)
        fields = await tj_db.get_template_fields(tid)
        assert fields[0]["field_key"] == "entry"

    async def test_fields_sorted_by_order(self, tj_db):
        tid = await self._template(tj_db, 41)
        await tj_db.add_template_field(tid, "b", "B", "text", sort_order=2)
        await tj_db.add_template_field(tid, "a", "A", "text", sort_order=1)
        fields = await tj_db.get_template_fields(tid)
        assert [f["field_key"] for f in fields] == ["a", "b"]

    async def test_get_fields_by_section(self, tj_db):
        tid = await self._template(tj_db, 42)
        await tj_db.add_template_field(tid, "x", "X", "text", field_section="entry")
        await tj_db.add_template_field(tid, "y", "Y", "text", field_section="exit")
        sections = await tj_db.get_template_fields_by_section(tid)
        assert "entry" in sections and "exit" in sections

    async def test_delete_field(self, tj_db):
        tid = await self._template(tj_db, 43)
        fid = await tj_db.add_template_field(tid, "z", "Z", "text")
        await tj_db.delete_template_field(fid)
        assert await tj_db.get_template_fields(tid) == []

    async def test_update_field_section(self, tj_db):
        tid = await self._template(tj_db, 44)
        fid = await tj_db.add_template_field(tid, "s", "S", "text", field_section="custom")
        await tj_db.update_template_field_section(fid, "notes")
        fields = await tj_db.get_template_fields(tid)
        assert fields[0]["field_section"] == "notes"


# ── Trades (FK: users + templates) ─────────────────────────────────────────
class TestTrades:
    async def _setup(self, tj_db, uid=50):
        await tj_db.ensure_user(uid)
        tid = await tj_db.create_template(uid, "TT")
        return uid, tid

    async def test_save_trade_uppercases_symbol(self, tj_db):
        uid, tid = await self._setup(tj_db, 50)
        trade_id = await tj_db.save_trade(uid, tid, "eurusd", "LONG", pnl=10.0)
        trade = await tj_db.get_trade(trade_id, uid)
        assert trade["symbol"] == "EURUSD"
        assert trade["direction"] == "LONG"

    async def test_get_trade_wrong_user(self, tj_db):
        uid, tid = await self._setup(tj_db, 51)
        trade_id = await tj_db.save_trade(uid, tid, "x", "LONG")
        assert await tj_db.get_trade(trade_id, 99999) is None

    async def test_get_user_trades(self, tj_db):
        uid, tid = await self._setup(tj_db, 52)
        await tj_db.save_trade(uid, tid, "a", "LONG")
        await tj_db.save_trade(uid, tid, "b", "SHORT")
        trades = await tj_db.get_user_trades(uid)
        assert len(trades) == 2

    async def test_update_trade(self, tj_db):
        uid, tid = await self._setup(tj_db, 53)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG", pnl=1.0)
        ok = await tj_db.update_trade(trade_id, uid, pnl=5.0)
        assert ok is True
        assert (await tj_db.get_trade(trade_id, uid))["pnl"] == 5.0

    async def test_update_trade_empty_kwargs(self, tj_db):
        uid, tid = await self._setup(tj_db, 54)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.update_trade(trade_id, uid) is False

    async def test_update_trade_wrong_user(self, tj_db):
        uid, tid = await self._setup(tj_db, 55)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.update_trade(trade_id, 888, pnl=1.0) is False

    async def test_update_trade_score(self, tj_db):
        uid, tid = await self._setup(tj_db, 56)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.update_trade_score(trade_id, uid, 8) is True
        assert (await tj_db.get_trade(trade_id, uid))["trade_score"] == 8

    async def test_delete_trade(self, tj_db):
        uid, tid = await self._setup(tj_db, 57)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.delete_trade(trade_id, uid) is True
        assert await tj_db.get_trade(trade_id, uid) is None

    async def test_delete_trade_wrong_user(self, tj_db):
        uid, tid = await self._setup(tj_db, 58)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.delete_trade(trade_id, 777) is False

    async def test_trade_count(self, tj_db):
        uid, tid = await self._setup(tj_db, 59)
        await tj_db.save_trade(uid, tid, "a", "LONG")
        await tj_db.save_trade(uid, tid, "b", "LONG")
        assert await tj_db.get_user_trades_count(uid) == 2

    async def test_trade_count_zero(self, tj_db):
        await tj_db.ensure_user(60)
        assert await tj_db.get_user_trades_count(60) == 0

    async def test_paginated(self, tj_db):
        uid, tid = await self._setup(tj_db, 61)
        for i in range(5):
            await tj_db.save_trade(uid, tid, f"s{i}", "LONG")
        page = await tj_db.get_user_trades_paginated(uid, limit=2, offset=0)
        assert len(page) == 2

    async def test_custom_data_roundtrip(self, tj_db):
        uid, tid = await self._setup(tj_db, 62)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG",
                                          custom_data={"note": "good"})
        trade = await tj_db.get_trade(trade_id, uid)
        assert trade["custom_data"] == {"note": "good"}

    async def test_custom_data_none(self, tj_db):
        uid, tid = await self._setup(tj_db, 63)
        trade_id = await tj_db.save_trade(uid, tid, "a", "LONG")
        trade = await tj_db.get_trade(trade_id, uid)
        assert not trade.get("custom_data")

    async def test_get_journal_number(self, tj_db):
        uid, tid = await self._setup(tj_db, 64)
        assert await tj_db.get_journal_number(uid) == 1
        await tj_db.save_trade(uid, tid, "a", "LONG")
        assert await tj_db.get_journal_number(uid) == 2

    async def test_get_trades_by_symbol(self, tj_db):
        uid, tid = await self._setup(tj_db, 65)
        await tj_db.save_trade(uid, tid, "eurusd", "LONG")
        await tj_db.save_trade(uid, tid, "gbpusd", "LONG")
        got = await tj_db.get_trades_by_symbol(uid, "eurusd")
        assert len(got) == 1
        assert got[0]["symbol"] == "EURUSD"

    async def test_get_trades_by_symbol_empty(self, tj_db):
        uid, tid = await self._setup(tj_db, 66)
        assert await tj_db.get_trades_by_symbol(uid, "") == []


# ── Statistics ──────────────────────────────────────────────────────────
class TestTradeStats:
    async def _setup(self, tj_db, uid=70):
        await tj_db.ensure_user(uid)
        tid = await tj_db.create_template(uid, "S")
        return uid, tid

    async def test_stats_empty(self, tj_db):
        await tj_db.ensure_user(70)
        stats = await tj_db.get_trade_stats(70)
        assert stats["total"] == 0
        assert stats["win_rate"] == 0
        assert stats["profit_factor"] == 0

    async def test_win_rate(self, tj_db):
        uid, tid = await self._setup(tj_db, 71)
        await tj_db.save_trade(uid, tid, "a", "LONG", pnl=10.0)
        await tj_db.save_trade(uid, tid, "b", "LONG", pnl=10.0)
        await tj_db.save_trade(uid, tid, "c", "LONG", pnl=-10.0)
        await tj_db.save_trade(uid, tid, "d", "LONG", pnl=-10.0)
        stats = await tj_db.get_trade_stats(uid)
        assert stats["total"] == 4
        assert stats["wins"] == 2
        assert stats["losses"] == 2
        assert stats["win_rate"] == 50.0

    async def test_profit_factor(self, tj_db):
        uid, tid = await self._setup(tj_db, 72)
        await tj_db.save_trade(uid, tid, "a", "LONG", pnl=30.0)
        await tj_db.save_trade(uid, tid, "b", "LONG", pnl=-10.0)
        stats = await tj_db.get_trade_stats(uid)
        # total_wins=30, total_losses=10 -> pf=3.0
        assert stats["profit_factor"] == pytest.approx(3.0)

    async def test_total_pnl(self, tj_db):
        uid, tid = await self._setup(tj_db, 73)
        await tj_db.save_trade(uid, tid, "a", "LONG", pnl=15.0)
        await tj_db.save_trade(uid, tid, "b", "LONG", pnl=-5.0)
        stats = await tj_db.get_trade_stats(uid)
        assert stats["total_pnl"] == pytest.approx(10.0)

    async def test_best_worst(self, tj_db):
        uid, tid = await self._setup(tj_db, 74)
        await tj_db.save_trade(uid, tid, "a", "LONG", pnl=50.0)
        await tj_db.save_trade(uid, tid, "b", "LONG", pnl=-30.0)
        stats = await tj_db.get_trade_stats(uid)
        assert stats["best_trade"] == pytest.approx(50.0)
        assert stats["worst_trade"] == pytest.approx(-30.0)

    async def test_profit_factor_no_losses(self, tj_db):
        uid, tid = await self._setup(tj_db, 75)
        await tj_db.save_trade(uid, tid, "a", "LONG", pnl=10.0)
        stats = await tj_db.get_trade_stats(uid)
        assert stats["profit_factor"] == 0  # guarded division


# ── Privacy ────────────────────────────────────────────────────────────────
class TestPrivacy:
    async def test_default_privacy_absent_user(self, tj_db):
        priv = await tj_db.get_privacy(9999)
        assert priv == {"mode": "off", "level": "medium"}

    async def test_set_and_get_privacy(self, tj_db):
        await tj_db.ensure_user(80)
        await tj_db.set_privacy(80, "on", "high")
        priv = await tj_db.get_privacy(80)
        assert priv["mode"] == "on"
        assert priv["level"] == "high"

    async def test_default_level(self, tj_db):
        await tj_db.ensure_user(81)
        await tj_db.set_privacy(81, "on")
        priv = await tj_db.get_privacy(81)
        assert priv["level"] == "medium"

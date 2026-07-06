"""Shared pytest fixtures for the OXYGPT test-suite.

The whole suite lives in this dedicated `tests/` package and never touches
the production databases: every fixture that needs persistence points the
module under test at a throw-away SQLite file inside a `tmp_path`.

Test env safety
----------------
Some modules read environment variables at import time (e.g. admin ids,
FCSAPI token). We set harmless defaults here via an autouse fixture so the
suite is deterministic regardless of the host environment.
"""

import os
import sys
import importlib
import asyncio

import pytest

# Make the project root importable no matter where pytest is invoked from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neutralise environment variables that modules read at runtime."""
    for var in (
        "ADMIN_IDS", "ADMIN_USER_ID", "FCSAPI_TOKEN",
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def db(tmp_path):
    """A fresh `DatabaseManager` backed by a temporary SQLite file."""
    from database import DatabaseManager
    path = tmp_path / "test_bot.db"
    return DatabaseManager(db_path=str(path))


@pytest.fixture
def manager_503(db):
    """A `Gemini503Manager` wired to a temp DB (no bot client attached)."""
    from gemini_503_manager import Gemini503Manager
    return Gemini503Manager(db, admin_user_id=12345)


# ── Channel-Watcher async DB fixture ──────────────────────────────────────
@pytest.fixture
async def cw_db(tmp_path, monkeypatch):
    """Initialise the Channel-Watcher async DB against a temp file.

    The module keeps a global connection + path; we redirect both and make
    sure the connection is reset before and after the test.
    """
    import channel_watcher.database as cwdb

    # Reset any leftover global connection from a prior test.
    if cwdb._db is not None:
        try:
            await cwdb._db.close()
        except Exception:
            pass
    monkeypatch.setattr(cwdb, "_db", None, raising=False)
    monkeypatch.setattr(cwdb, "_db_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(cwdb, "_DB_PATH", str(tmp_path / "cw.db"), raising=False)

    await cwdb.init_db()
    try:
        yield cwdb
    finally:
        if cwdb._db is not None:
            try:
                await cwdb._db.close()
            except Exception:
                pass
        cwdb._db = None


# ── Trade-Journal async DB fixture ────────────────────────────────────────
@pytest.fixture
async def tj_db(tmp_path, monkeypatch):
    """Initialise the Trade-Journal async DB against a temp file."""
    import trade_journal.database as tjdb

    if tjdb._db is not None:
        try:
            await tjdb._db.close()
        except Exception:
            pass
    monkeypatch.setattr(tjdb, "_db", None, raising=False)
    monkeypatch.setattr(tjdb, "_db_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(tjdb, "_DB_PATH", str(tmp_path / "journal.db"), raising=False)

    await tjdb.init_db()
    try:
        yield tjdb
    finally:
        if tjdb._db is not None:
            try:
                await tjdb._db.close()
            except Exception:
                pass
        tjdb._db = None

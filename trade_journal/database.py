import os
import json
import asyncio
import aiosqlite
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def _default_db_path() -> str:
    """Resolve the journal DB path, honouring ``OXYGPT_DATA_DIR`` if set."""
    try:
        import sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from paths import data_path
        return data_path("journal.db")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "journal.db")


_DB_PATH = _default_db_path()

_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()

VALID_TABLES = {"users", "user_channels", "symbols", "templates", "template_fields", "trades", "journal_log"}


class SafeConnection:
    """
    A thread/task-safe wrapper around aiosqlite.Connection that serializes
    all database operations using an asyncio.Lock to prevent concurrent access issues.
    """
    def __init__(self, conn: aiosqlite.Connection, lock: asyncio.Lock):
        self._conn = conn
        self._lock = lock
        self._operation_count = 0

    async def execute(self, *args, **kwargs):
        async with self._lock:
            try:
                self._operation_count += 1
                return await self._conn.execute(*args, **kwargs)
            except Exception as e:
                logger.error(f"Database execute error (op #{self._operation_count}): {e}", exc_info=True)
                raise

    async def executescript(self, *args, **kwargs):
        async with self._lock:
            try:
                return await self._conn.executescript(*args, **kwargs)
            except Exception as e:
                logger.error(f"Database executescript error: {e}", exc_info=True)
                raise

    async def commit(self, *args, **kwargs):
        async with self._lock:
            try:
                return await self._conn.commit(*args, **kwargs)
            except Exception as e:
                logger.error(f"Database commit error: {e}", exc_info=True)
                raise


async def _get_conn() -> SafeConnection:
    global _db
    async with _db_lock:
        if _db is None:
            _db = await aiosqlite.connect(_DB_PATH, timeout=10)
            _db.row_factory = aiosqlite.Row
            await _db.execute("PRAGMA journal_mode=WAL")
            await _db.execute("PRAGMA foreign_keys=ON")
            await _db.execute("PRAGMA busy_timeout=5000")
        try:
            await asyncio.wait_for(_db.execute("SELECT 1"), timeout=5.0)
        except Exception:
            logger.warning("Database connection stale, reconnecting...")
            try:
                await asyncio.wait_for(_db.close(), timeout=3.0)
            except Exception:
                pass
            _db = await aiosqlite.connect(_DB_PATH, timeout=10)
            _db.row_factory = aiosqlite.Row
            await _db.execute("PRAGMA journal_mode=WAL")
            await _db.execute("PRAGMA foreign_keys=ON")
            await _db.execute("PRAGMA busy_timeout=5000")
    return SafeConnection(_db, _db_lock)


async def _column_exists(table: str, column: str) -> bool:
    if table not in VALID_TABLES:
        return False
    conn = await _get_conn()
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    cols = await cursor.fetchall()
    return any(c["name"] == column for c in cols)


async def init_db() -> None:
    conn = await _get_conn()
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            channel_id INTEGER,
            channel_title TEXT,
            account_margin REAL DEFAULT 0,
            account_currency TEXT DEFAULT 'USD',
            state TEXT DEFAULT 'IDLE',
            state_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            channel_title TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            channel_id INTEGER,
            channel_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS template_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            field_label TEXT NOT NULL,
            field_type TEXT CHECK(field_type IN ('checkbox', 'text', 'choice')) NOT NULL,
            field_section TEXT DEFAULT 'custom',
            sort_order INTEGER DEFAULT 0,
            choice_options TEXT,
            FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            template_id INTEGER,
            symbol TEXT NOT NULL,
            direction TEXT CHECK(direction IN ('LONG','SHORT')) NOT NULL,
            volume REAL,
            entry_price REAL,
            stoploss_price REAL,
            exit_price REAL,
            trade_date TIMESTAMP,
            pnl REAL,
            pip_diff REAL,
            dollar_pnl REAL,
            multiple_risk REAL,
            risk_reward_ratio REAL,
            tags TEXT,
            custom_data TEXT,
            channel_message_id INTEGER,
            channel_chat_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS journal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trade_id INTEGER,
            channel_message_id INTEGER,
            channel_chat_id INTEGER,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE SET NULL
        );
    """)

    migs = [
        ("users", "account_margin", "REAL DEFAULT 0"),
        ("users", "account_currency", "TEXT DEFAULT 'USD'"),
        ("templates", "channel_id", "INTEGER"),
        ("templates", "channel_title", "TEXT"),
        ("template_fields", "field_section", "TEXT DEFAULT 'custom'"),
        ("trades", "pip_diff", "REAL"),
        ("trades", "dollar_pnl", "REAL"),
        ("trades", "multiple_risk", "REAL"),
        ("trades", "tags", "TEXT"),
        ("users", "privacy_mode", "TEXT DEFAULT 'off'"),
        ("users", "privacy_level", "TEXT DEFAULT 'medium'"),
        ("template_fields", "choice_options", "TEXT"),
        ("trades", "trade_score", "INTEGER"),
        ("template_fields", "is_required", "INTEGER DEFAULT 0"),
    ]
    for table, col, dtype in migs:
        if not await _column_exists(table, col):
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
                logger.info(f"Added column {col} to {table}")
            except Exception as e:
                logger.warning(f"Could not add column {col} to {table}: {e}")

    # Fix template_fields CHECK constraint if 'choice' is missing
    try:
        cursor = await conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='template_fields'")
        row = await cursor.fetchone()
        if row and 'choice' not in row['sql']:
            await conn.executescript("""
                CREATE TABLE template_fields_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    field_key TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_type TEXT CHECK(field_type IN ('checkbox', 'text', 'choice')) NOT NULL,
                    field_section TEXT DEFAULT 'custom',
                    sort_order INTEGER DEFAULT 0,
                    choice_options TEXT,
                    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
                );
                INSERT INTO template_fields_new SELECT * FROM template_fields;
                DROP TABLE template_fields;
                ALTER TABLE template_fields_new RENAME TO template_fields;
            """)
            logger.info("Migrated template_fields table: added 'choice' to CHECK constraint")
    except Exception as e:
        logger.warning(f"Could not migrate template_fields constraint: {e}")

    # Deduplicate user_channels so the UNIQUE index below can be created even
    # on databases that already accumulated duplicate rows (the old
    # ``INSERT OR IGNORE`` was a no-op without a matching constraint).
    try:
        await conn.execute(
            """DELETE FROM user_channels
               WHERE id NOT IN (
                   SELECT MIN(id) FROM user_channels
                   GROUP BY user_id, channel_id
               )"""
        )
    except Exception as e:
        logger.warning(f"Could not deduplicate user_channels: {e}")

    # Enforce one row per (user_id, channel_id) so ``set_user_channel`` really
    # is idempotent via ``INSERT OR IGNORE``.
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_channels_user_channel "
            "ON user_channels (user_id, channel_id)"
        )
    except Exception as e:
        logger.warning(f"Could not create user_channels unique index: {e}")

    await conn.commit()
    logger.info("Trade Journal database initialized / migrated")


async def ensure_user(user_id: int, username: str = None, first_name: str = None) -> Dict[str, Any]:
    conn = await _get_conn()
    cursor = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    if row:
        return dict(row)
    await conn.execute(
        "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
        (user_id, username, first_name)
    )
    await conn.commit()
    cursor = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return dict(await cursor.fetchone())


async def update_user(user_id: int, **kwargs: Any) -> None:
    conn = await _get_conn()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(user_id)
    await conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?", vals)
    await conn.commit()


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def set_user_channel(user_id: int, channel_id: int, channel_title: str) -> None:
    conn = await _get_conn()
    await conn.execute(
        "UPDATE users SET channel_id = ?, channel_title = ? WHERE user_id = ?",
        (channel_id, channel_title, user_id)
    )
    await conn.execute(
        "INSERT OR IGNORE INTO user_channels (user_id, channel_id, channel_title) VALUES (?, ?, ?)",
        (user_id, channel_id, channel_title)
    )
    await conn.commit()


async def get_user_channels(user_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM user_channels WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_user_channel(user_id: int, channel_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM user_channels WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_user_channel(user_id: int, channel_id: int) -> None:
    conn = await _get_conn()
    await conn.execute(
        "DELETE FROM user_channels WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id)
    )
    await conn.execute(
        "UPDATE templates SET channel_id = NULL, channel_title = NULL WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id)
    )
    await conn.commit()


async def set_template_channel(template_id: int, channel_id: Optional[int], channel_title: Optional[str]) -> None:
    conn = await _get_conn()
    await conn.execute(
        "UPDATE templates SET channel_id = ?, channel_title = ? WHERE id = ?",
        (channel_id, channel_title, template_id)
    )
    await conn.commit()


async def add_symbol(user_id: int, name: str) -> int:
    conn = await _get_conn()
    cursor = await conn.execute(
        "INSERT INTO symbols (user_id, name) VALUES (?, ?)",
        (user_id, name.upper())
    )
    await conn.commit()
    return cursor.lastrowid


async def get_symbols(user_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM symbols WHERE user_id = ? ORDER BY name", (user_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_symbol(symbol_id: int) -> None:
    conn = await _get_conn()
    await conn.execute("DELETE FROM symbols WHERE id = ?", (symbol_id,))
    await conn.commit()


async def get_symbol_by_name(user_id: int, name: str) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM symbols WHERE user_id = ? AND name = ?",
        (user_id, name.upper())
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_template(user_id: int, name: str) -> int:
    conn = await _get_conn()
    cursor = await conn.execute(
        "INSERT INTO templates (user_id, name) VALUES (?, ?)", (user_id, name)
    )
    await conn.commit()
    return cursor.lastrowid


async def get_templates(user_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM templates WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_template(template_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_template(template_id: int) -> None:
    conn = await _get_conn()
    await conn.execute("DELETE FROM template_fields WHERE template_id = ?", (template_id,))
    await conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    await conn.commit()


async def set_template_default(template_id: int, is_default: bool) -> None:
    conn = await _get_conn()
    await conn.execute("UPDATE templates SET is_default = ? WHERE id = ?", (1 if is_default else 0, template_id))
    await conn.commit()


async def clear_all_defaults(user_id: int) -> None:
    conn = await _get_conn()
    await conn.execute("UPDATE templates SET is_default = 0 WHERE user_id = ?", (user_id,))
    await conn.commit()


async def copy_template(source_id: int, user_id: int) -> int:
    source = await get_template(source_id)
    if not source:
        return 0
    new_id = await create_template(user_id, f"{source['name']} (copy)")
    fields = await get_template_fields(source_id)
    for f in fields:
        await add_template_field(new_id, f["field_key"], f["field_label"], f["field_type"], f["field_section"], f["sort_order"])
    if source.get("channel_id"):
        await set_template_channel(new_id, source["channel_id"], source.get("channel_title"))
    return new_id


async def add_template_field(template_id: int, field_key: str, field_label: str,
                       field_type: str, field_section: str = "custom",
                       sort_order: int = 0, choice_options: str = None, is_required: int = 0) -> int:
    conn = await _get_conn()
    cursor = await conn.execute(
        "INSERT INTO template_fields (template_id, field_key, field_label, field_type, field_section, sort_order, choice_options, is_required) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (template_id, field_key, field_label, field_type, field_section, sort_order, choice_options, is_required)
    )
    await conn.commit()
    return cursor.lastrowid


async def get_template_fields(template_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM template_fields WHERE template_id = ? ORDER BY sort_order, id",
        (template_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_template_fields_by_section(template_id: int) -> Dict[str, List[Dict[str, Any]]]:
    fields = await get_template_fields(template_id)
    sections = {}
    for f in fields:
        sec = f.get("field_section", "custom")
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(f)
    return sections


async def delete_template_field(field_id: int) -> None:
    conn = await _get_conn()
    await conn.execute("DELETE FROM template_fields WHERE id = ?", (field_id,))
    await conn.commit()


async def save_trade(user_id: int, template_id: int, symbol: str, direction: str,
               volume: float = None, entry_price: float = None,
               stoploss_price: float = None, exit_price: float = None,
               trade_date: str = None, pnl: float = None,
               pip_diff: float = None, dollar_pnl: float = None,
               multiple_risk: float = None, risk_reward_ratio: float = None,
               tags: str = None, custom_data: dict = None,
               channel_message_id: int = None, channel_chat_id: int = None,
               trade_score: int = None) -> int:
    conn = await _get_conn()
    cursor = await conn.execute(
        """INSERT INTO trades
           (user_id, template_id, symbol, direction, volume, entry_price,
            stoploss_price, exit_price, trade_date, pnl, pip_diff, dollar_pnl,
            multiple_risk, risk_reward_ratio, tags, custom_data,
            channel_message_id, channel_chat_id, trade_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, template_id, symbol.upper(), direction, volume, entry_price,
         stoploss_price, exit_price, trade_date, pnl, pip_diff, dollar_pnl,
         multiple_risk, risk_reward_ratio, tags,
         json.dumps(custom_data) if custom_data else None,
         channel_message_id, channel_chat_id, trade_score)
    )
    await conn.commit()
    return cursor.lastrowid


async def update_trade(trade_id: int, user_id: int, **kwargs) -> bool:
    conn = await _get_conn()
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k == "custom_data" and v is not None:
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return False
    vals.append(trade_id)
    vals.append(user_id)
    cursor = await conn.execute(
        f"UPDATE trades SET {', '.join(sets)} WHERE id = ? AND user_id = ?", vals
    )
    await conn.commit()
    return cursor.rowcount > 0


async def update_trade_score(trade_id: int, user_id: int, score: int) -> bool:
    return await update_trade(trade_id, user_id, trade_score=score)


async def get_user_trades(user_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM trades WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    )
    rows = await cursor.fetchall()
    return [_deserialize_trade(r) for r in rows]


async def get_trade(trade_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _deserialize_trade(row)


async def delete_trade(trade_id: int, user_id: int) -> bool:
    conn = await _get_conn()
    cursor = await conn.execute(
        "DELETE FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id)
    )
    await conn.commit()
    return cursor.rowcount > 0


async def get_user_trades_paginated(user_id: int, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT * FROM trades WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset)
    )
    rows = await cursor.fetchall()
    return [_deserialize_trade(r) for r in rows]


async def get_user_trades_count(user_id: int) -> int:
    conn = await _get_conn()
    cursor = await conn.execute(
        "SELECT COUNT(*) as cnt FROM trades WHERE user_id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    return row["cnt"] if row else 0


def _deserialize_trade(row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("custom_data"):
        try:
            d["custom_data"] = json.loads(d["custom_data"])
        except (json.JSONDecodeError, TypeError):
            d["custom_data"] = {}
    return d


async def get_trade_stats(user_id: int) -> Dict[str, Any]:
    conn = await _get_conn()
    cursor = await conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                  SUM(IFNULL(pnl, 0)) as total_pnl,
                  AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win,
                  AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss,
                  SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as total_wins,
                  SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as total_losses,
                  MAX(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as best_trade,
                  MIN(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as worst_trade
           FROM trades WHERE user_id = ?""",
        (user_id,)
    )
    row = await cursor.fetchone()
    d = dict(row)
    total = d["total"] or 0
    wins = d["wins"] or 0
    losses = d["losses"] or 0
    d["win_rate"] = (wins / total * 100) if total > 0 else 0
    # Profit factor = sum(winning trades) / sum(abs(losing trades))
    total_wins = float(d.get("total_wins") or 0.0)
    total_losses = float(d.get("total_losses") or 0.0)
    d["profit_factor"] = (total_wins / total_losses) if total_losses > 0 else 0
    return d


async def get_detailed_analysis(user_id: int) -> Dict[str, Any]:
    trades = await get_user_trades(user_id)
    analysis = {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakevens": 0,
        "total_pnl": 0.0,
        "total_wins_pnl": 0.0,
        "total_losses_pnl": 0.0,
        "symbol_breakdown": {},
        "direction_breakdown": {"LONG": {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
                                "SHORT": {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}},
        "daily_pnl": {},
        "hourly_pnl": {},
        "best_day": None,
        "worst_day": None,
        "best_trade": None,
        "worst_trade": None,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "total_risk": 0.0,
        "avg_r_multiple": 0.0,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
    }

    if not trades:
        return analysis

    for t in trades:
        analysis["total_trades"] += 1
        pnl = t.get("pnl") or 0
        analysis["total_pnl"] += pnl

        if pnl > 0:
            analysis["wins"] += 1
            analysis["total_wins_pnl"] += pnl
        elif pnl < 0:
            analysis["losses"] += 1
            analysis["total_losses_pnl"] += pnl
        else:
            analysis["breakevens"] += 1

        sym = t["symbol"]
        if sym not in analysis["symbol_breakdown"]:
            analysis["symbol_breakdown"][sym] = {
                "total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "total_volume": 0.0
            }
        analysis["symbol_breakdown"][sym]["total"] += 1
        analysis["symbol_breakdown"][sym]["total_pnl"] += pnl
        analysis["symbol_breakdown"][sym]["total_volume"] += t.get("volume") or 0
        if pnl > 0:
            analysis["symbol_breakdown"][sym]["wins"] += 1
        elif pnl < 0:
            analysis["symbol_breakdown"][sym]["losses"] += 1

        d = t.get("direction", "LONG")
        if d in analysis["direction_breakdown"]:
            analysis["direction_breakdown"][d]["total"] += 1
            analysis["direction_breakdown"][d]["total_pnl"] += pnl
            if pnl > 0:
                analysis["direction_breakdown"][d]["wins"] += 1
            elif pnl < 0:
                analysis["direction_breakdown"][d]["losses"] += 1

        td = t.get("trade_date", "")
        if td:
            day_key = td[:10] if len(td) >= 10 else td
            if day_key not in analysis["daily_pnl"]:
                analysis["daily_pnl"][day_key] = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
            analysis["daily_pnl"][day_key]["total"] += 1
            analysis["daily_pnl"][day_key]["total_pnl"] += pnl
            if pnl > 0:
                analysis["daily_pnl"][day_key]["wins"] += 1
            elif pnl < 0:
                analysis["daily_pnl"][day_key]["losses"] += 1

        if td and len(td) >= 16:
            try:
                hour = td[11:13]
                if hour not in analysis["hourly_pnl"]:
                    analysis["hourly_pnl"][hour] = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
                analysis["hourly_pnl"][hour]["total"] += 1
                analysis["hourly_pnl"][hour]["total_pnl"] += pnl
                if pnl > 0:
                    analysis["hourly_pnl"][hour]["wins"] += 1
                elif pnl < 0:
                    analysis["hourly_pnl"][hour]["losses"] += 1
            except (ValueError, IndexError):
                pass

        if analysis["best_trade"] is None or pnl > analysis["best_trade"]["pnl"]:
            analysis["best_trade"] = {"symbol": sym, "pnl": pnl, "date": td}
        if analysis["worst_trade"] is None or pnl < analysis["worst_trade"]["pnl"]:
            analysis["worst_trade"] = {"symbol": sym, "pnl": pnl, "date": td}

        analysis["total_risk"] += abs(t.get("multiple_risk") or 0) * abs(pnl) if pnl != 0 else 0

    total = analysis["total_trades"]
    wins = analysis["wins"]
    losses = analysis["losses"]
    analysis["win_rate"] = (wins / total * 100) if total > 0 else 0
    # Compute average win/loss correctly (sums over respective trades)
    analysis["avg_win"] = (analysis.get("total_wins_pnl", 0.0) / wins) if wins > 0 else 0
    # avg_loss is kept negative (average of losing PnL values)
    analysis["avg_loss"] = (analysis.get("total_losses_pnl", 0.0) / losses) if losses > 0 else 0

    # Profit factor = sum(winning trades) / sum(abs(losing trades))
    total_wins = analysis.get("total_wins_pnl", 0.0)
    total_losses = abs(analysis.get("total_losses_pnl", 0.0))
    analysis["profit_factor"] = (total_wins / total_losses) if total_losses > 0 else 0

    r_multiples = [abs(t.get("multiple_risk") or 0) for t in trades if t.get("multiple_risk")]
    analysis["avg_r_multiple"] = sum(r_multiples) / len(r_multiples) if r_multiples else 0

    for sym, data in analysis["symbol_breakdown"].items():
        if data["total"] > 0:
            data["win_rate"] = data["wins"] / data["total"] * 100

    best_day = None
    worst_day = None
    for day, data in analysis["daily_pnl"].items():
        if best_day is None or data["total_pnl"] > best_day["pnl"]:
            best_day = {"day": day, "pnl": data["total_pnl"], "trades": data["total"]}
        if worst_day is None or data["total_pnl"] < worst_day["pnl"]:
            worst_day = {"day": day, "pnl": data["total_pnl"], "trades": data["total"]}
    analysis["best_day"] = best_day
    analysis["worst_day"] = worst_day

    streak = 0
    max_streak = 0
    streak_type = None
    for t in trades:
        pnl = t.get("pnl") or 0
        if pnl > 0:
            if streak_type == "win":
                streak += 1
            else:
                streak_type = "win"
                streak = 1
        elif pnl < 0:
            if streak_type == "loss":
                streak += 1
            else:
                streak_type = "loss"
                streak = 1
        else:
            streak = 0
            streak_type = None
        if streak > max_streak:
            max_streak = streak
    analysis["max_consecutive_wins"] = max_streak if streak_type == "win" else 0
    analysis["max_consecutive_losses"] = max_streak if streak_type == "loss" else 0

    return analysis


async def update_template_field_section(field_id: int, section: str) -> None:
    conn = await _get_conn()
    await conn.execute("UPDATE template_fields SET field_section = ? WHERE id = ?", (section, field_id))
    await conn.commit()


async def get_template_count(user_id: int) -> int:
    conn = await _get_conn()
    cursor = await conn.execute("SELECT COUNT(*) as cnt FROM templates WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    return row["cnt"] if row else 0


async def get_trades_by_symbol(user_id: int, symbol: str) -> List[Dict[str, Any]]:
    if not symbol:
        return []
    norm = symbol.strip().upper()
    trades = await get_user_trades(user_id)
    return [t for t in trades if (t.get("symbol") or "").upper() == norm]


async def get_journal_number(user_id: int) -> int:
    return await get_user_trades_count(user_id) + 1


async def set_privacy(user_id: int, mode: str, level: str = "medium") -> None:
    conn = await _get_conn()
    await conn.execute(
        "UPDATE users SET privacy_mode = ?, privacy_level = ? WHERE user_id = ?",
        (mode, level, user_id)
    )
    await conn.commit()


async def get_privacy(user_id: int) -> Dict[str, str]:
    user = await get_user(user_id)
    if not user:
        return {"mode": "off", "level": "medium"}
    return {
        "mode": user.get("privacy_mode", "off"),
        "level": user.get("privacy_level", "medium"),
    }

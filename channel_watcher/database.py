"""Async SQLite database for the Channel Watcher module.

Stores monitor configurations, analyzed message cache (dedup),
per-message chat sessions for the Ask AI feature, and per-channel
activity statistics.

Uses ``aiosqlite`` with an ``asyncio.Lock`` to serialise writes,
following the same pattern as the Trade Journal module.
"""

import os
import json
import asyncio
import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
    tz_tehran = ZoneInfo("Asia/Tehran")
except ImportError:  # pragma: no cover
    from datetime import timezone
    tz_tehran = timezone(timedelta(hours=3, minutes=30))

logger = logging.getLogger(__name__)

def _default_db_path() -> str:
    """Resolve the channel-watcher DB path, honouring ``OXYGPT_DATA_DIR``."""
    try:
        import sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from paths import data_path
        return data_path("channel_watcher.db")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "channel_watcher.db")


_DB_PATH = _default_db_path()

_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()


class SafeConnection:
    """Task-safe wrapper around ``aiosqlite.Connection``.

    Serialises every database operation with an ``asyncio.Lock`` so
    that concurrent scheduler tasks and inline callback handlers do
    not race on the same SQLite connection.
    """

    def __init__(self, conn: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._conn = conn
        self._lock = lock

    async def execute(self, *args: Any, **kwargs: Any) -> aiosqlite.Cursor:
        async with self._lock:
            return await self._conn.execute(*args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> aiosqlite.Cursor:
        async with self._lock:
            return await self._conn.executemany(*args, **kwargs)

    async def executescript(self, *args: Any, **kwargs: Any) -> None:
        async with self._lock:
            await self._conn.executescript(*args, **kwargs)

    async def commit(self) -> None:
        async with self._lock:
            await self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            await self._conn.close()

    async def execute_fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            row = await cur.fetchone()
            if row is None:
                return None
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, row))

    async def execute_fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            rows = await cur.fetchall()
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, r)) for r in rows]


async def init_db() -> None:
    """Create or migrate the database schema and store the global connection."""
    global _db
    if _db is not None:
        return

    _db = await aiosqlite.connect(_DB_PATH)
    _db.row_factory = aiosqlite.Row

    await _db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS monitors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            chat_id         INTEGER NOT NULL,
            channel_username TEXT NOT NULL,
            channel_title   TEXT DEFAULT '',

            delivery_method TEXT NOT NULL DEFAULT 'pm',
            group_chat_id   INTEGER DEFAULT NULL,
            group_username  TEXT DEFAULT NULL,

            interval_hours  INTEGER NOT NULL DEFAULT 4,
            importance_filter TEXT NOT NULL DEFAULT 'important',
            system_prompt   TEXT DEFAULT NULL,

            is_active       INTEGER NOT NULL DEFAULT 1,
            is_paused       INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT DEFAULT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_monitors_user ON monitors(user_id);
        CREATE INDEX IF NOT EXISTS idx_monitors_active ON monitors(is_active, is_paused);

        CREATE TABLE IF NOT EXISTS analyzed_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id      INTEGER NOT NULL,
            channel_post_id INTEGER NOT NULL,
            message_text    TEXT DEFAULT '',
            sender_id       INTEGER DEFAULT NULL,

            summary         TEXT NOT NULL,
            key_points      TEXT DEFAULT '[]',
            importance      TEXT NOT NULL DEFAULT 'medium',
            topics          TEXT DEFAULT '[]',

            analyzed_at     TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_analyzed_dedup
            ON analyzed_messages(monitor_id, channel_post_id);
        CREATE INDEX IF NOT EXISTS idx_analyzed_monitor ON analyzed_messages(monitor_id);

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            monitor_id      INTEGER NOT NULL,
            message_id      INTEGER NOT NULL,

            history         TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),

            FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE,
            FOREIGN KEY (message_id) REFERENCES analyzed_messages(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_session_unique
            ON chat_sessions(user_id, monitor_id, message_id);

        CREATE TABLE IF NOT EXISTS activity_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id      INTEGER NOT NULL,
            date            TEXT NOT NULL,
            total_posts     INTEGER NOT NULL DEFAULT 0,
            analyzed_posts  INTEGER NOT NULL DEFAULT 0,
            hourly_breakdown TEXT DEFAULT '[]',
            last_post_at    TEXT DEFAULT NULL,

            FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE,
            UNIQUE(monitor_id, date)
        );
    """)

    # ── Lightweight migrations for existing databases ──────────────
    await _migrate_add_column(
        "monitors", "last_message_id", "INTEGER NOT NULL DEFAULT 0"
    )

    await _db.commit()
    logger.info("Channel Watcher database initialised")


async def _migrate_add_column(table: str, column: str, ddl: str) -> None:
    """Add *column* to *table* if it doesn't already exist (SQLite-safe)."""
    cur = await _db.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in await cur.fetchall()]
    if column not in cols:
        await _db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("Migrated %s: added column %s", table, column)


async def _get_conn() -> SafeConnection:
    """Return a thread-safe connection wrapper, initialising DB on first call."""
    if _db is None:
        await init_db()
    return SafeConnection(_db, _db_lock)


# ── Monitors ──────────────────────────────────────────────────────────


async def create_monitor(
    user_id: int,
    chat_id: int,
    channel_username: str,
    channel_title: str = "",
    delivery_method: str = "pm",
    group_chat_id: Optional[int] = None,
    group_username: Optional[str] = None,
    interval_hours: int = 4,
    importance_filter: str = "important",
    system_prompt: Optional[str] = None,
    last_message_id: int = 0,
) -> int:
    """Insert a new monitor and return its id.

    ``last_message_id`` should be seeded to the id of the newest post
    already in the channel at the moment the user "locks" it (see
    ``ChannelFetcher.get_latest_message_id``), so the very first fetch
    cycle only ever sees posts published after that moment — nothing
    from before the lock is ever handed to the model.
    """
    conn = await _get_conn()
    cur = await conn.execute(
        """INSERT INTO monitors
           (user_id, chat_id, channel_username, channel_title,
            delivery_method, group_chat_id, group_username,
            interval_hours, importance_filter, system_prompt,
            last_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, chat_id, channel_username, channel_title,
         delivery_method, group_chat_id, group_username,
         interval_hours, importance_filter, system_prompt,
         last_message_id),
    )
    await conn.commit()
    return cur.lastrowid


async def get_monitor(monitor_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchone(
        "SELECT * FROM monitors WHERE id = ?", (monitor_id,)
    )


async def get_user_monitors(user_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchall(
        "SELECT * FROM monitors WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )


async def get_active_monitors() -> List[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchall(
        "SELECT * FROM monitors WHERE is_active = 1 AND is_paused = 0"
    )


async def get_monitor_count(user_id: int) -> int:
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT COUNT(*) as cnt FROM monitors WHERE user_id = ? AND is_active = 1",
        (user_id,),
    )
    return row["cnt"] if row else 0


async def update_monitor(monitor_id: int, **kwargs: Any) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [monitor_id]
    conn = await _get_conn()
    await conn.execute(
        f"UPDATE monitors SET {sets} WHERE id = ?", tuple(vals)
    )
    await conn.commit()


async def delete_monitor(monitor_id: int) -> None:
    conn = await _get_conn()
    await conn.execute("UPDATE monitors SET is_active = 0, is_paused = 0 WHERE id = ?", (monitor_id,))
    await conn.commit()


async def set_last_checked(monitor_id: int, timestamp: str) -> None:
    conn = await _get_conn()
    await conn.execute(
        "UPDATE monitors SET last_checked_at = ? WHERE id = ?",
        (timestamp, monitor_id),
    )
    await conn.commit()


async def set_last_message_id(monitor_id: int, message_id: int) -> None:
    """Advance the high-water mark of the newest post seen for a monitor.

    Only moves forward — never regresses — so overlapping fetch cycles
    can't rewind the boundary and re-process old posts.
    """
    conn = await _get_conn()
    await conn.execute(
        "UPDATE monitors SET last_message_id = ? "
        "WHERE id = ? AND ? > last_message_id",
        (message_id, monitor_id, message_id),
    )
    await conn.commit()


async def toggle_pause(monitor_id: int) -> bool:
    conn = await _get_conn()
    mon = await conn.execute_fetchone(
        "SELECT is_paused FROM monitors WHERE id = ?", (monitor_id,)
    )
    if mon is None:
        return False
    new_state = 0 if mon["is_paused"] else 1
    await conn.execute(
        "UPDATE monitors SET is_paused = ? WHERE id = ?",
        (new_state, monitor_id),
    )
    await conn.commit()
    return bool(new_state)


# ── Analyzed Messages ─────────────────────────────────────────────────


async def is_message_analyzed(monitor_id: int, channel_post_id: int) -> bool:
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT 1 FROM analyzed_messages WHERE monitor_id = ? AND channel_post_id = ?",
        (monitor_id, channel_post_id),
    )
    return row is not None


async def add_analysis(
    monitor_id: int,
    channel_post_id: int,
    message_text: str,
    summary: str,
    key_points: List[str],
    importance: str,
    topics: List[str],
    sender_id: Optional[int] = None,
) -> int:
    conn = await _get_conn()
    try:
        cur = await conn.execute(
            """INSERT OR IGNORE INTO analyzed_messages
               (monitor_id, channel_post_id, message_text, sender_id,
                summary, key_points, importance, topics)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (monitor_id, channel_post_id, message_text, sender_id,
             summary, json.dumps(key_points, ensure_ascii=False),
             importance, json.dumps(topics, ensure_ascii=False)),
        )
        await conn.commit()
        # INSERT OR IGNORE: rowcount==0 means the row already existed
        # (duplicate).  Do NOT trust ``lastrowid`` here — it reports the
        # previous successful insert on this connection, so a duplicate
        # would look like a fresh insert.
        if not cur.rowcount:
            return 0
        return cur.lastrowid or 0
    except Exception as exc:
        logger.warning("add_analysis duplicate or error: %s", exc)
        return 0


async def get_analyses(monitor_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchall(
        "SELECT * FROM analyzed_messages WHERE monitor_id = ? ORDER BY analyzed_at DESC LIMIT ?",
        (monitor_id, limit),
    )


async def get_analysis_by_message(monitor_id: int, channel_post_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchone(
        "SELECT * FROM analyzed_messages WHERE monitor_id = ? AND channel_post_id = ?",
        (monitor_id, channel_post_id),
    )


async def get_analysis_by_id(analysis_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchone(
        "SELECT * FROM analyzed_messages WHERE id = ?", (analysis_id,)
    )


# ── Chat Sessions (Ask AI) ────────────────────────────────────────────

MAX_HISTORY_LENGTH = 20


async def get_session(user_id: int, monitor_id: int, message_id: int) -> Optional[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchone(
        "SELECT * FROM chat_sessions WHERE user_id = ? AND monitor_id = ? AND message_id = ?",
        (user_id, monitor_id, message_id),
    )


async def create_session(user_id: int, monitor_id: int, message_id: int) -> int:
    conn = await _get_conn()
    cur = await conn.execute(
        "INSERT INTO chat_sessions (user_id, monitor_id, message_id) VALUES (?, ?, ?)",
        (user_id, monitor_id, message_id),
    )
    await conn.commit()
    return cur.lastrowid


async def append_to_history(session_id: int, role: str, content: str) -> None:
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT history FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if row is None:
        return
    history: list = json.loads(row["history"])
    history.append({"role": role, "content": content})
    # Trim oldest messages when exceeding the limit, keeping the system
    # context (first message) intact.
    if len(history) > MAX_HISTORY_LENGTH:
        system_msgs = [m for m in history if m.get("role") == "system"]
        other_msgs = [m for m in history if m.get("role") != "system"]
        excess = len(other_msgs) - (MAX_HISTORY_LENGTH - len(system_msgs))
        if excess > 0:
            other_msgs = other_msgs[excess:]
        history = system_msgs + other_msgs
    await conn.execute(
        "UPDATE chat_sessions SET history = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(history, ensure_ascii=False), session_id),
    )
    await conn.commit()


async def get_history(session_id: int) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT history FROM chat_sessions WHERE id = ?", (session_id,)
    )
    return json.loads(row["history"]) if row else []


async def count_turns(session_id: int) -> int:
    """Number of user questions asked so far in a session (excludes system context)."""
    history = await get_history(session_id)
    return sum(1 for m in history if m.get("role") == "user")


async def reset_session_history(session_id: int) -> None:
    """Wipe the Q&A turns of a session, keeping only the system context.

    Used by the "🧹 پاک‌سازی گفتگو" action — lets a user start a fresh
    line of questioning about the same post without losing the pinned
    post context (which is stored as the first ``system`` message).
    """
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT history FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if row is None:
        return
    history: list = json.loads(row["history"])
    system_msgs = [m for m in history if m.get("role") == "system"]
    await conn.execute(
        "UPDATE chat_sessions SET history = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(system_msgs, ensure_ascii=False), session_id),
    )
    await conn.commit()


# ── Activity Stats ────────────────────────────────────────────────────


def _cutoff_date(days: int) -> str:
    """Return the ``YYYY-MM-DD`` boundary *days* ago in Tehran time."""
    return (datetime.now(tz_tehran) - timedelta(days=days)).strftime("%Y-%m-%d")


async def increment_post_count(monitor_id: int, date: str, hour: int) -> None:
    conn = await _get_conn()
    row = await conn.execute_fetchone(
        "SELECT * FROM activity_stats WHERE monitor_id = ? AND date = ?",
        (monitor_id, date),
    )
    now_iso = datetime.now(tz_tehran).isoformat()
    if row:
        breakdown = json.loads(row["hourly_breakdown"])
        if 0 <= hour < len(breakdown):
            breakdown[hour] += 1
        await conn.execute(
            "UPDATE activity_stats SET total_posts = total_posts + 1, hourly_breakdown = ?, last_post_at = ? WHERE id = ?",
            (json.dumps(breakdown), now_iso, row["id"]),
        )
    else:
        breakdown = [0] * 24
        if 0 <= hour < 24:
            breakdown[hour] = 1
        await conn.execute(
            "INSERT INTO activity_stats (monitor_id, date, total_posts, hourly_breakdown, last_post_at) VALUES (?, ?, 1, ?, ?)",
            (monitor_id, date, json.dumps(breakdown), now_iso),
        )
    await conn.commit()


async def increment_analyzed_count(monitor_id: int, date: str) -> None:
    conn = await _get_conn()
    await conn.execute(
        """INSERT INTO activity_stats (monitor_id, date, analyzed_posts)
           VALUES (?, ?, 1)
           ON CONFLICT(monitor_id, date) DO UPDATE SET
               analyzed_posts = analyzed_posts + 1""",
        (monitor_id, date),
    )
    await conn.commit()


async def get_stats(monitor_id: int, days: int = 7) -> List[Dict[str, Any]]:
    conn = await _get_conn()
    return await conn.execute_fetchall(
        """SELECT * FROM activity_stats
           WHERE monitor_id = ? AND date >= ?
           ORDER BY date ASC""",
        (monitor_id, _cutoff_date(days)),
    )


async def cleanup_old_analyses(retention_days: int = 90) -> int:
    """Delete analysed messages, sessions and stats older than *retention_days*.

    Returns the number of deleted analysis rows.
    """
    conn = await _get_conn()
    cutoff_date = (datetime.now(tz_tehran) - timedelta(days=retention_days)).isoformat()

    await conn.execute(
        "DELETE FROM chat_sessions WHERE message_id IN "
        "(SELECT id FROM analyzed_messages WHERE analyzed_at < ?)",
        (cutoff_date,),
    )

    # Purge stats for deleted monitors and stats older than retention.
    await conn.execute(
        "DELETE FROM activity_stats WHERE monitor_id IN "
        "(SELECT id FROM monitors WHERE is_active = 0)"
    )
    await conn.execute(
        "DELETE FROM activity_stats WHERE date < ?",
        (_cutoff_date(retention_days),),
    )

    cur = await conn.execute(
        "DELETE FROM analyzed_messages WHERE analyzed_at < ?",
        (cutoff_date,),
    )
    deleted_count = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    await conn.commit()
    logger.info("Cleaned up %d old analysed messages (retention=%d days)", deleted_count, retention_days)
    return deleted_count


async def get_hourly_distribution(monitor_id: int, days: int = 7) -> List[int]:
    conn = await _get_conn()
    rows = await conn.execute_fetchall(
        """SELECT hourly_breakdown FROM activity_stats
           WHERE monitor_id = ? AND date >= ?""",
        (monitor_id, _cutoff_date(days)),
    )
    combined = [0] * 24
    for r in rows:
        breakdown = json.loads(r["hourly_breakdown"])
        for i in range(min(len(breakdown), 24)):
            combined[i] += breakdown[i]
    return combined

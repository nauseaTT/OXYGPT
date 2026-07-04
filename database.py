"""SQLite database manager for the Telegram bot.

Provides the DatabaseManager class that handles all persistent storage:
- Conversation windows with history and session data
- Per-user usage tracking (requests, tokens, images, HTML files)
- User/group blocking and admin locks
- Application settings and pending states
- 12-hour rolling window metric resets
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Generator

class DatabaseManager:
    """
    Manages SQLite database operations for persisting user sessions, 
    pending states, conversation windows, and usage limits.
    """
    def __init__(self, db_path: str = "bot_database.db") -> None:
        """
        Initializes the database manager and runs migrations.

        Args:
            db_path (str): Path to the SQLite database file.
        """
        self.db_path: str = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager to ensure connections are always closed properly and handle timeouts.

        Yields:
            sqlite3.Connection: A connection object to the SQLite database.
        """
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes database tables and indexes if they do not exist."""
        with self._get_conn() as conn:
            # Table for AI sessions (legacy/fallback)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id INTEGER PRIMARY KEY,
                    history TEXT,
                    total_requests INTEGER DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0
                )
            """)
            # Table for multi-window conversations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_windows (
                    window_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    title TEXT,
                    history TEXT DEFAULT '[]',
                    total_requests INTEGER DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 0,
                    mode TEXT DEFAULT 'quick_ask',
                    mentor_key TEXT
                )
            """)
            # Table for tracking 12-hour user limits independently of windows
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_usage (
                    user_id INTEGER PRIMARY KEY,
                    total_requests INTEGER DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0,
                    total_images INTEGER DEFAULT 0,
                    total_html_files INTEGER DEFAULT 0
                )
            """)
            # Table for pending user states
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_states (
                    user_id INTEGER PRIMARY KEY,
                    pending_question INTEGER DEFAULT 0,
                    pending_role TEXT,
                    pending_mentor_key TEXT,
                    pending_action TEXT
                )
            """)
            # Table for settings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Table for blocked users
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_by INTEGER,
                    reason TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Table for blocked groups
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_groups (
                    group_id INTEGER PRIMARY KEY,
                    blocked_by INTEGER,
                    reason TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Table for group usage tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS group_usage (
                    group_id INTEGER PRIMARY KEY,
                    total_requests INTEGER DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0
                )
            """)
            # Table for locks (Mandatory Join)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS locks (
                    channel_id INTEGER PRIMARY KEY,
                    title TEXT,
                    invite_link TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            # Table for join logs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS join_logs (
                    user_id INTEGER,
                    channel_id INTEGER,
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, channel_id)
                )
            """)
            # Create composite index on join_logs for optimized time-based queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_join_logs_channel_time 
                ON join_logs (channel_id, joined_at)
            """)
            # Table for Gemini 503 auto-recovery state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gemini_503_state (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    is_downgraded INTEGER DEFAULT 0,
                    downgrade_time TEXT DEFAULT NULL,
                    recovery_scheduled INTEGER DEFAULT 0,
                    original_models TEXT DEFAULT NULL,
                    CHECK (id = 1)
                )
            """)
            # Migrations: add columns that may not exist in older databases
            cursor = conn.execute("PRAGMA table_info(user_usage)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "total_images" not in existing_cols:
                conn.execute("ALTER TABLE user_usage ADD COLUMN total_images INTEGER DEFAULT 0")
            if "total_html_files" not in existing_cols:
                conn.execute("ALTER TABLE user_usage ADD COLUMN total_html_files INTEGER DEFAULT 0")

            # Migration: add service_id to conversation_windows for multi-service support
            cursor = conn.execute("PRAGMA table_info(conversation_windows)")
            win_cols = {row[1] for row in cursor.fetchall()}
            if "service_id" not in win_cols:
                conn.execute("ALTER TABLE conversation_windows ADD COLUMN service_id TEXT DEFAULT NULL")

            # Migration: add last_interaction_time for second-verify feature
            if "last_interaction_time" not in win_cols:
                conn.execute("ALTER TABLE conversation_windows ADD COLUMN last_interaction_time TEXT DEFAULT NULL")

            # Migration: add last_user_message for caching last user message in verify
            if "last_user_message" not in win_cols:
                conn.execute("ALTER TABLE conversation_windows ADD COLUMN last_user_message TEXT DEFAULT NULL")

            conn.commit()

    # --- Multi-Window Conversation Methods ---

    def get_user_windows(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Retrieves all conversation windows for a specific user.

        Args:
            user_id (int): The Telegram user ID.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries representing the windows.
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM conversation_windows 
                WHERE user_id = ? 
                ORDER BY window_id ASC
            """, (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def is_window_owner(self, user_id: int, window_id: int) -> bool:
        """
        Security check to verify if a window belongs to a specific user.

        Args:
            user_id (int): The Telegram user ID.
            window_id (int): The window ID.

        Returns:
            bool: True if the user owns the window, False otherwise.
        """
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT 1 FROM conversation_windows 
                WHERE user_id = ? AND window_id = ?
            """, (user_id, window_id)).fetchone()
            return row is not None

    def get_active_window(self, user_id: int, default_mode: str = "quick_ask", default_mentor: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves the active conversation window for a user, creating a default one if none exists.

        When creating a default window, the INSERT now includes ``service_id``
        (set to NULL) to stay consistent with ``create_window()`` which accepts
        and stores a service_id. Previously the INSERT only covered 6 columns
        (user_id, title, history, is_active, mode, mentor_key) and omitted
        service_id, leaving it NULL automatically via the schema default.
        While functionally the same (NULL in both cases), explicitly listing
        service_id in the INSERT guarantees that any future schema default
        changes or column reordering won't break this code path.

        **Race-safe design:**
        After activating a window via UPDATE, we reuse the already-fetched row
        data instead of issuing a third SELECT to re-fetch it.  This eliminates
        the tiny window where another connection could change ``is_active``
        between the UPDATE and the re-fetch, which would cause ``dict(row)``
        to crash on ``None``.

        Args:
            user_id (int): The Telegram user ID.
            default_mode (str): Default mode if a new window needs to be created.
            default_mentor (Optional[str]): Default mentor key if a new window needs to be created.

        Returns:
            Dict[str, Any]: The active window record.
        """
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM conversation_windows 
                WHERE user_id = ? AND is_active = 1
            """, (user_id,)).fetchone()

            if row:
                return dict(row)

            # No active window → promote the first existing window, or create one
            any_row = conn.execute("""
                SELECT * FROM conversation_windows 
                WHERE user_id = ? 
                ORDER BY window_id ASC LIMIT 1
            """, (user_id,)).fetchone()

            if any_row:
                conn.execute("""
                    UPDATE conversation_windows 
                    SET is_active = 1 
                    WHERE window_id = ?
                """, (any_row["window_id"],))
                conn.commit()
                # Reuse the already-fetched row instead of a 3rd SELECT.
                # This eliminates a tiny race where another connection
                # could change is_active between our UPDATE and re-fetch.
                row = dict(any_row)
                row["is_active"] = 1
                return row

            # NOTE: service_id is explicitly set to NULL in the INSERT
            # to match create_window()'s column list.  This matters because
            # the service_manager checks service_id for per-window routing;
            # NULL = use Gemini (the default provider).
            title = "پنجره اصلی" if default_mode == "quick_ask" else f"منتور {default_mentor}"
            cursor = conn.execute("""
                INSERT INTO conversation_windows (user_id, title, history, is_active, mode, mentor_key, service_id)
                VALUES (?, ?, '[]', 1, ?, ?, NULL)
            """, (user_id, title, default_mode, default_mentor))
            conn.commit()

            new_id = cursor.lastrowid
            new_row = conn.execute("SELECT * FROM conversation_windows WHERE window_id = ?", (new_id,)).fetchone()
            return dict(new_row)

    def create_window(self, user_id: int, title: str, mode: str = "quick_ask", mentor_key: Optional[str] = None, service_id: Optional[str] = None) -> bool:
        """
        Creates a new conversation window for a user, up to a maximum of 5 windows.

        Uses ``BEGIN IMMEDIATE`` to acquire an exclusive write lock before the
        ``SELECT COUNT(*)`` so that two concurrent requests from the same user
        cannot both see a count < 5 and both insert a duplicate window for the
        same mode/mentor pair (Bug #1 race condition fix).

        Args:
            user_id: The Telegram user ID.
            title: The title of the window.
            mode: The mode of the window.
            mentor_key: The mentor key.
            service_id: The AI service ID (None for Gemini).

        Returns:
            True if creation was successful, False if the limit was reached.
        """
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) as cnt FROM conversation_windows WHERE user_id = ?", (user_id,)).fetchone()["cnt"]
            if count >= 5:
                conn.execute("ROLLBACK")
                return False

            is_active = 1 if count == 0 else 0

            conn.execute("""
                INSERT INTO conversation_windows (user_id, title, history, is_active, mode, mentor_key, service_id)
                VALUES (?, ?, '[]', ?, ?, ?, ?)
            """, (user_id, title, is_active, mode, mentor_key, service_id))
            conn.commit()
            return True

    def delete_window(self, user_id: int, window_id: int) -> bool:
        """
        Deletes a conversation window and sets another window as active if the deleted one was active.

        Args:
            user_id (int): The Telegram user ID.
            window_id (int): The window ID to delete.

        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        with self._get_conn() as conn:
            # Check if it exists and belongs to the user
            row = conn.execute("SELECT * FROM conversation_windows WHERE user_id = ? AND window_id = ?", (user_id, window_id)).fetchone()
            if not row:
                return False
            
            was_active = bool(row["is_active"])
            conn.execute("DELETE FROM conversation_windows WHERE user_id = ? AND window_id = ?", (user_id, window_id))
            
            if was_active:
                # Set another window as active if available
                sibling = conn.execute("""
                    SELECT window_id FROM conversation_windows 
                    WHERE user_id = ? 
                    ORDER BY window_id ASC LIMIT 1
                """, (user_id,)).fetchone()
                if sibling:
                    conn.execute("UPDATE conversation_windows SET is_active = 1 WHERE window_id = ?", (sibling["window_id"],))
            
            conn.commit()
            return True

    def rename_window(self, user_id: int, window_id: int, new_title: str) -> bool:
        """
        Renames a conversation window.

        Uses ``cursor.rowcount > 0`` to distinguish between a successful
        rename (a matching row was found and updated) and a no-op (no
        matching row — either the window doesn't exist or doesn't belong
        to this user). Previously this always returned ``True`` regardless
        of whether any row was actually updated, which made it impossible
        for callers to detect silent failures.

        Args:
            user_id (int): The Telegram user ID.
            window_id (int): The window ID.
            new_title (str): The new title.

        Returns:
            bool: True if a row was actually updated, False otherwise.
        """
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE conversation_windows 
                SET title = ? 
                WHERE user_id = ? AND window_id = ?
            """, (new_title, user_id, window_id))
            conn.commit()
            return cursor.rowcount > 0

    def set_active_window(self, user_id: int, window_id: int) -> bool:
        """
        Sets a specific window as active and deactivates all other windows for the user.

        Args:
            user_id (int): The Telegram user ID.
            window_id (int): The window ID to activate.

        Returns:
            bool: True if successful, False if the window does not belong to the user.
        """
        with self._get_conn() as conn:
            # Verify window belongs to user
            row = conn.execute("SELECT 1 FROM conversation_windows WHERE user_id = ? AND window_id = ?", (user_id, window_id)).fetchone()
            if not row:
                return False
            
            conn.execute("UPDATE conversation_windows SET is_active = 0 WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE conversation_windows SET is_active = 1 WHERE user_id = ? AND window_id = ?", (user_id, window_id))
            conn.commit()
            return True

    def save_window_session(self, window_id: int, history: List[Dict[str, Any]], total_requests: int, total_input_tokens: int, total_output_tokens: int) -> None:
        """
        Saves the conversation history and usage metrics for a specific window, enforcing a 40-message limit.

        Args:
            window_id (int): The window ID.
            history (List[Dict[str, Any]]): The conversation history list.
            total_requests (int): Total requests count.
            total_input_tokens (int): Total input tokens.
            total_output_tokens (int): Total output tokens.
        """
        # Enforce 40-message history limit directly in the database layer
        if isinstance(history, list) and len(history) > 40:
            history = history[-40:]
            
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE conversation_windows 
                SET history = ?,
                    total_requests = ?,
                    total_input_tokens = ?,
                    total_output_tokens = ?
                WHERE window_id = ?
            """, (json.dumps(history), total_requests, total_input_tokens, total_output_tokens, window_id))
            conn.commit()

    def get_last_user_message(self, history: List[Dict[str, Any]]) -> Optional[str]:
        """
        Extracts the last user message text from a conversation history list.

        Searches backwards through the history and returns the first 100 characters
        of the most recent message with role='user'.

        Args:
            history: List of {role, parts} dicts from a conversation window.

        Returns:
            The last user message text (truncated to 100 chars), or None if not found.
        """
        if not isinstance(history, list):
            return None
        for msg in reversed(history):
            if msg.get("role") == "user":
                parts = msg.get("parts", [])
                for part in reversed(parts):
                    if "text" in part and part["text"].strip():
                        text = part["text"].strip()
                        # Return first 100 chars
                        return text[:100]
        return None

    def update_window_interaction(self, window_id: int, history: List[Dict[str, Any]]) -> None:
        """
        Updates the last_interaction_time and last_user_message for a window.

        Called after a successful AI response to keep verify timestamps fresh.
        Uses ISO 8601 format (Tehran timezone) for the timestamp.

        Args:
            window_id: The window ID to update.
            history: The current conversation history list.
        """
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Tehran")
        except ImportError:
            from datetime import timezone, timedelta
            tz = timezone(timedelta(hours=3, minutes=30))

        now_str = datetime.now(tz).isoformat()
        last_msg = self.get_last_user_message(history)

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE conversation_windows
                SET last_interaction_time = ?,
                    last_user_message = ?
                WHERE window_id = ?
            """, (now_str, last_msg, window_id))
            conn.commit()

    def get_quick_ask_windows(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Retrieves all quick_ask mode windows for a user (excluding mentor windows).

        Args:
            user_id: The Telegram user ID.

        Returns:
            List of window dicts with mode='quick_ask'.
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM conversation_windows
                WHERE user_id = ? AND mode = 'quick_ask'
                ORDER BY window_id ASC
            """, (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_mentor_window(self, user_id: int, mentor_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the conversation window for a specific mentor.

        Args:
            user_id: The Telegram user ID.
            mentor_key: The mentor identifier (e.g. 'micheal').

        Returns:
            Window dict or None if no window exists for this mentor.
        """
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM conversation_windows
                WHERE user_id = ? AND mode = 'mentor' AND mentor_key = ?
                LIMIT 1
            """, (user_id, mentor_key)).fetchone()
            return dict(row) if row else None

    def reset_all_windows(self) -> None:
        """Resets history and metrics for all conversation windows in the database."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE conversation_windows 
                SET history = '[]', total_requests = 0, total_input_tokens = 0, total_output_tokens = 0
            """)
            conn.commit()

    # --- Persistent User Usage Tracking ---

    def get_user_usage(self, user_id: int) -> Dict[str, Any]:
        """
        Retrieves the persistent 12-hour usage metrics for a user.

        Args:
            user_id (int): The Telegram user ID.

        Returns:
            Dict[str, Any]: A dictionary containing usage metrics.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM user_usage WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return dict(row)
            return {
                "user_id": user_id,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0
            }

    def increment_user_usage(self, user_id: int, requests: int, input_tokens: int, output_tokens: int) -> None:
        """
        Increments the persistent 12-hour usage metrics for a user.

        Args:
            user_id (int): The Telegram user ID.
            requests (int): Number of requests to add.
            input_tokens (int): Number of input tokens to add.
            output_tokens (int): Number of output tokens to add.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO user_usage (user_id, total_requests, total_input_tokens, total_output_tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_requests = total_requests + excluded.total_requests,
                    total_input_tokens = total_input_tokens + excluded.total_input_tokens,
                    total_output_tokens = total_output_tokens + excluded.total_output_tokens
            """, (user_id, requests, input_tokens, output_tokens))
            conn.commit()

    def increment_image_usage(self, user_id: int, count: int = 1) -> None:
        """
        Increments the image generation counter for a user.

        Args:
            user_id (int): The Telegram user ID.
            count (int): Number of images to add.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO user_usage (user_id, total_images)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_images = total_images + excluded.total_images
            """, (user_id, count))
            conn.commit()

    def decrement_image_usage(self, user_id: int, count: int = 1) -> None:
        """
        Decrements the image generation counter (for rollback on error).

        Args:
            user_id (int): The Telegram user ID.
            count (int): Number of images to subtract.
        """
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE user_usage 
                SET total_images = MAX(0, total_images - ?)
                WHERE user_id = ?
            """, (count, user_id))
            conn.commit()

    def get_image_usage(self, user_id: int) -> int:
        """
        Retrieves the image generation count for a user.

        Args:
            user_id (int): The Telegram user ID.

        Returns:
            int: The number of images generated.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT total_images FROM user_usage WHERE user_id = ?", 
                (user_id,)
            ).fetchone()
            return row["total_images"] if row else 0

    def increment_html_usage(self, user_id: int, count: int = 1) -> None:
        """Increments the HTML file generation counter for a user."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO user_usage (user_id, total_html_files)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_html_files = total_html_files + excluded.total_html_files
            """, (user_id, count))
            conn.commit()

    def decrement_html_usage(self, user_id: int, count: int = 1) -> None:
        """Decrements the HTML file generation counter (for rollback on error)."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE user_usage 
                SET total_html_files = MAX(0, total_html_files - ?)
                WHERE user_id = ?
            """, (count, user_id))
            conn.commit()

    def get_html_usage(self, user_id: int) -> int:
        """Retrieves the HTML file generation count for a user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT total_html_files FROM user_usage WHERE user_id = ?", 
                (user_id,)
            ).fetchone()
            return row["total_html_files"] if row else 0

    def reset_usage_metrics(self) -> None:
        """Resets only the token usage, message counts, and image counts for all windows, sessions, and persistent user usage."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE conversation_windows 
                SET total_requests = 0, total_input_tokens = 0, total_output_tokens = 0
            """)
            conn.execute("""
                UPDATE sessions 
                SET total_requests = 0, total_input_tokens = 0, total_output_tokens = 0
            """)
            conn.execute("DELETE FROM user_usage")
            conn.execute("DELETE FROM group_usage")
            conn.commit()

    # --- Settings / Locks Methods ---

    def get_setting(self, key: str, default: str) -> str:
        """
        Retrieves a setting value from the database.

        Args:
            key (str): The setting key.
            default (str): The default value if the key is not found.

        Returns:
            str: The setting value.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def save_setting(self, key: str, value: str) -> None:
        """
        Saves or updates a setting value in the database.

        Args:
            key (str): The setting key.
            value (str): The setting value.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, value))
            conn.commit()

    # --- AI Service Management Methods ---

    def get_services(self) -> List[Dict[str, Any]]:
        """Get all configured AI services.

        Returns:
            List of service config dicts.
        """
        raw = self.get_setting("ai_services", "[]")
        try:
            return json.loads(raw)
        except Exception:
            return []

    def get_service(self, service_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific service by ID.

        Args:
            service_id: The service identifier.

        Returns:
            Service config dict or None if not found.
        """
        services = self.get_services()
        return next((s for s in services if s.get("id") == service_id), None)

    def save_service(self, service: Dict[str, Any]) -> None:
        """Save or update a service config.

        Args:
            service: Service config dict with at least 'id' key.
        """
        services = self.get_services()
        existing_idx = next((i for i, s in enumerate(services) if s.get("id") == service.get("id")), None)
        if existing_idx is not None:
            services[existing_idx] = service
        else:
            services.append(service)
        self.save_setting("ai_services", json.dumps(services, ensure_ascii=False))

    def delete_service(self, service_id: str) -> bool:
        """Delete a service by ID.

        Args:
            service_id: The service identifier.

        Returns:
            True if deleted, False if not found.
        """
        services = self.get_services()
        original_len = len(services)
        services = [s for s in services if s.get("id") != service_id]
        if len(services) < original_len:
            self.save_setting("ai_services", json.dumps(services, ensure_ascii=False))
            return True
        return False

    def get_window_service_id(self, window_id: int) -> Optional[str]:
        """Get the service_id assigned to a specific window.

        Args:
            window_id: The window ID.

        Returns:
            Service ID string, or None for Gemini.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT service_id FROM conversation_windows WHERE window_id = ?", (window_id,)).fetchone()
            if row:
                return row["service_id"]
            return None

    def set_window_service(self, window_id: int, service_id: Optional[str]) -> None:
        """Set the service_id for a specific window.

        Args:
            window_id: The window ID.
            service_id: Service ID string, or None for Gemini.
        """
        with self._get_conn() as conn:
            conn.execute("UPDATE conversation_windows SET service_id = ? WHERE window_id = ?", (service_id, window_id))
            conn.commit()

    def clear_all_windows_service_id(self) -> int:
        """Clear service_id from ALL conversation windows, resetting them to Gemini.

        Called when the admin switches the global provider away from OpenAI
        so that stale per-window service assignments don't override the new
        global provider choice.

        Returns:
            Number of rows affected.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE conversation_windows SET service_id = NULL WHERE service_id IS NOT NULL"
            )
            conn.commit()
            return cursor.rowcount

    def assign_service_to_unassigned_windows(self, service_id: str) -> int:
        """Assign a service_id to all windows that currently have no service.

        Called when the admin activates an OpenAI service so that existing
        windows immediately start routing through the new service instead
        of waiting for each user to send their next message.

        Args:
            service_id: The service ID to assign.

        Returns:
            Number of rows affected.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE conversation_windows SET service_id = ? WHERE service_id IS NULL",
                (service_id,)
            )
            conn.commit()
            return cursor.rowcount

    def get_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Redirects to the active window's session data to maintain consistency.

        Args:
            user_id (int): The Telegram user ID.

        Returns:
            Optional[Dict[str, Any]]: The session dictionary, or None.
        """
        win = self.get_active_window(user_id)
        if win:
            try:
                history_list = json.loads(win.get("history", "[]"))
            except Exception:
                history_list = []
            return {
                "history": json.dumps(history_list),
                "total_requests": win.get("total_requests", 0),
                "total_input_tokens": win.get("total_input_tokens", 0),
                "total_output_tokens": win.get("total_output_tokens", 0),
            }
        return None

    def save_session(self, user_id: int, history: List[Dict[str, Any]], total_requests: int, total_input_tokens: int, total_output_tokens: int) -> None:
        """Redirects saving to the active window session."""
        win = self.get_active_window(user_id)
        if win:
            self.save_window_session(win["window_id"], history, total_requests, total_input_tokens, total_output_tokens)

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Retrieves all legacy sessions from the database."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM sessions").fetchall()
            return [dict(row) for row in rows]

    def reset_all_sessions(self) -> None:
        """Resets history and metrics for all legacy sessions in the database."""
        with self._get_conn() as conn:
            conn.execute("UPDATE sessions SET history = '[]', total_requests = 0, total_input_tokens = 0, total_output_tokens = 0")
            conn.commit()

    def get_pending_state(self, user_id: int) -> Dict[str, Any]:
        """
        Retrieves the pending state for a user.

        Args:
            user_id (int): The Telegram user ID.

        Returns:
            Dict[str, Any]: A dictionary containing pending state flags and actions.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM pending_states WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return {
                    "pending_question": bool(row["pending_question"]),
                    "pending_role": row["pending_role"],
                    "pending_mentor_key": row["pending_mentor_key"],
                    "pending_action": row["pending_action"] if "pending_action" in row.keys() else None
                }
            return {
                "pending_question": False,
                "pending_role": None,
                "pending_mentor_key": None,
                "pending_action": None
            }

    def save_pending_state(self, user_id: int, pending_question: bool, pending_role: Optional[str] = None, pending_mentor_key: Optional[str] = None, pending_action: Optional[str] = None) -> None:
        """
        Saves or updates the pending state for a user.

        Args:
            user_id (int): The Telegram user ID.
            pending_question (bool): Whether a question is pending.
            pending_role (Optional[str]): The pending system role.
            pending_mentor_key (Optional[str]): The pending mentor key.
            pending_action (Optional[str]): The pending action identifier.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO pending_states (user_id, pending_question, pending_role, pending_mentor_key, pending_action)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    pending_question = excluded.pending_question,
                    pending_role = excluded.pending_role,
                    pending_mentor_key = excluded.pending_mentor_key,
                    pending_action = excluded.pending_action
            """, (user_id, int(pending_question), pending_role, pending_mentor_key, pending_action))
            conn.commit()

    def delete_pending_state(self, user_id: int) -> None:
        """Deletes the pending state for a user."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM pending_states WHERE user_id = ?", (user_id,))
            conn.commit()

    # --- Mandatory Join Locks Methods ---

    def add_lock(self, channel_id: int, title: str, invite_link: str) -> None:
        """
        Adds or updates a mandatory join lock for a channel.

        Args:
            channel_id (int): The Telegram channel ID.
            title (str): The title of the channel.
            invite_link (str): The invite link.
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO locks (channel_id, title, invite_link)
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    title = excluded.title,
                    invite_link = excluded.invite_link,
                    is_active = 1
            """, (channel_id, title, invite_link))
            conn.commit()

    def remove_lock(self, channel_id: int) -> None:
        """Deletes a mandatory join lock."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM locks WHERE channel_id = ?", (channel_id,))
            conn.commit()

    def get_locks(self) -> List[Dict[str, Any]]:
        """Retrieves all active mandatory join locks."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM locks WHERE is_active = 1").fetchall()
            return [dict(row) for row in rows]

    def log_join(self, user_id: int, channel_id: int) -> None:
        """Logs a successful join event for a user in a channel."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO join_logs (user_id, channel_id, joined_at)
                VALUES (?, ?, datetime('now'))
            """, (user_id, channel_id))
            conn.commit()

    def get_join_stats(self, channel_id: int) -> Dict[str, int]:
        """
        Retrieves join statistics for a channel.

        Args:
            channel_id (int): The channel ID.

        Returns:
            Dict[str, int]: A dictionary containing total joins and joins in the last 24 hours.
        """
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) as cnt FROM join_logs WHERE channel_id = ?", (channel_id,)).fetchone()["cnt"]
            last_24h = conn.execute("""
                SELECT COUNT(*) as cnt FROM join_logs 
                WHERE channel_id = ? AND joined_at >= datetime('now', '-1 day')
            """, (channel_id,)).fetchone()["cnt"]
            return {"total": total, "last_24h": last_24h}

    # --- Blocked Users Methods ---

    def block_user(self, user_id: int, blocked_by: int, reason: str = "") -> None:
        """Blocks a user from using the bot."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO blocked_users (user_id, blocked_by, reason)
                VALUES (?, ?, ?)
            """, (user_id, blocked_by, reason))
            conn.commit()

    def unblock_user(self, user_id: int) -> None:
        """Unblocks a user."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            conn.commit()

    def is_user_blocked(self, user_id: int) -> bool:
        """Checks if a user is blocked."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None

    def get_blocked_users(self) -> List[Dict[str, Any]]:
        """Returns all blocked users."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM blocked_users ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    # --- Blocked Groups Methods ---

    def block_group(self, group_id: int, blocked_by: int, reason: str = "") -> None:
        """Blocks a group from using the bot."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO blocked_groups (group_id, blocked_by, reason)
                VALUES (?, ?, ?)
            """, (group_id, blocked_by, reason))
            conn.commit()

    def unblock_group(self, group_id: int) -> None:
        """Unblocks a group."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM blocked_groups WHERE group_id = ?", (group_id,))
            conn.commit()

    def is_group_blocked(self, group_id: int) -> bool:
        """Checks if a group is blocked."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM blocked_groups WHERE group_id = ?", (group_id,)).fetchone()
            return row is not None

    def get_blocked_groups(self) -> List[Dict[str, Any]]:
        """Returns all blocked groups."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM blocked_groups ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    # --- Group Usage Methods ---

    def increment_group_usage(self, group_id: int, requests: int, input_tokens: int, output_tokens: int) -> None:
        """Increments usage metrics for a group."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO group_usage (group_id, total_requests, total_input_tokens, total_output_tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    total_requests = total_requests + excluded.total_requests,
                    total_input_tokens = total_input_tokens + excluded.total_input_tokens,
                    total_output_tokens = total_output_tokens + excluded.total_output_tokens
            """, (group_id, requests, input_tokens, output_tokens))
            conn.commit()

    def get_group_usage(self, group_id: int) -> Dict[str, Any]:
        """Retrieves usage metrics for a group."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM group_usage WHERE group_id = ?", (group_id,)).fetchone()
            if row:
                return dict(row)
            return {
                "group_id": group_id,
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0
            }

    def get_top_users_by_token(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns top users by total token consumption (input + output)."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT user_id, total_requests, total_input_tokens, total_output_tokens,
                       (total_input_tokens + total_output_tokens) as total_tokens
                FROM user_usage
                ORDER BY total_tokens DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_top_groups_by_token(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns top groups by total token consumption (input + output)."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT group_id, total_requests, total_input_tokens, total_output_tokens,
                       (total_input_tokens + total_output_tokens) as total_tokens
                FROM group_usage
                ORDER BY total_tokens DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def reset_group_usage(self) -> None:
        """Resets all group usage metrics."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM group_usage")
            conn.commit()

    # --- Gemini 503 Auto-Recovery Methods ---

    def get_503_state(self) -> Dict[str, Any]:
        """Get current 503 downgrade state.
        
        Returns:
            Dict with keys: is_downgraded, downgrade_time, recovery_scheduled, original_models
        """
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT is_downgraded, downgrade_time, recovery_scheduled, original_models
                FROM gemini_503_state 
                WHERE id = 1
            """).fetchone()
            
            if row:
                result = dict(row)
                # Parse JSON string to dict
                if result["original_models"]:
                    result["original_models"] = json.loads(result["original_models"])
                return result
            
            # Return default state if no record exists
            return {
                "is_downgraded": 0,
                "downgrade_time": None,
                "recovery_scheduled": 0,
                "original_models": None
            }

    def set_503_downgrade(self, original_models: Dict[str, str]) -> None:
        """Activate downgrade state and save backup.
        
        Args:
            original_models: Dict of setting keys to original model names
                            Example: {
                                "quick_ask_model": "gemini-3.5-flash",
                                "mentors_model": "gemini-3.5-flash"
                            }
        """
        from datetime import datetime
        
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO gemini_503_state (id, is_downgraded, downgrade_time, recovery_scheduled, original_models)
                VALUES (1, 1, ?, 1, ?)
            """, (datetime.utcnow().isoformat(), json.dumps(original_models)))
            conn.commit()

    def clear_503_downgrade(self) -> Dict[str, str]:
        """Clear downgrade state and return backup.
        
        Returns:
            Dict of original models to restore
        """
        with self._get_conn() as conn:
            # Get backup before clearing
            state = self.get_503_state()
            original_models = state.get("original_models", {})
            
            # Reset state
            conn.execute("""
                UPDATE gemini_503_state 
                SET is_downgraded = 0, downgrade_time = NULL, recovery_scheduled = 0, original_models = NULL
                WHERE id = 1
            """)
            conn.commit()
            
            return original_models or {}

    def downgrade_gemini_models(self) -> Dict[str, str]:
        """Downgrade all gemini-3.5-flash models to 3.1-flash-lite.
        
        Returns:
            Dict of changed settings (setting_key -> original_value)
        """
        with self._get_conn() as conn:
            # Find all settings with gemini-3.5-flash
            rows = conn.execute("""
                SELECT key, value FROM settings 
                WHERE value LIKE '%gemini-3.5-flash%'
            """).fetchall()
            
            changed_models = {}
            
            for row in rows:
                key = row["key"]
                old_value = row["value"]
                
                # Only change if it's exactly gemini-3.5-flash
                if old_value == "gemini-3.5-flash":
                    changed_models[key] = old_value
                    
                    # Update to gemini-3.1-flash-lite
                    conn.execute("""
                        UPDATE settings 
                        SET value = 'gemini-3.1-flash-lite' 
                        WHERE key = ?
                    """, (key,))
            
            conn.commit()
            return changed_models

    def restore_gemini_models(self, original_models: Dict[str, str]) -> None:
        """Restore models to their original values.
        
        Args:
            original_models: Dict from backup (setting_key -> original_value)
        """
        with self._get_conn() as conn:
            for key, value in original_models.items():
                conn.execute("""
                    UPDATE settings 
                    SET value = ? 
                    WHERE key = ?
                """, (value, key))
            
            conn.commit()

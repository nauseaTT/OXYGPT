"""State machine for the Channel Watcher setup flow and Ask AI mode.

Manages per-user conversational states (e.g. awaiting channel link,
awaiting delivery choice, awaiting Ask AI question) with an automatic
timeout that purges stale entries after a configurable idle period.

Usage::

    from channel_watcher.states import get_state_manager

    mgr = get_state_manager()
    await mgr.set_state(user_id, UserState.AWAITING_CHANNEL_LINK, {...})
    state, data = await mgr.get_state(user_id)
"""

import asyncio
import copy
import logging
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class UserState(Enum):
    """All possible states a user can be in during Channel Watcher flows."""
    IDLE = auto()
    AWAITING_CHANNEL_LINK = auto()
    AWAITING_DELIVERY_CHOICE = auto()
    AWAITING_GROUP_LINK = auto()
    AWAITING_CONFIRM = auto()
    AWAITING_INTERVAL = auto()
    AWAITING_SYSTEM_PROMPT = auto()
    AWAITING_ASK_AI = auto()
    AWAITING_MONITOR_DELETE = auto()


class StateManager:
    """Manages per-user states for the setup flow and Ask AI mode.

    Each entry carries a ``created_at`` timestamp and a background
    ``timeout_task`` that auto-clears the state after
    ``timeout_minutes`` of inactivity.  Any call to ``set_state``
    or ``get_state`` refreshes the timeout.

    Internal structure::

        {
            user_id: {
                "state": UserState,
                "data": { ... },
                "created_at": datetime,
                "timeout_task": asyncio.Task | None,
            }
        }
    """

    def __init__(self, timeout_minutes: int = 10) -> None:
        self._states: Dict[int, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._timeout_minutes = timeout_minutes

    async def set_state(
        self, user_id: int, state: UserState, data: Optional[dict] = None,
        timeout_minutes: Optional[int] = None,
    ) -> None:
        async with self._lock:
            old = self._states.get(user_id)
            if old and old.get("timeout_task"):
                old["timeout_task"].cancel()
            self._states[user_id] = {
                "state": state,
                "data": data or {},
                "created_at": datetime.now(),
                "timeout_task": None,
                "timeout_minutes": timeout_minutes or self._timeout_minutes,
            }
            self._states[user_id]["timeout_task"] = asyncio.create_task(
                self._start_timeout(user_id)
            )

    async def get_state(self, user_id: int) -> Tuple[Optional[UserState], Optional[dict]]:
        async with self._lock:
            entry = self._states.get(user_id)
            if entry is None:
                return None, None
            entry["created_at"] = datetime.now()
            if entry.get("timeout_task"):
                entry["timeout_task"].cancel()
            entry["timeout_task"] = asyncio.create_task(
                self._start_timeout(user_id)
            )
            return entry["state"], copy.deepcopy(entry["data"])

    async def peek_state(self, user_id: int) -> Tuple[Optional[UserState], Optional[dict]]:
        """Read the current state **without** touching the timeout.

        Use this for cheap "is this user mid-flow?" checks (e.g. the main
        bot's message router) that run on *every* message.  ``get_state``
        must NOT be used there because it refreshes the idle timeout, which
        would let a user stuck mid-flow keep the state alive forever and
        block normal chat.
        """
        async with self._lock:
            entry = self._states.get(user_id)
            if entry is None:
                return None, None
            return entry["state"], copy.deepcopy(entry["data"])

    async def clear_state(self, user_id: int) -> None:
        async with self._lock:
            entry = self._states.pop(user_id, None)
            if entry and entry.get("timeout_task"):
                entry["timeout_task"].cancel()

    async def is_in_state(self, user_id: int, state: UserState) -> bool:
        async with self._lock:
            entry = self._states.get(user_id)
            if entry is None:
                return False
            return entry["state"] == state

    async def _start_timeout(self, user_id: int) -> None:
        entry = self._states.get(user_id)
        timeout = (entry.get("timeout_minutes", self._timeout_minutes) * 60) if entry else self._timeout_minutes * 60
        await asyncio.sleep(timeout)
        async with self._lock:
            entry = self._states.get(user_id)
            if entry and entry.get("timeout_task") is asyncio.current_task():
                self._states.pop(user_id, None)
                logger.info("State for user %s timed out and was cleared", user_id)


_module_state_manager: Optional[StateManager] = None


def get_state_manager(timeout_minutes: int = 10) -> StateManager:
    """Return the module-level singleton ``StateManager``.

    Created on first call; subsequent calls return the same instance.
    """
    global _module_state_manager
    if _module_state_manager is None:
        _module_state_manager = StateManager(timeout_minutes)
    return _module_state_manager

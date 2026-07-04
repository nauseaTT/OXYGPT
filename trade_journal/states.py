import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

IDLE = "IDLE"
FILLING_FORM = "FILLING_FORM"
AWAIT_TEXT = "AWAIT_TEXT"
AWAIT_PHOTO = "AWAIT_PHOTO"
AWAIT_CHANNEL_FORWARD = "AWAIT_CHANNEL_FORWARD"
AWAIT_TEMPLATE_NAME = "AWAIT_TEMPLATE_NAME"
AWAIT_FIELD_LABEL = "AWAIT_FIELD_LABEL"
AWAIT_SYMBOL_NAME = "AWAIT_SYMBOL_NAME"
AWAIT_MARGIN = "AWAIT_MARGIN"
SHOWING_TEMPLATES = "SHOWING_TEMPLATES"
SHOWING_SYMBOLS = "SHOWING_SYMBOLS"
SHOWING_SETTINGS = "SHOWING_SETTINGS"

SESSION_TIMEOUT = 1800  # 30 minutes
_CLEANUP_INTERVAL = 300  # Run cleanup every 5 minutes
_last_cleanup = 0.0

user_states: Dict[int, Dict[str, Any]] = {}

# Lock for thread-safe state operations
import asyncio
_state_lock = asyncio.Lock()


def _cleanup_expired() -> None:
    """Remove expired user sessions — throttled to avoid O(n) on every call"""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    expired = []
    for uid, s in list(user_states.items()):
        if s.get("state") != IDLE:
            last_active = s.get("_last_active", now)
            if now - last_active > SESSION_TIMEOUT:
                expired.append(uid)

    for uid in expired:
        logger.debug(f"Session expired for user {uid} (inactive for {SESSION_TIMEOUT}s)")
        user_states.pop(uid, None)


def get_state(user_id: int) -> Dict[str, Any]:
    """Get current user state"""
    _cleanup_expired()
    state = user_states.get(user_id, {"state": IDLE, "data": {}})
    if state.get("state") != IDLE:
        state["_last_active"] = time.time()
    return state


def get_state_str(user_id: int) -> str:
    """Get user's current state string"""
    return get_state(user_id).get("state", IDLE)


def get_state_data(user_id: int) -> Dict[str, Any]:
    """Get user's state data"""
    return get_state(user_id).get("data", {})


def set_state(user_id: int, state: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Set user's state with optional data"""
    now = time.time()
    if data is not None:
        data["_last_active"] = now
        user_states[user_id] = {"state": state, "data": data}
    elif user_id in user_states:
        user_states[user_id]["state"] = state
        user_states[user_id].setdefault("data", {})["_last_active"] = now
    else:
        user_states[user_id] = {"state": state, "data": {"_last_active": now}}
    logger.debug(f"State changed for user {user_id}: {state}")


def update_state_data(user_id: int, **kwargs: Any) -> None:
    """Update user's state data"""
    now = time.time()
    if user_id in user_states:
        user_states[user_id]["data"].update(kwargs)
        user_states[user_id]["data"]["_last_active"] = now
    else:
        user_states[user_id] = {"state": IDLE, "data": {**kwargs, "_last_active": now}}
    logger.debug(f"State data updated for user {user_id}: {list(kwargs.keys())}")


def clear_state(user_id: int) -> None:
    """Clear user's state completely"""
    user_states.pop(user_id, None)
    logger.debug(f"State cleared for user {user_id}")

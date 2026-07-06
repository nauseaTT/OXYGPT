"""Centralised, deployment-friendly locations for OXYGPT's persistent state.

Historically every persistent artefact (the bot session file, the main
SQLite database, the Channel-Watcher and Trade-Journal databases, and the
Channel-Watcher user session) lived at a *relative* path in the process
working directory. That works for a checkout-and-run setup, but makes a
container deployment awkward: the state is scattered and easy to lose on a
rebuild.

This module introduces a single opt-in knob, the ``OXYGPT_DATA_DIR``
environment variable, that redirects **all** persistent state into one
directory (which a container mounts as a volume). When the variable is unset
the behaviour is byte-for-byte identical to before, so existing bare-metal
deployments are unaffected.

Usage
-----
    from paths import data_path, session_name

    db = DatabaseManager(data_path("bot_database.db"))
    client = TelegramClient(session_name("bot"), api_id, api_hash)
"""

from __future__ import annotations

import os


def data_dir() -> str | None:
    """Return the configured data directory, or ``None`` when unset.

    A returned directory is created on first use so callers never have to.
    """
    d = os.environ.get("OXYGPT_DATA_DIR", "").strip()
    if not d:
        return None
    os.makedirs(d, exist_ok=True)
    return d


def data_path(filename: str) -> str:
    """Resolve a state *file* path.

    When ``OXYGPT_DATA_DIR`` is set the file lives inside it (using only the
    basename, so nested defaults like ``trade_journal/journal.db`` collapse to
    ``<data>/journal.db``). Otherwise the original relative path is returned
    unchanged.
    """
    d = data_dir()
    if d is None:
        return filename
    return os.path.join(d, os.path.basename(filename))


def session_name(name: str) -> str:
    """Resolve a Telethon session *base name* (Telethon appends ``.session``).

    When a data dir is configured the session is stored there; otherwise the
    bare name (a relative path) is returned, preserving legacy behaviour.
    """
    d = data_dir()
    if d is None:
        return name
    return os.path.join(d, os.path.basename(name))

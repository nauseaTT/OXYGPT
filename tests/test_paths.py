"""Tests for the ``paths`` state-location helper.

Guards the twin guarantees the module makes:
  * unset ``OXYGPT_DATA_DIR`` -> byte-for-byte legacy behaviour;
  * set ``OXYGPT_DATA_DIR``   -> every artefact collapses into that one dir.
"""

import os
import importlib

import pytest

import paths


class TestUnset:
    def test_data_dir_none(self, monkeypatch):
        monkeypatch.delenv("OXYGPT_DATA_DIR", raising=False)
        assert paths.data_dir() is None

    def test_empty_string_is_unset(self, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", "   ")
        assert paths.data_dir() is None

    def test_data_path_passthrough(self, monkeypatch):
        monkeypatch.delenv("OXYGPT_DATA_DIR", raising=False)
        assert paths.data_path("bot_database.db") == "bot_database.db"

    def test_nested_default_passthrough(self, monkeypatch):
        monkeypatch.delenv("OXYGPT_DATA_DIR", raising=False)
        assert paths.data_path("trade_journal/journal.db") == "trade_journal/journal.db"

    def test_session_passthrough(self, monkeypatch):
        monkeypatch.delenv("OXYGPT_DATA_DIR", raising=False)
        assert paths.session_name("bot") == "bot"


class TestSet:
    def test_data_dir_created(self, tmp_path, monkeypatch):
        target = tmp_path / "state"
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(target))
        assert paths.data_dir() == str(target)
        assert target.is_dir()

    def test_data_path_uses_basename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path))
        assert paths.data_path("bot_database.db") == str(tmp_path / "bot_database.db")

    def test_nested_default_collapses_to_basename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path))
        assert paths.data_path("trade_journal/journal.db") == str(tmp_path / "journal.db")

    def test_session_uses_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path))
        assert paths.session_name("bot") == str(tmp_path / "bot")

    def test_channel_watcher_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path))
        assert paths.session_name("channel_watcher_user") == str(
            tmp_path / "channel_watcher_user"
        )


class TestDatabaseManagerIntegration:
    def test_default_honours_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path))
        from database import DatabaseManager
        dm = DatabaseManager()
        assert dm.db_path == str(tmp_path / "bot_database.db")

    def test_explicit_path_wins(self, tmp_path, monkeypatch):
        # An explicit db_path must NOT be rewritten even when the env is set.
        monkeypatch.setenv("OXYGPT_DATA_DIR", str(tmp_path / "ignored"))
        from database import DatabaseManager
        explicit = str(tmp_path / "explicit.db")
        dm = DatabaseManager(db_path=explicit)
        assert dm.db_path == explicit

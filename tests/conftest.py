"""pytest configuration: keeps tests away from the bot's real filesystem."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """No test may drop checkpoints next to the real bot."""
    monkeypatch.setattr("easy_server_cloner.state.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("easy_server_cloner.snapshot.BACKUP_DIR", tmp_path / "backups")
    return tmp_path

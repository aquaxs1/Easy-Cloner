"""pytest-Konfiguration: hält Tests vom echten Dateisystem des Bots fern."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Kein Test darf Zwischenstände neben dem echten Bot ablegen."""
    monkeypatch.setattr("easy_server_cloner.state.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("easy_server_cloner.snapshot.BACKUP_DIR", tmp_path / "backups")
    return tmp_path

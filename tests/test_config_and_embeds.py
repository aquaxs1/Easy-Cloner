"""Tests für Konfiguration und Embed-Aufbau."""

from __future__ import annotations

from unittest.mock import MagicMock

import discord
import pytest

from easy_server_cloner import embeds
from easy_server_cloner.config import Config, ConfigError
from easy_server_cloner.models import CloneOptions, CloneReport, Progress, Stage

# Discord-Grenzwerte, die ein Embed nicht überschreiten darf.
LIMIT_TITLE = 256
LIMIT_DESCRIPTION = 4096
LIMIT_FIELD_NAME = 256
LIMIT_FIELD_VALUE = 1024
LIMIT_TOTAL = 6000
LIMIT_FIELDS = 25


def assert_valid_embed(embed: discord.Embed) -> None:
    assert len(embed.title or "") <= LIMIT_TITLE
    assert len(embed.description or "") <= LIMIT_DESCRIPTION
    assert len(embed.fields) <= LIMIT_FIELDS
    for field in embed.fields:
        assert len(field.name or "") <= LIMIT_FIELD_NAME, field.name
        assert len(field.value or "") <= LIMIT_FIELD_VALUE, field.name
    assert len(embed) <= LIMIT_TOTAL


# ── Konfiguration ─────────────────────────────────────────────────────────────


def test_token_is_required(monkeypatch):
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="kein Bot-Token"):
        Config.load()


def test_placeholder_token_is_rejected(monkeypatch):
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DISCORD_TOKEN", "hier.deinen.bot-token.eintragen")

    with pytest.raises(ConfigError):
        Config.load()


def test_owner_ids_are_parsed(monkeypatch):
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DISCORD_TOKEN", "abc.def.ghi")
    monkeypatch.setenv("OWNER_IDS", "123, 456 ;789")

    cfg = Config.load()
    assert cfg.owner_ids == {123, 456, 789}


def test_invalid_owner_id_raises(monkeypatch):
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DISCORD_TOKEN", "abc.def.ghi")
    monkeypatch.setenv("OWNER_IDS", "123,nicht-numerisch")

    with pytest.raises(ConfigError, match="ungültigen Eintrag"):
        Config.load()


def test_clone_delay_has_a_floor(monkeypatch):
    """Zu kleine Pausen provozieren nur Rate-Limits statt Tempo."""
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DISCORD_TOKEN", "abc.def.ghi")
    monkeypatch.setenv("CLONE_DELAY", "0.0")

    assert Config.load().clone_delay >= 0.25


def test_is_owner_allows_everyone_when_list_empty():
    assert Config(token="x", owner_ids=set()).is_owner(42)
    assert not Config(token="x", owner_ids={1}).is_owner(42)
    assert Config(token="x", owner_ids={42}).is_owner(42)


# ── Embeds ────────────────────────────────────────────────────────────────────


def fake_guild(name: str = "Testserver", channels: int = 12, roles: int = 8):
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123456789012345678
    guild.name = name
    guild.icon = None
    guild.channels = [MagicMock() for _ in range(channels)]

    role_mocks = []
    for i in range(roles):
        role = MagicMock(spec=discord.Role)
        role.is_default.return_value = i == 0
        role.managed = False
        role_mocks.append(role)
    guild.roles = role_mocks
    return guild


def test_help_embed_is_within_limits():
    assert_valid_embed(embeds.help_embed())


def test_preview_embed_is_within_limits():
    counts = {"Rollen": 30, "Text-Channels": 60, "Voice-Channels": 12, "Emojis": 50}
    assert_valid_embed(
        embeds.preview_embed(fake_guild("Quelle"), fake_guild("Ziel"), counts, 480)
    )


def test_confirm_embed_is_within_limits():
    counts = {"Rollen": 30, "Text-Channels": 60}
    assert_valid_embed(
        embeds.confirm_embed(
            fake_guild("Quelle"), fake_guild("Ziel"), CloneOptions(), counts
        )
    )


def test_progress_embed_is_within_limits():
    report = CloneReport(roles_created=12, text_channels_created=30, emojis_created=4)
    progress = Progress(
        stage=Stage.CHANNELS, detail="#allgemein", current=17, total=42, report=report
    )
    assert_valid_embed(
        embeds.progress_embed(progress, fake_guild("Quelle"), fake_guild("Ziel"))
    )


def test_result_embed_truncates_a_flood_of_messages():
    """Ein kaputter Server darf das Ergebnis-Embed nicht sprengen."""
    report = CloneReport()
    for i in range(500):
        report.warn(f"Warnung Nummer {i} mit einem recht ausführlichen Text dahinter")
        report.fail(f"Fehler Nummer {i} mit einem recht ausführlichen Text dahinter")

    embed = embeds.result_embed(report, Stage.DONE, fake_guild("Q"), fake_guild("Z"))
    assert_valid_embed(embed)
    # Die Zähler bleiben trotz Kürzung korrekt.
    assert report.warning_count == 500
    assert report.error_count == 500


def test_servers_embed_handles_many_guilds():
    guilds = [fake_guild(f"Server {i}") for i in range(40)]
    user = MagicMock()
    user.__str__ = lambda self: "EasyCloner#0001"  # type: ignore[assignment]
    assert_valid_embed(embeds.servers_embed(guilds, user))


def test_servers_embed_handles_no_guilds():
    user = MagicMock()
    user.__str__ = lambda self: "EasyCloner#0001"  # type: ignore[assignment]
    assert_valid_embed(embeds.servers_embed([], user))


@pytest.mark.parametrize(
    "stage", [Stage.DONE, Stage.FAILED, Stage.CANCELLED]
)
def test_result_embed_for_every_end_state(stage):
    report = CloneReport(roles_created=3, text_channels_created=5)
    assert_valid_embed(
        embeds.result_embed(report, stage, fake_guild("Q"), fake_guild("Z"))
    )


def test_progress_bar_endpoints():
    assert "0%" in embeds.progress_bar(0.0)
    assert "100%" in embeds.progress_bar(1.0)
    # Werte außerhalb von 0..1 dürfen die Leiste nicht verformen.
    assert "100%" in embeds.progress_bar(5.0)
    assert "0%" in embeds.progress_bar(-1.0)

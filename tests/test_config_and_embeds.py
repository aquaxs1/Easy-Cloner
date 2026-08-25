"""Tests for the configuration and embed construction."""

from __future__ import annotations

from unittest.mock import MagicMock

import discord
import pytest

from easy_server_cloner import embeds
from easy_server_cloner.config import Config, ConfigError
from easy_server_cloner.models import CloneOptions, CloneReport, Progress, Stage
from easy_server_cloner.state import CloneState

from helpers import blueprint

# Discord limits an embed must not exceed.
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


def _env(monkeypatch, **values):
    monkeypatch.setattr("easy_server_cloner.config.load_dotenv", lambda *a, **k: False)
    for key in (
        "DISCORD_TOKEN",
        "BOT_TOKEN",
        "OWNER_IDS",
        "CLONE_DELAY",
        "ENABLE_MESSAGE_CLONE",
        "ENABLE_MEMBER_ROLES",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


# ── Konfiguration ─────────────────────────────────────────────────────────────


def test_token_is_required(monkeypatch):
    _env(monkeypatch)
    with pytest.raises(ConfigError, match="No bot token"):
        Config.load()


def test_placeholder_token_is_rejected(monkeypatch):
    _env(monkeypatch, DISCORD_TOKEN="put.your.bot-token.here")
    with pytest.raises(ConfigError):
        Config.load()


def test_owner_ids_are_parsed(monkeypatch):
    _env(monkeypatch, DISCORD_TOKEN="abc.def.ghi", OWNER_IDS="123, 456 ;789")
    assert Config.load().owner_ids == {123, 456, 789}


def test_invalid_owner_id_raises(monkeypatch):
    _env(monkeypatch, DISCORD_TOKEN="abc.def.ghi", OWNER_IDS="123,not-numeric")
    with pytest.raises(ConfigError, match="invalid entry"):
        Config.load()


def test_clone_delay_has_a_floor(monkeypatch):
    """Pauses that are too short only provoke rate limits instead of speed."""
    _env(monkeypatch, DISCORD_TOKEN="abc.def.ghi", CLONE_DELAY="0.0")
    assert Config.load().clone_delay >= 0.25


@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("YES", True), ("1", True), ("false", False), ("no", False), ("", False)],
)
def test_privileged_flags_are_parsed(monkeypatch, value, expected):
    _env(monkeypatch, DISCORD_TOKEN="abc.def.ghi", ENABLE_MESSAGE_CLONE=value)
    assert Config.load().enable_message_clone is expected


def test_invalid_boolean_raises(monkeypatch):
    _env(monkeypatch, DISCORD_TOKEN="abc.def.ghi", ENABLE_MEMBER_ROLES="vielleicht")
    with pytest.raises(ConfigError, match="not a boolean"):
        Config.load()


def test_is_owner_allows_everyone_when_list_empty():
    assert Config(token="x", owner_ids=set()).is_owner(42)
    assert not Config(token="x", owner_ids={1}).is_owner(42)
    assert Config(token="x", owner_ids={42}).is_owner(42)


# ── Embeds ────────────────────────────────────────────────────────────────────


def test_help_embed_is_within_limits():
    assert_valid_embed(embeds.help_embed())


def test_preview_embed_is_within_limits():
    assert_valid_embed(embeds.preview_embed(blueprint("Source"), fake_guild("Target"), 480))


def test_confirm_embed_is_within_limits():
    assert_valid_embed(
        embeds.confirm_embed(blueprint("Source"), fake_guild("Target"), CloneOptions())
    )


def test_confirm_embed_shows_resume_instead_of_deletion_warning():
    """Resuming deletes nothing — the prompt must not claim otherwise."""
    state = CloneState(target_id=2, roles={"a": 1}, channels={"b": 2})
    embed = embeds.confirm_embed(
        blueprint("Source"), fake_guild("Target"), CloneOptions(wipe_target=True), resume_state=state
    )
    names = [f.name for f in embed.fields]

    assert any("Resuming" in n for n in names)
    assert not any("geleert" in n for n in names)
    assert_valid_embed(embed)


def test_confirm_embed_flags_message_reconstruction():
    embed = embeds.confirm_embed(
        blueprint("Source"), fake_guild("Target"), CloneOptions(messages_per_channel=50)
    )
    text = " ".join(f.value for f in embed.fields)

    assert "re-staged" in text and "not migrated" in text
    assert_valid_embed(embed)


def test_progress_embed_is_within_limits():
    report = CloneReport(roles_created=12, text_channels_created=30, emojis_created=4)
    progress = Progress(
        stage=Stage.CHANNELS, detail="#allgemein", current=17, total=42, report=report
    )
    assert_valid_embed(embeds.progress_embed(progress, "Source", fake_guild("Target")))


def test_progress_embed_reports_throttling():
    progress = Progress(stage=Stage.ROLES, throttled=True, delay=1.28)
    embed = embeds.progress_embed(progress, "Source", fake_guild("Target"))

    assert "1.28s" in " ".join(f.value for f in embed.fields)
    assert_valid_embed(embed)


def test_result_embed_truncates_a_flood_of_messages():
    """A broken server must not blow up the result embed."""
    report = CloneReport()
    for i in range(500):
        report.warn(f"Warning number {i} with a fairly verbose explanation after it")
        report.fail(f"Error number {i} with a fairly verbose explanation after it")

    embed = embeds.result_embed(report, Stage.DONE, "Source", fake_guild("Target"))
    assert_valid_embed(embed)
    # The counters stay correct despite the trimming.
    assert report.warning_count == 500
    assert report.error_count == 500


@pytest.mark.parametrize("stage", [Stage.DONE, Stage.FAILED, Stage.CANCELLED])
def test_result_embed_for_every_end_state(stage):
    report = CloneReport(roles_created=3, text_channels_created=5, messages_copied=9)
    assert_valid_embed(embeds.result_embed(report, stage, "Source", fake_guild("Target")))


def test_cancelled_result_points_at_resume():
    embed = embeds.result_embed(CloneReport(), Stage.CANCELLED, "Q", fake_guild("Z"))
    assert "resume:True" in " ".join(f.value for f in embed.fields)


def test_backup_embed_is_within_limits():
    assert_valid_embed(
        embeds.backup_embed(blueprint("Source"), "source.esc.json.gz", "1.4 MB", True, "backups/x")
    )


def test_backup_too_large_embed_suggests_a_way_out():
    embed = embeds.backup_too_large_embed("14.2 MB", "10.0 MB")
    assert "include_images:False" in (embed.description or "")
    assert_valid_embed(embed)


def test_servers_embed_handles_many_guilds():
    guilds = [fake_guild(f"Server {i}") for i in range(40)]
    user = MagicMock()
    user.__str__ = lambda self: "EasyCloner#0001"  # type: ignore[assignment]
    assert_valid_embed(embeds.servers_embed(guilds, user))


def test_servers_embed_handles_no_guilds():
    user = MagicMock()
    user.__str__ = lambda self: "EasyCloner#0001"  # type: ignore[assignment]
    assert_valid_embed(embeds.servers_embed([], user))


def test_progress_bar_endpoints():
    assert "0%" in embeds.progress_bar(0.0)
    assert "100%" in embeds.progress_bar(1.0)
    # Values outside 0..1 must not deform the bar.
    assert "100%" in embeds.progress_bar(5.0)
    assert "0%" in embeds.progress_bar(-1.0)

"""
Tests für die Klon-Engine.

Der Fokus liegt auf den Fehlern, die in der ursprünglichen Skript-Version
steckten: verdrehte Rollen-Hierarchie, hart kodiertes Bitrate-Limit,
identische Quelle und Ziel sowie das versehentliche Löschen des Ziel-Icons.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from easy_server_cloner.cloner import CloneError, ServerCloner
from easy_server_cloner.models import CloneOptions


# ── Hilfsobjekte ──────────────────────────────────────────────────────────────


def make_role(role_id: int, name: str, position: int, *, default=False, managed=False):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.position = position
    role.managed = managed
    role.is_default.return_value = default
    role.permissions = discord.Permissions.none()
    role.colour = discord.Colour.default()
    role.hoist = False
    role.mentionable = False
    role.display_icon = None
    return role


def make_guild(guild_id: int, name: str, *, roles=None, bot_top_position=99):
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name
    guild.roles = roles or []
    guild.channels = []
    guild.categories = []
    guild.text_channels = []
    guild.voice_channels = []
    guild.stage_channels = []
    guild.forums = []
    guild.emojis = []
    guild.stickers = []
    guild.features = []
    guild.icon = None
    guild.banner = None
    guild.splash = None
    guild.description = None
    guild.afk_channel = None
    guild.system_channel = None
    guild.bitrate_limit = 96000
    guild.emoji_limit = 50
    guild.sticker_limit = 5
    # Wie bei Discord ist die @everyone-Rolle Teil der Rollenliste.
    guild.default_role = next(
        (r for r in guild.roles if r.is_default()),
        make_role(guild_id, "@everyone", 0, default=True),
    )

    me = MagicMock(spec=discord.Member)
    me.id = 999
    me.guild_permissions = discord.Permissions.all()
    me.top_role = make_role(guild_id + 1, "Bot", bot_top_position)
    guild.me = me
    guild.owner_id = 1

    guild.create_role = AsyncMock(
        side_effect=lambda **kw: make_role(hash(kw["name"]) & 0xFFFF, kw["name"], 1)
    )
    guild.edit = AsyncMock()
    guild.default_role.edit = AsyncMock()
    return guild


def cloner_for(source, target, **kw):
    return ServerCloner(source, target, delay=0, **kw)


# ── Sicherheitsnetz: Quelle == Ziel ───────────────────────────────────────────


def test_source_equals_target_is_rejected():
    """Der schlimmste denkbare Bedienfehler muss vor dem ersten Löschen greifen."""
    guild = make_guild(1, "Mein Server")
    c = cloner_for(guild, guild)

    with pytest.raises(CloneError, match="identisch"):
        asyncio.run(c.run())

    guild.edit.assert_not_awaited()


def test_missing_permissions_are_rejected():
    source = make_guild(1, "Quelle")
    target = make_guild(2, "Ziel")
    target.me.guild_permissions = discord.Permissions.none()

    c = cloner_for(source, target)
    with pytest.raises(CloneError, match="fehlen"):
        asyncio.run(c.run())


def test_bot_not_in_target_is_rejected():
    source = make_guild(1, "Quelle")
    target = make_guild(2, "Ziel")
    target.me = None

    c = cloner_for(source, target)
    with pytest.raises(CloneError, match="kein Mitglied"):
        asyncio.run(c.run())


# ── Rollen-Hierarchie ─────────────────────────────────────────────────────────


def test_roles_are_created_from_highest_to_lowest():
    """
    Discord legt jede neue Rolle ganz unten an. Wer aufsteigend kopiert, dreht
    die Hierarchie um — genau der Bug im Originalskript. Erwartet wird also
    absteigende Erstellungsreihenfolge.
    """
    admin = make_role(10, "Admin", 3)
    mod = make_role(11, "Mod", 2)
    member = make_role(12, "Member", 1)
    everyone = make_role(1, "@everyone", 0, default=True)
    integration = make_role(13, "BotRolle", 4, managed=True)

    source = make_guild(1, "Quelle", roles=[everyone, member, mod, admin, integration])
    target = make_guild(2, "Ziel")

    c = cloner_for(source, target)
    asyncio.run(c._clone_roles())

    created = [call.kwargs["name"] for call in target.create_role.await_args_list]
    assert created == ["Admin", "Mod", "Member"]
    # @everyone und verwaltete Integrations-Rollen dürfen nicht kopiert werden.
    assert "BotRolle" not in created
    assert "@everyone" not in created
    assert c.report.roles_created == 3


def test_everyone_permissions_are_transferred():
    everyone = make_role(1, "@everyone", 0, default=True)
    everyone.permissions = discord.Permissions(send_messages=True)
    source = make_guild(1, "Quelle", roles=[everyone])
    target = make_guild(2, "Ziel")

    c = cloner_for(source, target)
    asyncio.run(c._clone_roles())

    target.default_role.edit.assert_awaited_once()
    assert target.default_role.edit.await_args.kwargs["permissions"] == everyone.permissions


def test_role_creation_falls_back_without_permissions():
    """Kann der Bot die Rechte nicht vergeben, entsteht trotzdem die Rolle."""
    role = make_role(10, "Admin", 1)
    source = make_guild(1, "Quelle", roles=[role])
    target = make_guild(2, "Ziel")

    calls = []

    async def create_role(**kw):
        calls.append(kw)
        if "permissions" in kw:
            raise discord.Forbidden(MagicMock(status=403), "missing access")
        return make_role(50, kw["name"], 1)

    target.create_role = AsyncMock(side_effect=create_role)

    c = cloner_for(source, target)
    asyncio.run(c._clone_roles())

    assert len(calls) == 2
    assert "permissions" not in calls[1]
    assert c.report.roles_created == 1


# ── Permission-Overwrites ─────────────────────────────────────────────────────


def test_overwrites_map_roles_and_default_role():
    source_everyone = make_role(1, "@everyone", 0, default=True)
    source_mod = make_role(11, "Mod", 2)
    unmapped = make_role(12, "Fremd", 3)

    source = make_guild(1, "Quelle")
    target = make_guild(2, "Ziel")
    c = cloner_for(source, target)

    new_mod = make_role(77, "Mod", 1)
    c.role_map[11] = new_mod

    ov_everyone = discord.PermissionOverwrite(view_channel=False)
    ov_mod = discord.PermissionOverwrite(view_channel=True)
    ov_other = discord.PermissionOverwrite(manage_messages=True)

    result = c._build_overwrites(
        {source_everyone: ov_everyone, source_mod: ov_mod, unmapped: ov_other}
    )

    assert result == {target.default_role: ov_everyone, new_mod: ov_mod}


def test_member_overwrites_are_skipped_with_single_warning():
    member_a = MagicMock(spec=discord.Member)
    member_b = MagicMock(spec=discord.Member)
    source, target = make_guild(1, "Q"), make_guild(2, "Z")
    c = cloner_for(source, target)

    ov = discord.PermissionOverwrite(view_channel=True)
    result = c._build_overwrites({member_a: ov, member_b: ov})

    assert result == {}
    # Bei 200 Channels sollen nicht 200 identische Warnungen entstehen.
    assert c.report.warning_count == 1


# ── Server-Einstellungen ──────────────────────────────────────────────────────


def test_missing_source_icon_does_not_wipe_target_icon():
    """
    Im Originalskript wurde `icon=None` übergeben, sobald die Quelle kein Icon
    hatte — das löschte das Icon des Ziel-Servers.
    """
    source = make_guild(1, "Quelle")
    source.icon = None
    target = make_guild(2, "Ziel")

    c = cloner_for(source, target, options=CloneOptions())
    asyncio.run(c._clone_settings())

    kwargs = target.edit.await_args.kwargs
    assert "icon" not in kwargs
    assert "banner" not in kwargs
    assert kwargs["name"] == "Quelle"


def test_source_icon_is_read_and_transferred():
    source = make_guild(1, "Quelle")
    asset = MagicMock(spec=discord.Asset)
    asset.read = AsyncMock(return_value=b"PNGDATA")
    source.icon = asset
    target = make_guild(2, "Ziel")

    c = cloner_for(source, target)
    asyncio.run(c._clone_settings())

    assert target.edit.await_args.kwargs["icon"] == b"PNGDATA"


# ── Voice-Bitrate ─────────────────────────────────────────────────────────────


def test_bitrate_uses_target_limit_not_hardcoded_value():
    """
    Fest verdrahtete 96 kbit/s verschenken auf geboosteten Ziel-Servern Qualität.
    Maßgeblich ist das Limit des Ziel-Servers.
    """
    source, target = make_guild(1, "Q"), make_guild(2, "Z")
    target.bitrate_limit = 384000
    target.create_voice_channel = AsyncMock(return_value=MagicMock())

    channel = MagicMock(spec=discord.VoiceChannel)
    channel.name = "Talk"
    channel.bitrate = 256000
    channel.user_limit = 0
    channel.rtc_region = None
    channel.video_quality_mode = discord.VideoQualityMode.auto

    c = cloner_for(source, target)
    asyncio.run(c._create_voice_channel(channel, {"name": "Talk"}))

    assert target.create_voice_channel.await_args.kwargs["bitrate"] == 256000


def test_bitrate_is_capped_at_target_limit():
    source, target = make_guild(1, "Q"), make_guild(2, "Z")
    target.bitrate_limit = 64000
    target.create_voice_channel = AsyncMock(return_value=MagicMock())

    channel = MagicMock(spec=discord.VoiceChannel)
    channel.name = "Talk"
    channel.bitrate = 256000
    channel.user_limit = 0
    channel.rtc_region = None
    channel.video_quality_mode = discord.VideoQualityMode.auto

    c = cloner_for(source, target)
    asyncio.run(c._create_voice_channel(channel, {"name": "Talk"}))

    assert target.create_voice_channel.await_args.kwargs["bitrate"] == 64000


# ── Abbruch ───────────────────────────────────────────────────────────────────


def test_cancel_stops_the_run():
    source = make_guild(1, "Quelle", roles=[make_role(10 + i, f"R{i}", i) for i in range(5)])
    target = make_guild(2, "Ziel")

    c = ServerCloner(source, target, delay=0.01)
    original = target.create_role

    async def create_and_cancel(**kw):
        c.cancel()
        return await original(**kw)

    target.create_role = AsyncMock(side_effect=create_and_cancel)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c.run())

    assert c.cancelled

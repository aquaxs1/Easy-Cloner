"""Gemeinsame Test-Attrappen.

`MagicMock(spec=...)` passes `isinstance` checks, so it can stand in for a
Discord server without building real gateway objects.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from easy_server_cloner.blueprint import (
    EVERYONE_KEY,
    ChannelBlueprint,
    GuildBlueprint,
    RoleBlueprint,
)


# ── Bausteine ─────────────────────────────────────────────────────────────────


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
    role.__lt__ = lambda self, other: self.position < other.position
    return role


def make_guild(guild_id: int, name: str, *, roles=None, bot_top_position=99, features=None):
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
    guild.features = features or []
    guild.icon = None
    guild.banner = None
    guild.splash = None
    guild.description = None
    guild.afk_channel = None
    guild.system_channel = None
    guild.afk_timeout = 300
    guild.bitrate_limit = 96000
    guild.emoji_limit = 50
    guild.sticker_limit = 5
    guild.verification_level = discord.VerificationLevel.none
    guild.default_notifications = discord.NotificationLevel.all_messages
    guild.explicit_content_filter = discord.ContentFilter.disabled
    guild.system_channel_flags = discord.SystemChannelFlags()

    # As in Discord, @everyone is part of the role list.
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
        side_effect=lambda **kw: make_role(abs(hash(kw["name"])) % 100000, kw["name"], 1)
    )
    guild.edit = AsyncMock()
    guild.default_role.edit = AsyncMock()
    guild.get_role = MagicMock(return_value=None)
    guild.get_channel = MagicMock(return_value=None)
    return guild


def role_bp(key: str, name: str, position: int, *, default=False, permissions=0):
    return RoleBlueprint(
        key=EVERYONE_KEY if default else key,
        name=name,
        permissions=permissions,
        colour=0,
        hoist=False,
        mentionable=False,
        position=position,
        is_default=default,
    )


def channel_bp(key: str, name: str, kind: str, position: int = 0, **kwargs):
    return ChannelBlueprint(key=key, name=name, kind=kind, position=position, **kwargs)


def blueprint(name="Source", *, roles=None, channels=None, source_id="1", **kwargs):
    return GuildBlueprint(
        name=name,
        roles=roles or [role_bp(EVERYONE_KEY, "@everyone", 0, default=True)],
        channels=channels or [],
        source_id=source_id,
        source_name=name,
        **kwargs,
    )

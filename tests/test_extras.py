"""Tests for message re-staging and role assignment."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from easy_server_cloner import extras
from easy_server_cloner.blueprint import EVERYONE_KEY, KIND_TEXT, KIND_VOICE
from easy_server_cloner.cloner import ServerCloner

from helpers import blueprint, channel_bp, make_guild, make_role, role_bp


def make_message(content="Hallo", author_name="Alice", attachments=(), embeds_=()):
    message = MagicMock(spec=discord.Message)
    message.content = content
    message.attachments = list(attachments)
    message.embeds = list(embeds_)
    author = MagicMock()
    author.display_name = author_name
    avatar = MagicMock()
    avatar.url = "https://example.invalid/a.png"
    author.display_avatar = avatar
    message.author = author
    return message


def make_history_channel(messages):
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "allgemein"

    def history(**kwargs):
        async def gen():
            for message in messages:
                yield message

        return gen()

    channel.history = history
    return channel


def build_cloner(target, source, channels=None):
    bp = blueprint(channels=channels or [channel_bp("20", "allgemein", KIND_TEXT)])
    return ServerCloner(bp, target, delay=0, source_guild=source)


# ── Messages ──────────────────────────────────────────────────────────────────


def test_messages_are_reposted_in_chronological_order():
    """Discord returns newest first — the order in the target must be right."""
    source = make_guild(1, "Source")
    target = make_guild(2, "Target")

    origin = make_history_channel([make_message("neu"), make_message("alt")])
    source.get_channel = MagicMock(return_value=origin)

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "allgemein"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.send = AsyncMock()
    webhook.delete = AsyncMock()
    mirror.create_webhook = AsyncMock(return_value=webhook)

    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror

    asyncio.run(extras.copy_messages(cloner, source, 50))

    sent = [call.kwargs["content"] for call in webhook.send.await_args_list]
    assert sent == ["alt", "neu"]
    assert cloner.report.messages_copied == 2


def test_webhook_is_removed_afterwards():
    """A webhook left behind would be a permanent security hole."""
    source, target = make_guild(1, "Source"), make_guild(2, "Target")
    source.get_channel = MagicMock(return_value=make_history_channel([make_message()]))

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "allgemein"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.send = AsyncMock()
    webhook.delete = AsyncMock()
    mirror.create_webhook = AsyncMock(return_value=webhook)

    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror
    asyncio.run(extras.copy_messages(cloner, source, 10))

    webhook.delete.assert_awaited_once()


def test_webhook_is_removed_even_when_sending_fails():
    source, target = make_guild(1, "Source"), make_guild(2, "Target")
    source.get_channel = MagicMock(return_value=make_history_channel([make_message()]))

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "allgemein"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.send = AsyncMock(side_effect=RuntimeError("Verbindung weg"))
    webhook.delete = AsyncMock()
    mirror.create_webhook = AsyncMock(return_value=webhook)

    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror

    with pytest.raises(RuntimeError):
        asyncio.run(extras.copy_messages(cloner, source, 10))

    webhook.delete.assert_awaited_once()


def test_mass_mentions_are_defused_and_nobody_is_pinged():
    source, target = make_guild(1, "Source"), make_guild(2, "Target")
    source.get_channel = MagicMock(
        return_value=make_history_channel([make_message("Heads up @everyone and @here!")])
    )

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "allgemein"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.send = AsyncMock()
    webhook.delete = AsyncMock()
    mirror.create_webhook = AsyncMock(return_value=webhook)

    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror
    asyncio.run(extras.copy_messages(cloner, source, 10))

    kwargs = webhook.send.await_args.kwargs
    # An invisible character breaks the mention apart.
    assert "@everyone" not in kwargs["content"]
    assert "@here" not in kwargs["content"]
    assert kwargs["allowed_mentions"].everyone is False


def test_overlong_content_is_trimmed():
    source, target = make_guild(1, "Source"), make_guild(2, "Target")
    source.get_channel = MagicMock(
        return_value=make_history_channel([make_message("x" * 5000)])
    )

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "allgemein"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.send = AsyncMock()
    webhook.delete = AsyncMock()
    mirror.create_webhook = AsyncMock(return_value=webhook)

    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror
    asyncio.run(extras.copy_messages(cloner, source, 10))

    assert len(webhook.send.await_args.kwargs["content"]) <= extras.MAX_CONTENT


def test_voice_channels_are_not_considered():
    source, target = make_guild(1, "Source"), make_guild(2, "Target")
    cloner = build_cloner(target, source, channels=[channel_bp("30", "Talk", KIND_VOICE)])
    cloner.channel_map["30"] = MagicMock(spec=discord.VoiceChannel)

    asyncio.run(extras.copy_messages(cloner, source, 10))

    assert cloner.report.messages_copied == 0


def test_unreadable_history_is_reported_not_fatal():
    source, target = make_guild(1, "Source"), make_guild(2, "Target")

    origin = MagicMock(spec=discord.TextChannel)
    origin.name = "geheim"

    def history(**kwargs):
        async def gen():
            raise discord.Forbidden(MagicMock(status=403), "no access")
            yield  # pragma: no cover

        return gen()

    origin.history = history
    source.get_channel = MagicMock(return_value=origin)

    mirror = MagicMock(spec=discord.TextChannel)
    mirror.name = "geheim"
    cloner = build_cloner(target, source)
    cloner.channel_map["20"] = mirror

    asyncio.run(extras.copy_messages(cloner, source, 10))

    assert any("No access" in w for w in cloner.report.warnings)


# ── Role assignment ───────────────────────────────────────────────────────────


def make_member(user_id, roles=(), bot=False):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = bot
    member.roles = list(roles)
    member.add_roles = AsyncMock()
    return member


def target_with_members(members):
    target = make_guild(2, "Target")

    def fetch_members(**kwargs):
        async def gen():
            for member in members:
                yield member

        return gen()

    target.fetch_members = fetch_members
    return target


def test_roles_are_assigned_to_shared_members():
    source_mod = make_role(11, "Mod", 2)
    everyone = make_role(1, "@everyone", 0, default=True)
    source = make_guild(1, "Source")
    source_member = make_member(555, roles=[everyone, source_mod])
    source.get_member = MagicMock(return_value=source_member)

    target_member = make_member(555)
    target = target_with_members([target_member])

    bp = blueprint(roles=[role_bp("11", "Mod", 2)])
    cloner = ServerCloner(bp, target, delay=0, source_guild=source)
    new_mod = make_role(77, "Mod", 1)
    cloner.role_map["11"] = new_mod

    asyncio.run(extras.assign_member_roles(cloner, source))

    target_member.add_roles.assert_awaited_once()
    assert new_mod in target_member.add_roles.await_args.args
    assert cloner.report.members_updated == 1


def test_bots_and_strangers_are_skipped():
    source = make_guild(1, "Source")
    source.get_member = MagicMock(return_value=None)  # nobody is on both servers

    bot_member = make_member(1, bot=True)
    stranger = make_member(2)
    target = target_with_members([bot_member, stranger])

    cloner = ServerCloner(blueprint(), target, delay=0, source_guild=source)
    asyncio.run(extras.assign_member_roles(cloner, source))

    bot_member.add_roles.assert_not_awaited()
    stranger.add_roles.assert_not_awaited()
    assert cloner.report.members_updated == 0


def test_roles_above_the_bot_are_not_attempted():
    """Discord forbids it anyway — trying would only add another error."""
    source_admin = make_role(11, "Admin", 5)
    source = make_guild(1, "Source")
    source.get_member = MagicMock(return_value=make_member(555, roles=[source_admin]))

    target_member = make_member(555)
    target = target_with_members([target_member])
    target.me.top_role = make_role(900, "Bot", 3)

    cloner = ServerCloner(blueprint(), target, delay=0, source_guild=source)
    # The mapped role sits ABOVE the bot's role.
    cloner.role_map["11"] = make_role(77, "Admin", 8)

    asyncio.run(extras.assign_member_roles(cloner, source))

    target_member.add_roles.assert_not_awaited()


def test_missing_members_intent_is_reported_clearly():
    source = make_guild(1, "Source")
    target = make_guild(2, "Target")

    def fetch_members(**kwargs):
        async def gen():
            raise discord.HTTPException(MagicMock(status=403), "missing intent")
            yield  # pragma: no cover

        return gen()

    target.fetch_members = fetch_members

    cloner = ServerCloner(blueprint(), target, delay=0, source_guild=source)
    asyncio.run(extras.assign_member_roles(cloner, source))

    assert any("Members-Intent" in w for w in cloner.report.warnings)

"""
Tests für die Klon-Engine.

Der Fokus liegt auf den Fehlern, die in der ursprünglichen Skript-Version
steckten, und auf den Zusagen der neuen Funktionen: Fortsetzbarkeit, Abgleich
ohne Löschen, adaptive Taktung.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from easy_server_cloner.blueprint import (
    EVERYONE_KEY,
    KIND_CATEGORY,
    KIND_TEXT,
    KIND_VOICE,
    OverwriteData,
)
from easy_server_cloner.cloner import CloneError, ServerCloner
from easy_server_cloner.models import CloneOptions
from easy_server_cloner.state import CloneState

from helpers import blueprint, channel_bp, make_guild, make_role, role_bp


def cloner_for(bp, target, **kw):
    kw.setdefault("delay", 0)
    return ServerCloner(bp, target, **kw)


# ── Sicherheitsnetz ───────────────────────────────────────────────────────────


def test_source_equals_target_is_rejected():
    """Der teuerste Bedienfehler muss vor dem ersten Löschen greifen."""
    target = make_guild(1, "Mein Server")
    c = cloner_for(blueprint(source_id="1"), target)

    with pytest.raises(CloneError, match="identisch"):
        asyncio.run(c.run())

    target.edit.assert_not_awaited()


def test_missing_permissions_are_rejected():
    target = make_guild(2, "Ziel")
    target.me.guild_permissions = discord.Permissions.none()

    with pytest.raises(CloneError, match="fehlen"):
        asyncio.run(cloner_for(blueprint(), target).run())


def test_bot_not_in_target_is_rejected():
    target = make_guild(2, "Ziel")
    target.me = None

    with pytest.raises(CloneError, match="kein Mitglied"):
        asyncio.run(cloner_for(blueprint(), target).run())


def test_dry_run_changes_nothing():
    target = make_guild(2, "Ziel")
    bp = blueprint(roles=[role_bp("10", "Admin", 3)])

    c = cloner_for(bp, target, options=CloneOptions(dry_run=True))
    asyncio.run(c.run())

    target.edit.assert_not_awaited()
    target.create_role.assert_not_awaited()


# ── Rollen-Hierarchie ─────────────────────────────────────────────────────────


def test_roles_are_created_from_highest_to_lowest():
    """
    Discord legt jede neue Rolle ganz unten an. Wer aufsteigend kopiert, dreht
    die Hierarchie um — genau der Bug im Originalskript.
    """
    bp = blueprint(
        roles=[
            role_bp(EVERYONE_KEY, "@everyone", 0, default=True),
            role_bp("12", "Member", 1),
            role_bp("11", "Mod", 2),
            role_bp("10", "Admin", 3),
        ]
    )
    target = make_guild(2, "Ziel")

    c = cloner_for(bp, target)
    asyncio.run(c._apply_roles())

    created = [call.kwargs["name"] for call in target.create_role.await_args_list]
    assert created == ["Admin", "Mod", "Member"]
    assert "@everyone" not in created
    assert c.report.roles_created == 3


def test_everyone_permissions_are_transferred():
    perms = discord.Permissions(send_messages=True)
    bp = blueprint(roles=[role_bp(EVERYONE_KEY, "@everyone", 0, default=True, permissions=perms.value)])
    target = make_guild(2, "Ziel")

    asyncio.run(cloner_for(bp, target)._apply_roles())

    target.default_role.edit.assert_awaited_once()
    assert target.default_role.edit.await_args.kwargs["permissions"] == perms


def test_role_creation_falls_back_without_permissions():
    """Kann der Bot die Rechte nicht vergeben, entsteht trotzdem die Rolle."""
    bp = blueprint(roles=[role_bp("10", "Admin", 1)])
    target = make_guild(2, "Ziel")
    calls: list[dict] = []

    async def create_role(**kw):
        calls.append(kw)
        if "permissions" in kw:
            raise discord.Forbidden(MagicMock(status=403), "missing access")
        return make_role(50, kw["name"], 1)

    target.create_role = AsyncMock(side_effect=create_role)

    c = cloner_for(bp, target)
    asyncio.run(c._apply_roles())

    assert len(calls) == 2
    assert "permissions" not in calls[1]
    assert c.report.roles_created == 1


def test_warns_when_bot_role_is_too_low():
    bp = blueprint(roles=[role_bp(str(i), f"R{i}", i) for i in range(1, 6)])
    target = make_guild(2, "Ziel", bot_top_position=2)

    c = cloner_for(bp, target)
    asyncio.run(c._validate())

    assert any("gestaucht" in w for w in c.report.warnings)


# ── Permission-Overwrites ─────────────────────────────────────────────────────


def test_overwrites_map_roles_and_default_role():
    target = make_guild(2, "Ziel")
    c = cloner_for(blueprint(), target)

    new_mod = make_role(77, "Mod", 1)
    c.role_map[EVERYONE_KEY] = target.default_role
    c.role_map["11"] = new_mod

    deny_view = discord.Permissions(view_channel=True)
    entries = [
        OverwriteData(EVERYONE_KEY, allow=0, deny=deny_view.value),
        OverwriteData("11", allow=deny_view.value, deny=0),
        OverwriteData("99-existiert-nicht", allow=deny_view.value, deny=0),
    ]

    result = c._build_overwrites(entries)

    assert set(result) == {target.default_role, new_mod}
    assert result[target.default_role].view_channel is False
    assert result[new_mod].view_channel is True


# ── Server-Einstellungen ──────────────────────────────────────────────────────


def test_missing_icon_does_not_wipe_target_icon():
    """Im Originalskript löschte `icon=None` das Icon des Ziel-Servers."""
    target = make_guild(2, "Ziel")
    asyncio.run(cloner_for(blueprint(name="Quelle"), target)._apply_settings())

    kwargs = target.edit.await_args.kwargs
    assert "icon" not in kwargs
    assert "banner" not in kwargs
    assert kwargs["name"] == "Quelle"


def test_icon_from_blueprint_is_applied():
    import base64

    bp = blueprint(icon=base64.b64encode(b"PNGDATA").decode())
    target = make_guild(2, "Ziel")

    asyncio.run(cloner_for(bp, target)._apply_settings())

    assert target.edit.await_args.kwargs["icon"] == b"PNGDATA"


def test_settings_retry_drops_boost_only_fields():
    """Ein abgelehntes Banner darf nicht den ganzen Vorgang kippen."""
    import base64

    bp = blueprint(banner=base64.b64encode(b"BANNER").decode())
    target = make_guild(2, "Ziel")
    attempts: list[dict] = []

    async def edit(**kw):
        attempts.append(kw)
        if "banner" in kw:
            raise discord.HTTPException(MagicMock(status=400), "banner not allowed")

    target.edit = AsyncMock(side_effect=edit)

    c = cloner_for(bp, target)
    asyncio.run(c._apply_settings())

    assert len(attempts) == 2
    assert "banner" not in attempts[1]
    assert c.report.error_count == 0
    assert any("Boost" in w for w in c.report.warnings)


# ── Voice-Bitrate ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source_bitrate, target_limit, expected",
    [
        (256000, 384000, 256000),  # geboostetes Ziel: Qualität bleibt erhalten
        (256000, 64000, 64000),  # ungeboostetes Ziel: sauber gedeckelt
    ],
)
def test_bitrate_follows_target_limit(source_bitrate, target_limit, expected):
    """Fest verdrahtete 96 kbit/s waren in beide Richtungen falsch."""
    target = make_guild(2, "Ziel")
    target.bitrate_limit = target_limit
    target.create_voice_channel = AsyncMock(return_value=MagicMock())

    channel = channel_bp("5", "Talk", KIND_VOICE, bitrate=source_bitrate)
    c = cloner_for(blueprint(), target)
    asyncio.run(c._create_voice_channel(channel, {"name": "Talk"}))

    assert target.create_voice_channel.await_args.kwargs["bitrate"] == expected


# ── Community-Beschränkungen ──────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["stage", "forum"])
def test_community_channels_are_skipped_on_normal_servers(kind):
    target = make_guild(2, "Ziel", features=[])
    c = cloner_for(blueprint(), target)

    result = asyncio.run(c._create_channel(channel_bp("7", "Bühne", kind)))

    assert result is None
    assert any("Community-Server" in w for w in c.report.warnings)


# ── Fortsetzbarkeit ───────────────────────────────────────────────────────────


def test_resume_skips_objects_that_still_exist():
    bp = blueprint(
        roles=[role_bp("10", "Admin", 2), role_bp("11", "Mod", 1)],
        channels=[channel_bp("20", "allgemein", KIND_TEXT)],
    )
    target = make_guild(2, "Ziel")

    existing_role = make_role(500, "Admin", 1)
    existing_channel = MagicMock(spec=discord.TextChannel)
    existing_channel.id = 600
    target.get_role = MagicMock(side_effect=lambda rid: existing_role if rid == 500 else None)
    target.get_channel = MagicMock(side_effect=lambda cid: existing_channel if cid == 600 else None)

    state = CloneState(target_id=2, roles={"10": 500}, channels={"20": 600})
    c = cloner_for(bp, target, state=state)

    asyncio.run(c._validate())
    c._restore_maps_from_state()
    asyncio.run(c._apply_roles())

    created = [call.kwargs["name"] for call in target.create_role.await_args_list]
    assert created == ["Mod"]  # Admin war schon da
    assert c.role_map["10"] is existing_role


def test_resume_recreates_objects_that_vanished():
    """Wurde ein Objekt zwischenzeitlich gelöscht, entsteht es neu."""
    bp = blueprint(roles=[role_bp("10", "Admin", 1)])
    target = make_guild(2, "Ziel")
    target.get_role = MagicMock(return_value=None)

    state = CloneState(target_id=2, roles={"10": 500})
    c = cloner_for(bp, target, state=state)
    c._restore_maps_from_state()
    asyncio.run(c._apply_roles())

    assert [call.kwargs["name"] for call in target.create_role.await_args_list] == ["Admin"]


def test_resume_never_wipes_the_target():
    """Beim Fortsetzen wäre erneutes Leeren genau das Gegenteil des Ziels."""
    target = make_guild(2, "Ziel")
    target.channels = [MagicMock(spec=discord.TextChannel)]

    state = CloneState(target_id=2, roles={"10": 500})
    c = cloner_for(
        blueprint(), target, state=state, options=CloneOptions(wipe_target=True)
    )
    c._wipe_target = AsyncMock()

    asyncio.run(c.run())

    c._wipe_target.assert_not_awaited()


def test_state_is_saved_on_cancel_and_cleared_on_success():
    bp = blueprint(roles=[role_bp(str(i), f"R{i}", i) for i in range(1, 4)])
    target = make_guild(2, "Ziel")

    c = ServerCloner(bp, target, delay=0.01)
    original = target.create_role

    async def create_and_cancel(**kw):
        c.cancel()
        return await original(**kw)

    target.create_role = AsyncMock(side_effect=create_and_cancel)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c.run())

    assert CloneState.path_for(2).exists()
    assert CloneState.load(2) is not None

    # Ein sauberer Durchlauf räumt den Zwischenstand wieder ab.
    target2 = make_guild(2, "Ziel")
    asyncio.run(ServerCloner(blueprint(), target2, delay=0).run())
    assert not CloneState.path_for(2).exists()


# ── /sync: nichts löschen, Vorhandenes wiederverwenden ────────────────────────


def test_skip_existing_reuses_channels_by_name():
    bp = blueprint(channels=[channel_bp("20", "allgemein", KIND_TEXT)])
    target = make_guild(2, "Ziel")

    existing = MagicMock(spec=discord.TextChannel)
    existing.id = 600
    existing.name = "allgemein"
    target.channels = [existing]
    target.create_text_channel = AsyncMock()

    c = cloner_for(bp, target, options=CloneOptions(skip_existing=True))
    asyncio.run(c._apply_channels())

    target.create_text_channel.assert_not_awaited()
    assert c.channel_map["20"] is existing


def test_skip_existing_reuses_categories_by_name():
    bp = blueprint(channels=[channel_bp("30", "INFO", KIND_CATEGORY)])
    target = make_guild(2, "Ziel")

    existing = MagicMock(spec=discord.CategoryChannel)
    existing.id = 700
    existing.name = "INFO"
    target.categories = [existing]
    target.create_category = AsyncMock()

    c = cloner_for(bp, target, options=CloneOptions(skip_existing=True))
    asyncio.run(c._apply_categories())

    target.create_category.assert_not_awaited()
    assert c.channel_map["30"] is existing


# ── Abbruch ───────────────────────────────────────────────────────────────────


def test_cancel_stops_the_run():
    bp = blueprint(roles=[role_bp(str(i), f"R{i}", i) for i in range(1, 6)])
    target = make_guild(2, "Ziel")

    c = ServerCloner(bp, target, delay=0.01)
    original = target.create_role

    async def create_and_cancel(**kw):
        c.cancel()
        return await original(**kw)

    target.create_role = AsyncMock(side_effect=create_and_cancel)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c.run())

    assert c.cancelled
    assert c.report.roles_created < 5


# ── Extras ohne Quell-Server ──────────────────────────────────────────────────


def test_extras_warn_when_restoring_from_a_file():
    """Aus einer Datei gibt es keine Nachrichten und keine Mitglieder."""
    target = make_guild(2, "Ziel")
    c = cloner_for(
        blueprint(),
        target,
        options=CloneOptions(messages_per_channel=10, member_roles=True),
    )
    asyncio.run(c._apply_extras())

    assert any("Quell-Server" in w for w in c.report.warnings)

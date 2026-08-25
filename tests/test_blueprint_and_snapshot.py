"""Tests for the blueprint, backup files, checkpoints and adaptive pacing."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from easy_server_cloner import snapshot
from easy_server_cloner.blueprint import (
    EVERYONE_KEY,
    KIND_CATEGORY,
    KIND_FORUM,
    KIND_NEWS,
    KIND_TEXT,
    KIND_VOICE,
    ChannelBlueprint,
    GuildBlueprint,
    OverwriteData,
)
from easy_server_cloner.pacing import MAX_DELAY, MIN_DELAY, AdaptivePacer
from easy_server_cloner.state import CloneState

from helpers import blueprint, channel_bp, make_guild, make_role, role_bp


# ── Bauplan: Reihenfolgen ─────────────────────────────────────────────────────


def test_creatable_roles_are_sorted_high_to_low():
    bp = blueprint(
        roles=[
            role_bp(EVERYONE_KEY, "@everyone", 0, default=True),
            role_bp("a", "Niedrig", 1),
            role_bp("b", "Hoch", 9),
            role_bp("c", "Mitte", 5),
        ]
    )
    assert [r.name for r in bp.creatable_roles] == ["Hoch", "Mitte", "Niedrig"]
    assert bp.default_role is not None and bp.default_role.name == "@everyone"


def test_channels_are_grouped_by_category_order():
    """Channels must come category by category, in category order."""
    bp = blueprint(
        channels=[
            channel_bp("cat-b", "ZWEITE", KIND_CATEGORY, position=1),
            channel_bp("cat-a", "ERSTE", KIND_CATEGORY, position=0),
            channel_bp("2", "b-zwei", KIND_TEXT, position=1, category_key="cat-b"),
            channel_bp("1", "b-eins", KIND_TEXT, position=0, category_key="cat-b"),
            channel_bp("0", "a-eins", KIND_TEXT, position=0, category_key="cat-a"),
            channel_bp("9", "no-category", KIND_TEXT, position=0),
        ]
    )

    assert [c.name for c in bp.categories] == ["ERSTE", "ZWEITE"]
    assert [c.name for c in bp.non_categories] == [
        "no-category",  # uncategorised first, as in Discord
        "a-eins",
        "b-eins",
        "b-zwei",
    ]


def test_counts_and_totals():
    bp = blueprint(
        roles=[role_bp(EVERYONE_KEY, "@everyone", 0, default=True), role_bp("r", "Mod", 1)],
        channels=[
            channel_bp("c", "KAT", KIND_CATEGORY),
            channel_bp("t", "text", KIND_TEXT),
            channel_bp("n", "news", KIND_NEWS),
            channel_bp("v", "voice", KIND_VOICE),
        ],
    )
    counts = bp.counts()
    assert counts["Roles"] == 1  # @everyone does not count as a role to create
    assert counts["Kategorien"] == 1
    assert counts["Text-Channels"] == 2  # announcement channels count too
    assert counts["Voice-Channels"] == 1
    assert bp.total_objects == sum(counts.values())


# ── Bauplan: aus einem Live-Server lesen ──────────────────────────────────────


def test_from_guild_skips_managed_roles_and_member_overwrites():
    everyone = make_role(1, "@everyone", 0, default=True)
    mod = make_role(11, "Mod", 2)
    bot_role = make_role(12, "BotRolle", 3, managed=True)
    guild = make_guild(1, "Quelle", roles=[everyone, mod, bot_role])

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 20
    channel.name = "allgemein"
    channel.position = 0
    channel.category_id = None
    channel.topic = "Hallo"
    channel.nsfw = False
    channel.slowmode_delay = 0
    channel.default_auto_archive_duration = 1440
    channel.is_news.return_value = False
    member = MagicMock(spec=discord.Member)
    channel.overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        mod: discord.PermissionOverwrite(view_channel=True),
        member: discord.PermissionOverwrite(view_channel=True),
    }
    guild.channels = [channel]

    bp = asyncio.run(GuildBlueprint.from_guild(guild, include_images=False))

    assert [r.name for r in bp.roles] == ["@everyone", "Mod"]  # the bot role is absent
    keys = {o.role_key for o in bp.channels[0].overwrites}
    assert keys == {EVERYONE_KEY, "11"}  # Nutzer-Override fehlt


def test_from_guild_without_images_leaves_assets_empty():
    guild = make_guild(1, "Quelle")
    asset = MagicMock(spec=discord.Asset)
    asset.read = AsyncMock(return_value=b"ICON")
    guild.icon = asset

    bp = asyncio.run(GuildBlueprint.from_guild(guild, include_images=False))

    assert bp.icon is None
    assert bp.has_images is False
    asset.read.assert_not_awaited()


# ── Bauplan: Serialisierung ───────────────────────────────────────────────────


def test_round_trip_preserves_everything():
    original = blueprint(
        name="Mein Server",
        roles=[
            role_bp(EVERYONE_KEY, "@everyone", 0, default=True, permissions=2048),
            role_bp("10", "Admin", 5, permissions=8),
        ],
        channels=[
            channel_bp("c", "KAT", KIND_CATEGORY),
            channel_bp(
                "t",
                "allgemein",
                KIND_TEXT,
                category_key="c",
                topic="Thema",
                nsfw=True,
                slowmode_delay=30,
                overwrites=[OverwriteData(EVERYONE_KEY, allow=0, deny=1024)],
            ),
            channel_bp("v", "Talk", KIND_VOICE, bitrate=128000, user_limit=5),
        ],
        icon="aGVsbG8=",
    )

    restored = GuildBlueprint.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored.to_dict() == original.to_dict()
    assert restored.channels[1].topic == "Thema"
    assert restored.channels[1].overwrites[0].deny == 1024
    assert restored.channels[2].bitrate == 128000


def test_forum_tags_survive_the_round_trip():
    from easy_server_cloner.blueprint import ForumTagData

    original = blueprint(
        channels=[
            channel_bp(
                "f",
                "hilfe",
                KIND_FORUM,
                available_tags=[ForumTagData(name="Solved", emoji="✅", moderated=True)],
                default_sort_order=1,
            )
        ]
    )
    restored = GuildBlueprint.from_dict(json.loads(json.dumps(original.to_dict())))
    tag = restored.channels[0].available_tags[0]

    assert (tag.name, tag.emoji, tag.moderated) == ("Solved", "✅", True)
    assert restored.channels[0].default_sort_order == 1


def test_newer_format_version_is_refused():
    data = blueprint().to_dict()
    data["format_version"] = 999

    with pytest.raises(ValueError, match="Format-Version"):
        GuildBlueprint.from_dict(data)


def test_unknown_extra_keys_are_ignored():
    """A backup from a newer minor version must not fail hard."""
    data = blueprint().to_dict()
    data["ein_feld_das_es_noch_nicht_gibt"] = 42

    assert GuildBlueprint.from_dict(data).name == "Quelle"


# ── Backup-Dateien ────────────────────────────────────────────────────────────


def test_small_backup_stays_plain_json():
    shot = snapshot.dump(blueprint(name="Kleiner Server"))

    assert not shot.compressed
    assert shot.filename.endswith(".esc.json")
    assert json.loads(shot.data)["name"] == "Kleiner Server"


def test_large_backup_is_compressed():
    """Images make backups large — gzip keeps them under Discord's limit."""
    big = blueprint(name="Large", icon="A" * (2 * 1024 * 1024))
    shot = snapshot.dump(big)

    assert shot.compressed
    assert shot.filename.endswith(".json.gz")
    assert shot.size < len(json.dumps(big.to_dict()))


@pytest.mark.parametrize("compress", [False, True])
def test_load_handles_both_formats(compress):
    raw = json.dumps(blueprint(name="Rund").to_dict()).encode()
    data = gzip.compress(raw) if compress else raw

    assert snapshot.load(data).name == "Rund"


def test_load_rejects_garbage():
    with pytest.raises(snapshot.SnapshotError):
        snapshot.load(b"this is not a backup")
    with pytest.raises(snapshot.SnapshotError):
        snapshot.load(b'["a list, not an object"]')
    with pytest.raises(snapshot.SnapshotError, match="damaged or incomplete"):
        snapshot.load(b"\x1f\x8b" + b"kaputt")


def test_filenames_are_safe():
    shot = snapshot.dump(blueprint(name="My / Server: <nasty>  characters"))

    assert "/" not in shot.filename
    assert ":" not in shot.filename
    assert "<" not in shot.filename
    assert shot.filename.startswith("My-Server-")


def test_save_to_disk_writes_the_file(isolated_state):
    shot = snapshot.dump(blueprint())
    path = snapshot.save_to_disk(shot)

    assert path is not None and path.exists()
    assert path.read_bytes() == shot.data


def test_human_size():
    assert snapshot.human_size(512) == "512 B"
    assert snapshot.human_size(2048) == "2.0 KB"
    assert snapshot.human_size(5 * 1024 * 1024) == "5.0 MB"


# ── Checkpoints ───────────────────────────────────────────────────────────────


def test_state_round_trip():
    state = CloneState(target_id=42, source_name="Quelle", roles={"a": 1}, channels={"b": 2})
    state.save()

    loaded = CloneState.load(42)
    assert loaded is not None
    assert loaded.roles == {"a": 1}
    assert loaded.channels == {"b": 2}
    assert loaded.object_count == 2


def test_state_is_none_when_missing():
    assert CloneState.load(999999) is None


def test_stale_state_is_discarded():
    state = CloneState(target_id=7, roles={"a": 1})
    state.save()
    # Backdate by two days.
    path = CloneState.path_for(7)
    data = json.loads(path.read_text())
    data["updated_at"] = time.time() - 48 * 3600
    path.write_text(json.dumps(data))

    assert CloneState.load(7) is None
    assert not path.exists()


def test_corrupt_state_does_not_crash():
    CloneState.path_for(8).parent.mkdir(parents=True, exist_ok=True)
    CloneState.path_for(8).write_text("{kaputt")

    assert CloneState.load(8) is None


def test_clear_removes_the_file():
    state = CloneState(target_id=9, roles={"a": 1})
    state.save()
    assert CloneState.path_for(9).exists()

    state.clear()
    assert not CloneState.path_for(9).exists()


# ── Adaptive Taktung ──────────────────────────────────────────────────────────


def test_pacer_backs_off_when_calls_get_slow():
    pacer = AdaptivePacer(0.5)
    start = pacer.delay

    for _ in range(3):
        pacer.observe(2.0)  # Discord bremst

    assert pacer.delay > start
    assert pacer.was_throttled
    assert pacer.throttle_events == 3


def test_pacer_recovers_after_a_calm_streak():
    pacer = AdaptivePacer(0.5)
    pacer.observe(2.0)
    throttled = pacer.delay

    for _ in range(30):
        pacer.observe(0.05)

    assert pacer.delay < throttled
    assert pacer.delay >= pacer.base_delay


def test_pacer_never_leaves_its_bounds():
    fast = AdaptivePacer(0.0)
    assert fast.delay >= MIN_DELAY

    slow = AdaptivePacer(1.0)
    for _ in range(50):
        slow.observe(5.0)
    assert slow.delay <= MAX_DELAY


def test_pacer_never_drops_below_base_delay():
    pacer = AdaptivePacer(0.8)
    for _ in range(100):
        pacer.observe(0.01)

    assert pacer.delay == pytest.approx(0.8)


def test_pacer_wait_reacts_to_cancel_immediately():
    async def scenario():
        cancel = asyncio.Event()
        pacer = AdaptivePacer(10.0, cancel_event=cancel)
        cancel.set()
        started = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await pacer.wait()
        # Do not wait the full 10 seconds.
        assert time.monotonic() - started < 1.0

    asyncio.run(scenario())


def test_pacer_call_measures_the_coroutine():
    async def scenario():
        pacer = AdaptivePacer(0.25)

        async def slow():
            await asyncio.sleep(0.01)
            return "fertig"

        assert await pacer.call(slow()) == "fertig"
        assert pacer.calls == 1

    asyncio.run(scenario())

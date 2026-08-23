"""
Der Bauplan eines Servers — unabhängig davon, woher er stammt.

Ein `GuildBlueprint` beschreibt vollständig, wie ein Server aussieht: Rollen,
Kategorien, Channels, Rechte, Emojis, Sticker. Er lässt sich aus einem
Live-Server auslesen (`from_guild`) *oder* aus einer JSON-Datei laden
(`from_dict`).

Dadurch benutzen `/clone`, `/backup` und `/restore` dieselbe Engine: Klonen ist
"Bauplan aus Server A lesen und auf Server B anwenden", Restore ist "Bauplan aus
Datei lesen und auf Server B anwenden".
"""

from __future__ import annotations

import base64
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import discord

log = logging.getLogger(__name__)

# Dateiformat-Version. Wird beim Laden geprüft, damit alte Backups nicht
# stillschweigend falsch interpretiert werden.
FORMAT_VERSION = 1

# Schlüssel der @everyone-Rolle. Sie hat auf jedem Server eine andere ID,
# deshalb bekommt sie im Bauplan einen festen, sprechenden Schlüssel.
EVERYONE_KEY = "@everyone"

# Channel-Typen, die der Bauplan kennt.
KIND_CATEGORY = "category"
KIND_TEXT = "text"
KIND_NEWS = "news"
KIND_VOICE = "voice"
KIND_STAGE = "stage"
KIND_FORUM = "forum"

TEXT_KINDS = (KIND_TEXT, KIND_NEWS)
COMMUNITY_ONLY_KINDS = (KIND_STAGE, KIND_FORUM)


def _b64(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode("ascii") if data else None


def decode_image(text: str | None) -> bytes | None:
    """Wandelt ein base64-Feld des Bauplans zurück in Rohbytes."""
    return base64.b64decode(text) if text else None


# Kurzform für den internen Gebrauch in diesem Modul.
_unb64 = decode_image


async def _read_asset(asset: discord.Asset | None) -> str | None:
    """Lädt ein Discord-Asset als base64 — Fehler sind hier nie fatal."""
    if asset is None:
        return None
    try:
        return _b64(await asset.read())
    except discord.HTTPException as exc:
        log.warning("Asset nicht lesbar: %s", exc)
        return None


# ── Bausteine ─────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class OverwriteData:
    """Ein Berechtigungs-Override für eine Rolle."""

    role_key: str
    allow: int
    deny: int

    @classmethod
    def from_pair(cls, role_key: str, overwrite: discord.PermissionOverwrite) -> "OverwriteData":
        allow, deny = overwrite.pair()
        return cls(role_key=role_key, allow=allow.value, deny=deny.value)

    def to_overwrite(self) -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite.from_pair(
            discord.Permissions(self.allow), discord.Permissions(self.deny)
        )


@dataclass(slots=True)
class RoleBlueprint:
    key: str
    name: str
    permissions: int
    colour: int
    hoist: bool
    mentionable: bool
    position: int
    is_default: bool = False
    icon: str | None = None
    unicode_emoji: str | None = None

    @classmethod
    async def from_role(cls, role: discord.Role, *, include_images: bool = True) -> "RoleBlueprint":
        icon: str | None = None
        unicode_emoji: str | None = None
        display = role.display_icon
        if isinstance(display, str):
            unicode_emoji = display
        elif display is not None and include_images:
            icon = await _read_asset(display)

        return cls(
            key=EVERYONE_KEY if role.is_default() else str(role.id),
            name=role.name,
            permissions=role.permissions.value,
            colour=role.colour.value,
            hoist=role.hoist,
            mentionable=role.mentionable,
            position=role.position,
            is_default=role.is_default(),
            icon=icon,
            unicode_emoji=unicode_emoji,
        )

    @property
    def icon_bytes(self) -> bytes | None:
        return _unb64(self.icon)


@dataclass(slots=True)
class ForumTagData:
    name: str
    emoji: str | None = None
    moderated: bool = False


@dataclass(slots=True)
class ChannelBlueprint:
    """Ein Channel jeder Bauart — nicht zutreffende Felder bleiben leer."""

    key: str
    name: str
    kind: str
    position: int
    category_key: str | None = None
    overwrites: list[OverwriteData] = field(default_factory=list)

    # Text / Forum
    topic: str | None = None
    nsfw: bool = False
    slowmode_delay: int = 0
    default_auto_archive_duration: int = 1440
    # Voice / Stage
    bitrate: int = 64000
    user_limit: int = 0
    rtc_region: str | None = None
    video_quality_mode: int = 1
    # Forum
    available_tags: list[ForumTagData] = field(default_factory=list)
    default_layout: int = 0
    default_sort_order: int | None = None

    @classmethod
    def from_channel(cls, channel: discord.abc.GuildChannel) -> "ChannelBlueprint | None":
        overwrites = [
            OverwriteData.from_pair(
                EVERYONE_KEY if entity.is_default() else str(entity.id), overwrite
            )
            for entity, overwrite in channel.overwrites.items()
            # Rechte einzelner Mitglieder ergeben auf einem anderen Server
            # keinen Sinn — dort existieren diese Nutzer nicht zwingend.
            if isinstance(entity, discord.Role)
        ]
        common = {
            "key": str(channel.id),
            "name": channel.name,
            "position": channel.position,
            "category_key": str(channel.category_id) if channel.category_id else None,
            "overwrites": overwrites,
        }

        if isinstance(channel, discord.CategoryChannel):
            common["category_key"] = None  # eine Kategorie hat selbst keine Kategorie
            return cls(kind=KIND_CATEGORY, **common)
        if isinstance(channel, discord.TextChannel):
            return cls(
                kind=KIND_NEWS if channel.is_news() else KIND_TEXT,
                topic=channel.topic,
                nsfw=channel.nsfw,
                slowmode_delay=channel.slowmode_delay,
                default_auto_archive_duration=channel.default_auto_archive_duration,
                **common,
            )
        if isinstance(channel, discord.VoiceChannel):
            return cls(
                kind=KIND_VOICE,
                bitrate=channel.bitrate,
                user_limit=channel.user_limit,
                rtc_region=channel.rtc_region,
                video_quality_mode=channel.video_quality_mode.value,
                **common,
            )
        if isinstance(channel, discord.StageChannel):
            return cls(
                kind=KIND_STAGE,
                bitrate=channel.bitrate,
                user_limit=channel.user_limit,
                rtc_region=channel.rtc_region,
                **common,
            )
        if isinstance(channel, discord.ForumChannel):
            return cls(
                kind=KIND_FORUM,
                topic=channel.topic,
                nsfw=channel.nsfw,
                slowmode_delay=channel.slowmode_delay,
                default_auto_archive_duration=channel.default_auto_archive_duration,
                available_tags=[
                    ForumTagData(
                        name=tag.name,
                        emoji=str(tag.emoji) if tag.emoji else None,
                        moderated=tag.moderated,
                    )
                    for tag in channel.available_tags
                ],
                default_layout=channel.default_layout.value,
                default_sort_order=(
                    channel.default_sort_order.value
                    if channel.default_sort_order is not None
                    else None
                ),
                **common,
            )
        return None

    @property
    def is_text_like(self) -> bool:
        return self.kind in TEXT_KINDS

    @property
    def needs_community(self) -> bool:
        return self.kind in COMMUNITY_ONLY_KINDS


@dataclass(slots=True)
class EmojiBlueprint:
    name: str
    image: str | None
    animated: bool = False

    @property
    def image_bytes(self) -> bytes | None:
        return _unb64(self.image)


@dataclass(slots=True)
class StickerBlueprint:
    name: str
    description: str
    emoji: str
    image: str | None
    file_format: str = "png"

    @property
    def image_bytes(self) -> bytes | None:
        return _unb64(self.image)


# ── Der komplette Bauplan ─────────────────────────────────────────────────────


@dataclass(slots=True)
class GuildBlueprint:
    name: str
    roles: list[RoleBlueprint] = field(default_factory=list)
    channels: list[ChannelBlueprint] = field(default_factory=list)
    emojis: list[EmojiBlueprint] = field(default_factory=list)
    stickers: list[StickerBlueprint] = field(default_factory=list)

    description: str | None = None
    icon: str | None = None
    banner: str | None = None
    splash: str | None = None
    verification_level: int = 0
    default_notifications: int = 0
    explicit_content_filter: int = 0
    afk_timeout: int = 300
    afk_channel_key: str | None = None
    system_channel_key: str | None = None
    system_channel_flags: int = 0

    # Herkunftsangaben — rein informativ, für Anzeige und Fehlersuche.
    source_id: str | None = None
    source_name: str | None = None
    created_at: str = ""
    format_version: int = FORMAT_VERSION
    has_images: bool = True

    # ── Auslesen ──────────────────────────────────────────────────────────────

    @classmethod
    async def from_guild(
        cls, guild: discord.Guild, *, include_images: bool = True
    ) -> "GuildBlueprint":
        """Liest einen Live-Server vollständig aus."""
        roles = [
            await RoleBlueprint.from_role(role, include_images=include_images)
            for role in guild.roles
            # Von Integrationen verwaltete Rollen (Bot-Rollen, Booster-Rolle)
            # lassen sich nicht nachbauen — sie entstehen mit dem jeweiligen Bot.
            if not role.managed
        ]

        channels: list[ChannelBlueprint] = []
        for channel in guild.channels:
            blueprint = ChannelBlueprint.from_channel(channel)
            if blueprint is not None:
                channels.append(blueprint)

        emojis: list[EmojiBlueprint] = []
        stickers: list[StickerBlueprint] = []
        if include_images:
            for emoji in guild.emojis:
                emojis.append(
                    EmojiBlueprint(
                        name=emoji.name,
                        image=_b64(await _safe_read(emoji)),
                        animated=emoji.animated,
                    )
                )
            for sticker in guild.stickers:
                stickers.append(
                    StickerBlueprint(
                        name=sticker.name,
                        description=sticker.description or "",
                        emoji=sticker.emoji or "grinning",
                        image=_b64(await _safe_read(sticker)),
                        file_format=sticker.format.name,
                    )
                )

        return cls(
            name=guild.name,
            roles=roles,
            channels=channels,
            emojis=emojis,
            stickers=stickers,
            description=guild.description,
            icon=await _read_asset(guild.icon) if include_images else None,
            banner=await _read_asset(guild.banner) if include_images else None,
            splash=await _read_asset(guild.splash) if include_images else None,
            verification_level=guild.verification_level.value,
            default_notifications=guild.default_notifications.value,
            explicit_content_filter=guild.explicit_content_filter.value,
            afk_timeout=guild.afk_timeout,
            afk_channel_key=str(guild.afk_channel.id) if guild.afk_channel else None,
            system_channel_key=str(guild.system_channel.id) if guild.system_channel else None,
            system_channel_flags=guild.system_channel_flags.value,
            source_id=str(guild.id),
            source_name=guild.name,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            has_images=include_images,
        )

    # ── Serialisierung ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuildBlueprint":
        version = data.get("format_version", 0)
        if version > FORMAT_VERSION:
            raise ValueError(
                f"Diese Backup-Datei hat Format-Version {version}, dieser Bot kennt "
                f"nur bis {FORMAT_VERSION}. Aktualisiere den Bot."
            )
        if "name" not in data:
            raise ValueError("Die Datei enthält keinen Server-Namen — kein gültiges Backup.")

        def build_channel(raw: dict[str, Any]) -> ChannelBlueprint:
            raw = dict(raw)
            raw["overwrites"] = [OverwriteData(**o) for o in raw.get("overwrites", [])]
            raw["available_tags"] = [ForumTagData(**t) for t in raw.get("available_tags", [])]
            return ChannelBlueprint(**raw)

        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload["roles"] = [RoleBlueprint(**r) for r in data.get("roles", [])]
        payload["channels"] = [build_channel(c) for c in data.get("channels", [])]
        payload["emojis"] = [EmojiBlueprint(**e) for e in data.get("emojis", [])]
        payload["stickers"] = [StickerBlueprint(**s) for s in data.get("stickers", [])]
        return cls(**payload)

    # ── Abfragen ──────────────────────────────────────────────────────────────

    @property
    def default_role(self) -> RoleBlueprint | None:
        return next((r for r in self.roles if r.is_default), None)

    @property
    def creatable_roles(self) -> list[RoleBlueprint]:
        """Alle Rollen außer @everyone, von der höchsten zur niedrigsten.

        Die Reihenfolge ist entscheidend: Discord legt jede neue Rolle ganz
        unten an. Wer aufsteigend erstellt, dreht die Hierarchie um.
        """
        return sorted(
            (r for r in self.roles if not r.is_default),
            key=lambda r: r.position,
            reverse=True,
        )

    @property
    def categories(self) -> list[ChannelBlueprint]:
        return sorted(
            (c for c in self.channels if c.kind == KIND_CATEGORY), key=lambda c: c.position
        )

    @property
    def non_categories(self) -> list[ChannelBlueprint]:
        """Channels in der Reihenfolge, in der sie angelegt werden sollen."""
        by_key = {c.key: c for c in self.channels}

        def sort_key(channel: ChannelBlueprint) -> tuple[int, int]:
            parent = by_key.get(channel.category_key or "")
            return (parent.position if parent else -1, channel.position)

        return sorted(
            (c for c in self.channels if c.kind != KIND_CATEGORY), key=sort_key
        )

    def counts(self) -> dict[str, int]:
        kinds = [c.kind for c in self.channels]
        return {
            "Rollen": len(self.creatable_roles),
            "Kategorien": kinds.count(KIND_CATEGORY),
            "Text-Channels": kinds.count(KIND_TEXT) + kinds.count(KIND_NEWS),
            "Voice-Channels": kinds.count(KIND_VOICE),
            "Bühnen-Channels": kinds.count(KIND_STAGE),
            "Foren": kinds.count(KIND_FORUM),
            "Emojis": len(self.emojis),
            "Sticker": len(self.stickers),
        }

    @property
    def total_objects(self) -> int:
        return sum(self.counts().values())


async def _safe_read(obj: Any) -> bytes | None:
    try:
        return await obj.read()
    except (discord.HTTPException, discord.NotFound) as exc:
        log.warning("Konnte %s nicht laden: %s", getattr(obj, "name", obj), exc)
        return None

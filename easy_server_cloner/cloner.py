"""
Die eigentliche Klon-Logik.

Kopiert Rollen, Kategorien, Channels, Emojis, Sticker und Server-Einstellungen
von einem Quell- auf einen Ziel-Server. Die Klasse kennt Discord, aber weder
Slash-Commands noch Embeds — die Anbindung passiert über einen
Progress-Callback, damit sich dieselbe Engine auch per CLI ansteuern lässt.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Awaitable, Callable, Iterable

import discord

from .models import CloneOptions, CloneReport, Progress, Stage

log = logging.getLogger(__name__)

ProgressCallback = Callable[[Progress], Awaitable[None]]

# Permissions, ohne die ein Klon garantiert scheitert.
REQUIRED_PERMISSIONS = ("manage_roles", "manage_channels", "manage_guild")

# Overwrites für Mitglieder werden nicht übertragen: das Zielserver-Mitglied
# existiert im Zweifel gar nicht. Wir sammeln stattdessen eine Warnung.
_MEMBER_OVERWRITE_HINT = (
    "Nutzer-spezifische Channel-Rechte wurden übersprungen "
    "(nur Rollen-Rechte lassen sich sinnvoll übertragen)."
)


class CloneError(RuntimeError):
    """Abbruchbedingung, die dem Nutzer direkt angezeigt werden kann."""


class ServerCloner:
    """Klont einen Guild auf einen anderen."""

    def __init__(
        self,
        source: discord.Guild,
        target: discord.Guild,
        options: CloneOptions | None = None,
        *,
        delay: float = 0.6,
        on_progress: ProgressCallback | None = None,
        reason: str = "Easy Server Cloner",
    ) -> None:
        self.source = source
        self.target = target
        self.options = options or CloneOptions()
        self.delay = delay
        self.reason = reason
        self._on_progress = on_progress
        self._cancel = asyncio.Event()

        self.progress = Progress()
        self.report = self.progress.report

        # source_role_id → neue Rolle im Ziel-Server
        self.role_map: dict[int, discord.Role] = {}
        # source_category_id → neue Kategorie im Ziel-Server
        self.category_map: dict[int, discord.CategoryChannel] = {}
        # source_channel_id → neuer Channel im Ziel-Server
        self.channel_map: dict[int, discord.abc.GuildChannel] = {}

        self._member_overwrite_warned = False

    # ── Steuerung ─────────────────────────────────────────────────────────────

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Hängt die Fortschrittsanzeige ein (z. B. erst nach dem Absenden
        der Status-Nachricht, deren Objekt der Callback braucht)."""
        self._on_progress = callback

    def cancel(self) -> None:
        """Bricht den laufenden Vorgang beim nächsten Zwischenschritt ab."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise asyncio.CancelledError

    async def _tick(self) -> None:
        """Wartet die konfigurierte Pause ab und reagiert auf Abbrüche."""
        self._check_cancel()
        if self.delay > 0:
            try:
                # Ein Abbruch wirkt so sofort statt erst nach der Pause.
                await asyncio.wait_for(self._cancel.wait(), timeout=self.delay)
            except asyncio.TimeoutError:
                return
            raise asyncio.CancelledError

    async def _emit(
        self,
        stage: Stage | None = None,
        detail: str = "",
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if stage is not None:
            self.progress.stage = stage
        self.progress.detail = detail
        if current is not None:
            self.progress.current = current
        if total is not None:
            self.progress.total = total
        if self._on_progress is not None:
            try:
                await self._on_progress(self.progress)
            except Exception:  # Anzeige darf den Klon niemals stoppen
                log.exception("Progress-Callback ist fehlgeschlagen")

    # ── Einstiegspunkt ────────────────────────────────────────────────────────

    async def run(self) -> CloneReport:
        """Führt den kompletten Klon-Vorgang aus."""
        try:
            await self._validate()

            if self.options.dry_run:
                self.progress.stage = Stage.DONE
                return self.report

            if self.options.wipe_target:
                await self._wipe_target()
            if self.options.settings or self.options.icon:
                await self._clone_settings()
            if self.options.roles:
                await self._clone_roles()
            if self.options.channels:
                await self._clone_categories()
                await self._clone_channels()
                await self._finalize_guild_channels()
            if self.options.emojis:
                await self._clone_emojis()
            if self.options.stickers:
                await self._clone_stickers()

            await self._emit(Stage.DONE, "Fertig")
        except asyncio.CancelledError:
            self.progress.stage = Stage.CANCELLED
            self.report.warn("Vorgang wurde vom Nutzer abgebrochen.")
            raise
        except CloneError as exc:
            self.progress.stage = Stage.FAILED
            self.report.fail(str(exc))
            raise
        except Exception as exc:  # pragma: no cover - Sicherheitsnetz
            log.exception("Unerwarteter Fehler beim Klonen")
            self.progress.stage = Stage.FAILED
            self.report.fail(f"Unerwarteter Fehler: {exc}")
            raise CloneError(str(exc)) from exc
        finally:
            self.report.finished_at = time.monotonic()

        return self.report

    # ── 0. Vorabprüfungen ─────────────────────────────────────────────────────

    async def _validate(self) -> None:
        await self._emit(Stage.VALIDATING, "Prüfe Server und Berechtigungen")

        # Der mit Abstand teuerste Fehler: sich selbst überschreiben.
        if self.source.id == self.target.id:
            raise CloneError(
                "Quell- und Ziel-Server sind identisch. Das würde den Server "
                "löschen statt ihn zu kopieren — Vorgang abgebrochen."
            )

        me = self.target.me
        if me is None:
            raise CloneError(
                "Der Bot ist kein Mitglied des Ziel-Servers. Lade ihn zuerst ein."
            )

        perms = me.guild_permissions
        if not perms.administrator:
            missing = [p for p in REQUIRED_PERMISSIONS if not getattr(perms, p)]
            if missing:
                pretty = ", ".join(p.replace("_", " ").title() for p in missing)
                raise CloneError(
                    f"Dem Bot fehlen auf dem Ziel-Server folgende Rechte: {pretty}.\n"
                    f"Am einfachsten gibst du ihm die Rolle „Administrator“."
                )

        # Rollen und Channels lassen sich nur unterhalb der eigenen Rolle anlegen.
        if me.top_role.position <= 1 and not self.target.owner_id == me.id:
            self.report.warn(
                "Die Bot-Rolle steht ganz unten in der Rangliste. Kopierte Rollen "
                "landen dadurch alle darunter. Schiebe die Bot-Rolle nach oben, "
                "damit die Hierarchie erhalten bleibt."
            )

        source_roles = [r for r in self.source.roles if not r.is_default()]
        if len(source_roles) > 0 and self.options.roles:
            highest_copyable = me.top_role.position - 1
            if highest_copyable < len(source_roles):
                self.report.warn(
                    f"Der Quell-Server hat {len(source_roles)} Rollen, oberhalb der "
                    f"Bot-Rolle ist aber nur Platz für {max(highest_copyable, 0)}. "
                    f"Die Rangfolge wird beim Kopieren gestaucht."
                )

        await self._emit(detail="Voraussetzungen erfüllt")

    # ── 1. Ziel-Server leeren ─────────────────────────────────────────────────

    async def _wipe_target(self) -> None:
        channels = list(self.target.channels)
        roles = [
            r
            for r in self.target.roles
            if not r.is_default() and not r.managed and r < self.target.me.top_role
        ]
        total = len(channels) + len(roles)
        await self._emit(Stage.WIPING, "Lösche alte Inhalte", 0, total)

        done = 0
        for channel in channels:
            try:
                await channel.delete(reason=self.reason)
                self.report.channels_deleted += 1
            except discord.Forbidden:
                self.report.warn(f"Keine Rechte zum Löschen von #{channel.name}.")
            except discord.HTTPException as exc:
                self.report.warn(f"#{channel.name} nicht gelöscht: {exc.text or exc}")
            done += 1
            await self._emit(detail=f"#{channel.name}", current=done)
            await self._tick()

        for role in roles:
            try:
                await role.delete(reason=self.reason)
                self.report.roles_deleted += 1
            except discord.Forbidden:
                self.report.warn(f"Keine Rechte zum Löschen der Rolle @{role.name}.")
            except discord.HTTPException as exc:
                self.report.warn(f"@{role.name} nicht gelöscht: {exc.text or exc}")
            done += 1
            await self._emit(detail=f"@{role.name}", current=done)
            await self._tick()

    # ── 2. Server-Einstellungen ───────────────────────────────────────────────

    async def _clone_settings(self) -> None:
        await self._emit(Stage.SETTINGS, "Name, Icon und Grundeinstellungen", 0, 1)

        payload: dict[str, object] = {}

        if self.options.settings:
            payload.update(
                name=self.source.name,
                verification_level=self.source.verification_level,
                default_notifications=self.source.default_notifications,
                explicit_content_filter=self.source.explicit_content_filter,
                afk_timeout=self.source.afk_timeout,
            )
            if self.source.description is not None and "COMMUNITY" in self.target.features:
                payload["description"] = self.source.description

        if self.options.icon:
            # Wichtig: Assets nur setzen, wenn die Quelle wirklich eines hat.
            # Ein `icon=None` würde das Ziel-Icon sonst aktiv löschen.
            for attr, asset in (
                ("icon", self.source.icon),
                ("banner", self.source.banner),
                ("splash", self.source.splash),
            ):
                if asset is None:
                    continue
                try:
                    payload[attr] = await asset.read()
                except discord.HTTPException as exc:
                    self.report.warn(f"{attr.title()} nicht ladbar: {exc.text or exc}")

        if not payload:
            return

        try:
            await self.target.edit(reason=self.reason, **payload)  # type: ignore[arg-type]
        except discord.Forbidden:
            self.report.warn(
                "Server-Einstellungen konnten nicht geändert werden "
                "(fehlendes 'Server verwalten')."
            )
        except discord.HTTPException as exc:
            # Banner/Splash brauchen ein Boost-Level. Statt komplett aufzugeben,
            # probieren wir es ohne die optionalen Assets noch einmal.
            fallback = {
                k: v for k, v in payload.items() if k not in ("banner", "splash", "description")
            }
            self.report.warn(
                f"Einige Server-Einstellungen wurden abgelehnt ({exc.text or exc}). "
                f"Banner/Splash benötigen ein Server-Boost-Level."
            )
            if fallback:
                try:
                    await self.target.edit(reason=self.reason, **fallback)  # type: ignore[arg-type]
                except discord.HTTPException as exc2:
                    self.report.fail(f"Server-Einstellungen fehlgeschlagen: {exc2.text or exc2}")

        await self._emit(detail=self.source.name, current=1)
        await self._tick()

    # ── 3. Rollen ─────────────────────────────────────────────────────────────

    async def _clone_roles(self) -> None:
        # Discord legt jede neue Rolle ganz UNTEN an. Wer von der niedrigsten
        # zur höchsten Rolle kopiert, dreht die Hierarchie damit um. Deshalb
        # wird bewusst von oben nach unten erstellt.
        roles = sorted(
            (r for r in self.source.roles if not r.is_default() and not r.managed),
            key=lambda r: r.position,
            reverse=True,
        )
        await self._emit(Stage.ROLES, "Lege Rollen an", 0, len(roles))

        # @everyone bekommt keine neue Rolle, aber ihre Rechte.
        await self._clone_default_role()

        for index, role in enumerate(roles, start=1):
            new_role = await self._create_role(role)
            if new_role is not None:
                self.role_map[role.id] = new_role
                self.report.roles_created += 1
            await self._emit(detail=f"@{role.name}", current=index)
            await self._tick()

    async def _clone_default_role(self) -> None:
        try:
            await self.target.default_role.edit(
                permissions=self.source.default_role.permissions,
                reason=self.reason,
            )
        except discord.Forbidden:
            self.report.warn("Rechte der @everyone-Rolle konnten nicht gesetzt werden.")
        except discord.HTTPException as exc:
            self.report.warn(f"@everyone nicht aktualisiert: {exc.text or exc}")

    async def _create_role(self, role: discord.Role) -> discord.Role | None:
        kwargs: dict[str, object] = {
            "name": role.name,
            "permissions": role.permissions,
            "colour": role.colour,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "reason": self.reason,
        }
        # Rollen-Icons gibt es erst ab Boost-Level 2.
        if role.display_icon is not None and "ROLE_ICONS" in self.target.features:
            if isinstance(role.display_icon, str):
                kwargs["display_icon"] = role.display_icon
            else:
                try:
                    kwargs["display_icon"] = await role.display_icon.read()
                except discord.HTTPException:
                    pass

        try:
            return await self.target.create_role(**kwargs)  # type: ignore[arg-type]
        except discord.Forbidden:
            # Ein Bot kann keine Rechte vergeben, die er selbst nicht besitzt.
            # Lieber eine rechtelose Rolle als gar keine.
            try:
                return await self.target.create_role(
                    name=role.name,
                    colour=role.colour,
                    hoist=role.hoist,
                    mentionable=role.mentionable,
                    reason=self.reason,
                )
            except discord.HTTPException as exc:
                self.report.fail(f"Rolle @{role.name}: {exc.text or exc}")
        except discord.HTTPException as exc:
            self.report.fail(f"Rolle @{role.name}: {exc.text or exc}")
        return None

    # ── 4. Kategorien ─────────────────────────────────────────────────────────

    async def _clone_categories(self) -> None:
        categories = sorted(self.source.categories, key=lambda c: c.position)
        await self._emit(Stage.CATEGORIES, "Lege Kategorien an", 0, len(categories))

        for index, cat in enumerate(categories, start=1):
            try:
                # Kein explizites `position`: die Quell-Positionen passen nicht
                # zum leeren Ziel-Server. Die Erstellungsreihenfolge sortiert
                # zuverlässiger.
                new_cat = await self.target.create_category(
                    name=cat.name,
                    overwrites=self._build_overwrites(cat.overwrites),
                    reason=self.reason,
                )
                self.category_map[cat.id] = new_cat
                self.channel_map[cat.id] = new_cat
                self.report.categories_created += 1
            except discord.HTTPException as exc:
                self.report.fail(f"Kategorie {cat.name}: {exc.text or exc}")
            await self._emit(detail=cat.name, current=index)
            await self._tick()

    # ── 5. Channels ───────────────────────────────────────────────────────────

    async def _clone_channels(self) -> None:
        # Kategorien sind bereits erledigt, alles andere kommt in Original-
        # Reihenfolge dran, damit die Sortierung im Ziel stimmt.
        channels = sorted(
            (c for c in self.source.channels if not isinstance(c, discord.CategoryChannel)),
            key=lambda c: (c.category.position if c.category else -1, c.position),
        )
        await self._emit(Stage.CHANNELS, "Lege Channels an", 0, len(channels))

        for index, channel in enumerate(channels, start=1):
            await self._create_channel(channel)
            await self._emit(detail=f"#{channel.name}", current=index)
            await self._tick()

    async def _create_channel(self, channel: discord.abc.GuildChannel) -> None:
        common = {
            "name": channel.name,
            "overwrites": self._build_overwrites(channel.overwrites),
            "category": self.category_map.get(channel.category_id) if channel.category_id else None,
            "reason": self.reason,
        }

        try:
            if isinstance(channel, discord.TextChannel):
                created = await self._create_text_channel(channel, common)
            elif isinstance(channel, discord.VoiceChannel):
                created = await self._create_voice_channel(channel, common)
            elif isinstance(channel, discord.StageChannel):
                created = await self._create_stage_channel(channel, common)
            elif isinstance(channel, discord.ForumChannel):
                created = await self._create_forum_channel(channel, common)
            else:
                self.report.warn(
                    f"Channel-Typ von #{channel.name} wird nicht unterstützt "
                    f"und wurde übersprungen."
                )
                return
        except discord.Forbidden:
            self.report.fail(f"#{channel.name}: keine Berechtigung.")
            return
        except discord.HTTPException as exc:
            self.report.fail(f"#{channel.name}: {exc.text or exc}")
            return

        if created is not None:
            self.channel_map[channel.id] = created

    async def _create_text_channel(
        self, channel: discord.TextChannel, common: dict
    ) -> discord.abc.GuildChannel:
        # Ankündigungs-Channels gibt es nur auf Community-Servern.
        is_news = channel.is_news() and "NEWS" in self.target.features
        created = await self.target.create_text_channel(
            topic=channel.topic or discord.utils.MISSING,
            slowmode_delay=channel.slowmode_delay,
            nsfw=channel.nsfw,
            news=is_news,
            default_auto_archive_duration=channel.default_auto_archive_duration,
            **common,
        )
        if channel.is_news() and not is_news:
            self.report.warn(
                f"#{channel.name} war ein Ankündigungs-Channel — der Ziel-Server "
                f"ist kein Community-Server, daher wurde ein normaler "
                f"Text-Channel angelegt."
            )
        self.report.text_channels_created += 1
        return created

    async def _create_voice_channel(
        self, channel: discord.VoiceChannel, common: dict
    ) -> discord.abc.GuildChannel:
        created = await self.target.create_voice_channel(
            # Das Bitrate-Limit hängt am Boost-Level des ZIEL-Servers, nicht an
            # einem festen Wert. So bleibt Qualität erhalten, wo sie möglich ist.
            bitrate=min(channel.bitrate, self.target.bitrate_limit),
            user_limit=channel.user_limit,
            rtc_region=channel.rtc_region,
            video_quality_mode=channel.video_quality_mode,
            **common,
        )
        self.report.voice_channels_created += 1
        return created

    async def _create_stage_channel(
        self, channel: discord.StageChannel, common: dict
    ) -> discord.abc.GuildChannel | None:
        if "COMMUNITY" not in self.target.features:
            self.report.warn(
                f"Bühnen-Channel „{channel.name}“ übersprungen: der Ziel-Server "
                f"muss dafür ein Community-Server sein."
            )
            return None
        created = await self.target.create_stage_channel(
            bitrate=min(channel.bitrate, self.target.bitrate_limit),
            user_limit=channel.user_limit,
            rtc_region=channel.rtc_region,
            **common,
        )
        self.report.stage_channels_created += 1
        return created

    async def _create_forum_channel(
        self, channel: discord.ForumChannel, common: dict
    ) -> discord.abc.GuildChannel | None:
        if "COMMUNITY" not in self.target.features:
            self.report.warn(
                f"Forum-Channel „{channel.name}“ übersprungen: der Ziel-Server "
                f"muss dafür ein Community-Server sein."
            )
            return None

        # Tags werden neu aufgebaut — die Originale tragen IDs des Quell-Servers.
        tags = [
            discord.ForumTag(name=tag.name, emoji=tag.emoji, moderated=tag.moderated)
            for tag in channel.available_tags
        ]

        extra: dict[str, object] = {}
        # `default_sort_order` kann None sein und der Enum-Wert 0 ist falsy —
        # deshalb explizit auf None prüfen statt auf Wahrheitswert.
        if channel.default_sort_order is not None:
            extra["default_sort_order"] = channel.default_sort_order

        created = await self.target.create_forum(
            topic=channel.topic or discord.utils.MISSING,
            slowmode_delay=channel.slowmode_delay,
            nsfw=channel.nsfw,
            default_auto_archive_duration=channel.default_auto_archive_duration,
            default_layout=channel.default_layout,
            available_tags=tags,
            **extra,
            **common,
        )
        self.report.forum_channels_created += 1
        return created

    # ── 6. Server-weite Channel-Verweise ──────────────────────────────────────

    async def _finalize_guild_channels(self) -> None:
        """AFK-, System- und Regel-Channel nachträglich verknüpfen."""
        await self._emit(Stage.FINALIZING, "Verknüpfe Spezial-Channels", 0, 1)

        payload: dict[str, object] = {}
        if self.source.afk_channel is not None:
            mapped = self.channel_map.get(self.source.afk_channel.id)
            if isinstance(mapped, discord.VoiceChannel):
                payload["afk_channel"] = mapped
        if self.source.system_channel is not None:
            mapped = self.channel_map.get(self.source.system_channel.id)
            if isinstance(mapped, discord.TextChannel):
                payload["system_channel"] = mapped
                payload["system_channel_flags"] = self.source.system_channel_flags

        if payload:
            try:
                await self.target.edit(reason=self.reason, **payload)  # type: ignore[arg-type]
            except discord.HTTPException as exc:
                self.report.warn(f"Spezial-Channels nicht gesetzt: {exc.text or exc}")

        await self._emit(detail="erledigt", current=1)
        await self._tick()

    # ── 7. Emojis & Sticker ───────────────────────────────────────────────────

    async def _clone_emojis(self) -> None:
        emojis = list(self.source.emojis)
        limit = self.target.emoji_limit
        await self._emit(Stage.EMOJIS, "Kopiere Emojis", 0, len(emojis))

        if len(emojis) > limit:
            self.report.warn(
                f"Der Quell-Server hat {len(emojis)} Emojis, der Ziel-Server fasst "
                f"nur {limit}. Die überzähligen wurden ausgelassen."
            )

        for index, emoji in enumerate(emojis, start=1):
            if self.report.emojis_created >= limit:
                break
            try:
                image = await emoji.read()
                await self.target.create_custom_emoji(
                    name=emoji.name, image=image, reason=self.reason
                )
                self.report.emojis_created += 1
            except discord.HTTPException as exc:
                self.report.warn(f"Emoji :{emoji.name}: — {exc.text or exc}")
            await self._emit(detail=f":{emoji.name}:", current=index)
            await self._tick()

    async def _clone_stickers(self) -> None:
        stickers = list(self.source.stickers)
        limit = self.target.sticker_limit
        await self._emit(Stage.STICKERS, "Kopiere Sticker", 0, len(stickers))

        if len(stickers) > limit:
            self.report.warn(
                f"Der Quell-Server hat {len(stickers)} Sticker, der Ziel-Server "
                f"fasst nur {limit}. Die überzähligen wurden ausgelassen."
            )

        for index, sticker in enumerate(stickers, start=1):
            if self.report.stickers_created >= limit:
                break
            try:
                data = await sticker.read()
                await self.target.create_sticker(
                    name=sticker.name,
                    description=sticker.description or "",
                    emoji=sticker.emoji or "grinning",
                    file=discord.File(io.BytesIO(data), filename=f"{sticker.name}.png"),
                    reason=self.reason,
                )
                self.report.stickers_created += 1
            except discord.HTTPException as exc:
                self.report.warn(f"Sticker {sticker.name} — {exc.text or exc}")
            await self._emit(detail=sticker.name, current=index)
            await self._tick()

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _build_overwrites(
        self, source_overwrites: dict
    ) -> dict[discord.Role, discord.PermissionOverwrite]:
        """
        Übersetzt Permission-Overwrites vom Quell- auf den Ziel-Server.

        Rollen werden über `role_map` gemappt, @everyone direkt auf die
        Standard-Rolle des Ziel-Servers. Rechte für einzelne Mitglieder werden
        ausgelassen, da diese Mitglieder im Ziel-Server nicht existieren müssen.
        """
        new_overwrites: dict[discord.Role, discord.PermissionOverwrite] = {}

        for entity, overwrite in source_overwrites.items():
            if isinstance(entity, discord.Role):
                if entity.is_default():
                    new_overwrites[self.target.default_role] = overwrite
                elif entity.id in self.role_map:
                    new_overwrites[self.role_map[entity.id]] = overwrite
                # Rollen ohne Mapping (z. B. Bot-Rollen) werden still ausgelassen:
                # sie existieren im Ziel-Server schlicht nicht.
            elif not self._member_overwrite_warned:
                self.report.warn(_MEMBER_OVERWRITE_HINT)
                self._member_overwrite_warned = True

        return new_overwrites


def preview_counts(guild: discord.Guild) -> dict[str, int]:
    """Zählt, was ein Klon dieses Servers umfassen würde."""
    return {
        "Rollen": len([r for r in guild.roles if not r.is_default() and not r.managed]),
        "Kategorien": len(guild.categories),
        "Text-Channels": len(guild.text_channels),
        "Voice-Channels": len(guild.voice_channels),
        "Bühnen-Channels": len(guild.stage_channels),
        "Foren": len(guild.forums),
        "Emojis": len(guild.emojis),
        "Sticker": len(guild.stickers),
    }


def estimate_duration(counts: Iterable[int], delay: float) -> float:
    """Grobe Laufzeitschätzung in Sekunden."""
    return sum(counts) * (delay + 0.25)

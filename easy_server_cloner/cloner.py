"""
Die Klon-Engine.

Wendet einen `GuildBlueprint` auf einen Ziel-Server an. Woher der Bauplan
stammt — Live-Server oder Backup-Datei — ist der Engine egal; damit teilen sich
`/clone`, `/restore` und `/sync` denselben Code.

Die Klasse kennt Discord, aber weder Slash-Commands noch Embeds: die Anbindung
läuft über einen Progress-Callback.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Awaitable, Callable

import discord

from .blueprint import (
    KIND_NEWS,
    KIND_STAGE,
    KIND_TEXT,
    KIND_VOICE,
    ChannelBlueprint,
    GuildBlueprint,
    OverwriteData,
    RoleBlueprint,
    decode_image,
)
from .models import CloneOptions, CloneReport, Progress, Stage
from .pacing import AdaptivePacer
from .state import CloneState

log = logging.getLogger(__name__)

ProgressCallback = Callable[[Progress], Awaitable[None]]

# Permissions, ohne die ein Klon garantiert scheitert.
REQUIRED_PERMISSIONS = ("manage_roles", "manage_channels", "manage_guild")


class CloneError(RuntimeError):
    """Abbruchbedingung, die dem Nutzer direkt angezeigt werden kann."""


class ServerCloner:
    """Baut einen Bauplan auf einem Ziel-Server nach."""

    def __init__(
        self,
        blueprint: GuildBlueprint,
        target: discord.Guild,
        options: CloneOptions | None = None,
        *,
        delay: float = 0.6,
        on_progress: ProgressCallback | None = None,
        reason: str = "Easy Server Cloner",
        state: CloneState | None = None,
        source_guild: discord.Guild | None = None,
    ) -> None:
        self.blueprint = blueprint
        self.target = target
        # Nur für Nachrichten-Nachbau und Rollen-Zuweisung nötig; beim
        # Wiederherstellen aus einer Datei gibt es keinen Quell-Server.
        self.source_guild = source_guild
        self.options = options or CloneOptions()
        self.reason = reason
        self._on_progress = on_progress
        self._cancel = asyncio.Event()
        self.pacer = AdaptivePacer(delay, cancel_event=self._cancel)

        self.progress = Progress()
        self.report = self.progress.report

        # Zwischenstand: leer bei einem frischen Lauf, gefüllt beim Fortsetzen.
        self.state = state or CloneState(
            target_id=target.id, source_name=blueprint.source_name or blueprint.name
        )
        self.resuming = bool(state and not state.is_empty)

        # Bauplan-Schlüssel → erzeugtes Objekt im Ziel-Server
        self.role_map: dict[str, discord.Role] = {}
        self.channel_map: dict[str, discord.abc.GuildChannel] = {}

    # ── Steuerung ─────────────────────────────────────────────────────────────

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Hängt die Fortschrittsanzeige ein — meist erst, wenn die
        Status-Nachricht steht, deren Objekt der Callback braucht."""
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
        """Pause nach einem API-Aufruf, mit sofortiger Abbruch-Reaktion."""
        await self.pacer.wait()

    async def _call(self, coro):
        """API-Aufruf mit Zeitmessung für die adaptive Taktung."""
        self._check_cancel()
        return await self.pacer.call(coro)

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
        self.progress.delay = self.pacer.delay
        self.progress.throttled = self.pacer.was_throttled
        if self._on_progress is not None:
            try:
                await self._on_progress(self.progress)
            except Exception:  # Anzeige darf den Klon niemals stoppen
                log.exception("Progress-Callback ist fehlgeschlagen")

    # ── Einstiegspunkt ────────────────────────────────────────────────────────

    async def run(self) -> CloneReport:
        """Führt den kompletten Vorgang aus."""
        try:
            await self._validate()
            self._restore_maps_from_state()

            if self.options.dry_run:
                self.progress.stage = Stage.DONE
                return self.report

            # Beim Fortsetzen darf nicht erneut geleert werden — sonst wäre
            # genau das weg, was der erste Anlauf schon geschafft hat.
            if self.options.wipe_target and not self.resuming:
                await self._wipe_target()
            if self.options.settings or self.options.icon:
                await self._apply_settings()
            if self.options.roles:
                await self._apply_roles()
            if self.options.channels:
                await self._apply_categories()
                await self._apply_channels()
                await self._apply_guild_channel_links()
            if self.options.emojis:
                await self._apply_emojis()
            if self.options.stickers:
                await self._apply_stickers()
            await self._apply_extras()

            if self.pacer.was_throttled:
                self.report.warn(
                    f"Discord hat {self.pacer.throttle_events}× gebremst — die Pause "
                    f"wurde automatisch auf {self.pacer.delay:.2f}s angehoben."
                )

            await self._emit(Stage.DONE, "Fertig")
            # Sauber durchgelaufen: der Zwischenstand wird nicht mehr gebraucht.
            self.state.clear()
        except asyncio.CancelledError:
            self.progress.stage = Stage.CANCELLED
            self.state.save()
            self.report.warn(
                "Vorgang abgebrochen. Der Zwischenstand wurde gespeichert — "
                "`/clone resume:True` setzt dort fort."
            )
            raise
        except CloneError as exc:
            self.progress.stage = Stage.FAILED
            self.state.save()
            self.report.fail(str(exc))
            raise
        except Exception as exc:  # pragma: no cover - Sicherheitsnetz
            log.exception("Unerwarteter Fehler beim Klonen")
            self.progress.stage = Stage.FAILED
            self.state.save()
            self.report.fail(f"Unerwarteter Fehler: {exc}")
            raise CloneError(str(exc)) from exc
        finally:
            self.report.finished_at = time.monotonic()
            self.report.throttle_events = self.pacer.throttle_events

        return self.report

    # ── 0. Vorabprüfungen ─────────────────────────────────────────────────────

    async def _validate(self) -> None:
        await self._emit(Stage.VALIDATING, "Prüfe Server und Berechtigungen")

        # Der mit Abstand teuerste Bedienfehler: sich selbst überschreiben.
        if self.blueprint.source_id and self.blueprint.source_id == str(self.target.id):
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

        if self.options.roles:
            wanted = len(self.blueprint.creatable_roles)
            room = me.top_role.position - 1
            if wanted > room:
                self.report.warn(
                    f"Der Bauplan enthält {wanted} Rollen, oberhalb der Bot-Rolle ist "
                    f"aber nur Platz für {max(room, 0)}. Die Rangfolge wird gestaucht "
                    f"— schiebe die Bot-Rolle weiter nach oben."
                )

        if not self.blueprint.has_images and (self.options.emojis or self.options.icon):
            self.report.warn(
                "Dieser Bauplan wurde ohne Bilder erstellt. Icon, Banner, Emojis "
                "und Sticker fehlen deshalb."
            )

        await self._emit(detail="Voraussetzungen erfüllt")

    def _restore_maps_from_state(self) -> None:
        """Baut nach einem Abbruch die Zuordnungen aus dem Zwischenstand auf."""
        if not self.resuming:
            return

        recovered_roles = 0
        for key, role_id in list(self.state.roles.items()):
            role = self.target.get_role(role_id)
            if role is None:
                # Zwischenzeitlich gelöscht — dann eben neu anlegen.
                self.state.roles.pop(key, None)
                continue
            self.role_map[key] = role
            recovered_roles += 1

        recovered_channels = 0
        for key, channel_id in list(self.state.channels.items()):
            channel = self.target.get_channel(channel_id)
            if channel is None:
                self.state.channels.pop(key, None)
                continue
            self.channel_map[key] = channel
            recovered_channels += 1

        self.report.warn(
            f"Fortsetzung eines abgebrochenen Laufs: {recovered_roles} Rollen und "
            f"{recovered_channels} Channels waren bereits vorhanden und wurden "
            f"übersprungen."
        )

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
                await self._call(channel.delete(reason=self.reason))
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
                await self._call(role.delete(reason=self.reason))
                self.report.roles_deleted += 1
            except discord.Forbidden:
                self.report.warn(f"Keine Rechte zum Löschen der Rolle @{role.name}.")
            except discord.HTTPException as exc:
                self.report.warn(f"@{role.name} nicht gelöscht: {exc.text or exc}")
            done += 1
            await self._emit(detail=f"@{role.name}", current=done)
            await self._tick()

    # ── 2. Server-Einstellungen ───────────────────────────────────────────────

    async def _apply_settings(self) -> None:
        await self._emit(Stage.SETTINGS, "Name, Icon und Grundeinstellungen", 0, 1)
        bp = self.blueprint
        payload: dict[str, object] = {}

        if self.options.settings:
            payload.update(
                name=bp.name,
                verification_level=discord.VerificationLevel(bp.verification_level),
                default_notifications=discord.NotificationLevel(bp.default_notifications),
                explicit_content_filter=discord.ContentFilter(bp.explicit_content_filter),
                afk_timeout=bp.afk_timeout,
            )
            if bp.description and "COMMUNITY" in self.target.features:
                payload["description"] = bp.description

        if self.options.icon:
            # Wichtig: Assets nur setzen, wenn der Bauplan wirklich eines
            # enthält. Ein `icon=None` würde das Ziel-Icon sonst aktiv löschen.
            for attr, encoded in (("icon", bp.icon), ("banner", bp.banner), ("splash", bp.splash)):
                data = decode_image(encoded)
                if data:
                    payload[attr] = data

        if not payload:
            return

        try:
            await self._call(self.target.edit(reason=self.reason, **payload))  # type: ignore[arg-type]
        except discord.Forbidden:
            self.report.warn(
                "Server-Einstellungen konnten nicht geändert werden "
                "(fehlendes 'Server verwalten')."
            )
        except discord.HTTPException as exc:
            # Banner und Splash brauchen ein Boost-Level. Statt komplett
            # aufzugeben, ohne die optionalen Assets noch einmal versuchen.
            fallback = {
                k: v for k, v in payload.items() if k not in ("banner", "splash", "description")
            }
            self.report.warn(
                f"Einige Server-Einstellungen wurden abgelehnt ({exc.text or exc}). "
                f"Banner und Splash benötigen ein Server-Boost-Level."
            )
            if fallback:
                try:
                    await self._call(self.target.edit(reason=self.reason, **fallback))  # type: ignore[arg-type]
                except discord.HTTPException as exc2:
                    self.report.fail(f"Server-Einstellungen fehlgeschlagen: {exc2.text or exc2}")

        await self._emit(detail=bp.name, current=1)
        await self._tick()

    # ── 3. Rollen ─────────────────────────────────────────────────────────────

    async def _apply_roles(self) -> None:
        roles = self.blueprint.creatable_roles
        await self._emit(Stage.ROLES, "Lege Rollen an", 0, len(roles))

        await self._apply_default_role()

        for index, role_bp in enumerate(roles, start=1):
            if role_bp.key in self.role_map:
                await self._emit(detail=f"@{role_bp.name} (vorhanden)", current=index)
                continue

            new_role = await self._create_role(role_bp)
            if new_role is not None:
                self.role_map[role_bp.key] = new_role
                self.state.roles[role_bp.key] = new_role.id
                self.state.save()
                self.report.roles_created += 1
            await self._emit(detail=f"@{role_bp.name}", current=index)
            await self._tick()

    async def _apply_default_role(self) -> None:
        default_bp = self.blueprint.default_role
        if default_bp is None:
            return
        # @everyone existiert auf jedem Server bereits — nur die Rechte
        # werden übernommen.
        self.role_map[default_bp.key] = self.target.default_role
        try:
            await self._call(
                self.target.default_role.edit(
                    permissions=discord.Permissions(default_bp.permissions),
                    reason=self.reason,
                )
            )
        except discord.Forbidden:
            self.report.warn("Rechte der @everyone-Rolle konnten nicht gesetzt werden.")
        except discord.HTTPException as exc:
            self.report.warn(f"@everyone nicht aktualisiert: {exc.text or exc}")

    async def _create_role(self, role_bp: RoleBlueprint) -> discord.Role | None:
        kwargs: dict[str, object] = {
            "name": role_bp.name,
            "permissions": discord.Permissions(role_bp.permissions),
            "colour": discord.Colour(role_bp.colour),
            "hoist": role_bp.hoist,
            "mentionable": role_bp.mentionable,
            "reason": self.reason,
        }
        # Rollen-Icons gibt es erst ab Boost-Level 2.
        if "ROLE_ICONS" in self.target.features:
            if role_bp.unicode_emoji:
                kwargs["display_icon"] = role_bp.unicode_emoji
            elif role_bp.icon_bytes:
                kwargs["display_icon"] = role_bp.icon_bytes

        try:
            return await self._call(self.target.create_role(**kwargs))  # type: ignore[arg-type]
        except discord.Forbidden:
            # Ein Bot kann keine Rechte vergeben, die er selbst nicht besitzt.
            # Lieber eine rechtelose Rolle als gar keine.
            try:
                return await self._call(
                    self.target.create_role(
                        name=role_bp.name,
                        colour=discord.Colour(role_bp.colour),
                        hoist=role_bp.hoist,
                        mentionable=role_bp.mentionable,
                        reason=self.reason,
                    )
                )
            except discord.HTTPException as exc:
                self.report.fail(f"Rolle @{role_bp.name}: {exc.text or exc}")
        except discord.HTTPException as exc:
            self.report.fail(f"Rolle @{role_bp.name}: {exc.text or exc}")
        return None

    # ── 4. Kategorien ─────────────────────────────────────────────────────────

    async def _apply_categories(self) -> None:
        categories = self.blueprint.categories
        await self._emit(Stage.CATEGORIES, "Lege Kategorien an", 0, len(categories))

        for index, cat in enumerate(categories, start=1):
            if cat.key in self.channel_map:
                await self._emit(detail=f"{cat.name} (vorhanden)", current=index)
                continue
            if self.options.skip_existing:
                existing = discord.utils.get(self.target.categories, name=cat.name)
                if existing is not None:
                    self.channel_map[cat.key] = existing
                    self.state.channels[cat.key] = existing.id
                    await self._emit(detail=f"{cat.name} (vorhanden)", current=index)
                    continue

            try:
                # Kein explizites `position`: die Positionen des Bauplans passen
                # nicht zum leeren Ziel-Server. Die Erstellungsreihenfolge
                # sortiert zuverlässiger.
                new_cat = await self._call(
                    self.target.create_category(
                        name=cat.name,
                        overwrites=self._build_overwrites(cat.overwrites),
                        reason=self.reason,
                    )
                )
                self.channel_map[cat.key] = new_cat
                self.state.channels[cat.key] = new_cat.id
                self.state.save()
                self.report.categories_created += 1
            except discord.HTTPException as exc:
                self.report.fail(f"Kategorie {cat.name}: {exc.text or exc}")
            await self._emit(detail=cat.name, current=index)
            await self._tick()

    # ── 5. Channels ───────────────────────────────────────────────────────────

    async def _apply_channels(self) -> None:
        channels = self.blueprint.non_categories
        await self._emit(Stage.CHANNELS, "Lege Channels an", 0, len(channels))

        for index, channel in enumerate(channels, start=1):
            if channel.key in self.channel_map:
                await self._emit(detail=f"#{channel.name} (vorhanden)", current=index)
                continue
            if self.options.skip_existing and self._find_existing_channel(channel) is not None:
                existing = self._find_existing_channel(channel)
                self.channel_map[channel.key] = existing  # type: ignore[assignment]
                self.state.channels[channel.key] = existing.id  # type: ignore[union-attr]
                await self._emit(detail=f"#{channel.name} (vorhanden)", current=index)
                continue

            created = await self._create_channel(channel)
            if created is not None:
                self.channel_map[channel.key] = created
                self.state.channels[channel.key] = created.id
                self.state.save()
            await self._emit(detail=f"#{channel.name}", current=index)
            await self._tick()

    def _find_existing_channel(self, channel: ChannelBlueprint):
        """Sucht im Ziel nach einem gleichnamigen Channel in derselben Kategorie."""
        parent = self.channel_map.get(channel.category_key or "")
        candidates = (
            parent.channels if isinstance(parent, discord.CategoryChannel) else self.target.channels
        )
        return discord.utils.get(candidates, name=channel.name)

    async def _create_channel(self, channel: ChannelBlueprint):
        category = self.channel_map.get(channel.category_key or "")
        common = {
            "name": channel.name,
            "overwrites": self._build_overwrites(channel.overwrites),
            "category": category if isinstance(category, discord.CategoryChannel) else None,
            "reason": self.reason,
        }

        if channel.needs_community and "COMMUNITY" not in self.target.features:
            label = "Bühnen-Channel" if channel.kind == KIND_STAGE else "Forum-Channel"
            self.report.warn(
                f"{label} „{channel.name}“ übersprungen: der Ziel-Server muss dafür "
                f"ein Community-Server sein."
            )
            return None

        try:
            if channel.kind in (KIND_TEXT, KIND_NEWS):
                return await self._create_text_channel(channel, common)
            if channel.kind == KIND_VOICE:
                return await self._create_voice_channel(channel, common)
            if channel.kind == KIND_STAGE:
                return await self._create_stage_channel(channel, common)
            if channel.kind == KIND_FORUM:
                return await self._create_forum_channel(channel, common)
            self.report.warn(
                f"Channel-Typ „{channel.kind}“ von #{channel.name} wird nicht "
                f"unterstützt und wurde übersprungen."
            )
        except discord.Forbidden:
            self.report.fail(f"#{channel.name}: keine Berechtigung.")
        except discord.HTTPException as exc:
            self.report.fail(f"#{channel.name}: {exc.text or exc}")
        return None

    async def _create_text_channel(self, channel: ChannelBlueprint, common: dict):
        # Ankündigungs-Channels gibt es nur auf Community-Servern.
        is_news = channel.kind == KIND_NEWS and "NEWS" in self.target.features
        created = await self._call(
            self.target.create_text_channel(
                topic=channel.topic or discord.utils.MISSING,
                slowmode_delay=channel.slowmode_delay,
                nsfw=channel.nsfw,
                news=is_news,
                default_auto_archive_duration=channel.default_auto_archive_duration,
                **common,
            )
        )
        if channel.kind == KIND_NEWS and not is_news:
            self.report.warn(
                f"#{channel.name} war ein Ankündigungs-Channel — der Ziel-Server ist "
                f"kein Community-Server, daher wurde ein normaler Text-Channel angelegt."
            )
        self.report.text_channels_created += 1
        return created

    async def _create_voice_channel(self, channel: ChannelBlueprint, common: dict):
        created = await self._call(
            self.target.create_voice_channel(
                # Das Bitrate-Limit hängt am Boost-Level des ZIEL-Servers, nicht
                # an einem festen Wert. So bleibt Qualität erhalten, wo möglich.
                bitrate=min(channel.bitrate, self.target.bitrate_limit),
                user_limit=channel.user_limit,
                rtc_region=channel.rtc_region,
                video_quality_mode=discord.VideoQualityMode(channel.video_quality_mode),
                **common,
            )
        )
        self.report.voice_channels_created += 1
        return created

    async def _create_stage_channel(self, channel: ChannelBlueprint, common: dict):
        created = await self._call(
            self.target.create_stage_channel(
                bitrate=min(channel.bitrate, self.target.bitrate_limit),
                user_limit=channel.user_limit,
                rtc_region=channel.rtc_region,
                **common,
            )
        )
        self.report.stage_channels_created += 1
        return created

    async def _create_forum_channel(self, channel: ChannelBlueprint, common: dict):
        # Tags werden neu aufgebaut — die Originale tragen IDs des Quell-Servers.
        tags = [
            discord.ForumTag(name=tag.name, emoji=tag.emoji, moderated=tag.moderated)
            for tag in channel.available_tags
        ]
        extra: dict[str, object] = {}
        # Der Enum-Wert 0 ist falsy, deshalb explizit auf None prüfen.
        if channel.default_sort_order is not None:
            extra["default_sort_order"] = discord.ForumOrderType(channel.default_sort_order)

        created = await self._call(
            self.target.create_forum(
                topic=channel.topic or discord.utils.MISSING,
                slowmode_delay=channel.slowmode_delay,
                nsfw=channel.nsfw,
                default_auto_archive_duration=channel.default_auto_archive_duration,
                default_layout=discord.ForumLayoutType(channel.default_layout),
                available_tags=tags,
                **extra,
                **common,
            )
        )
        self.report.forum_channels_created += 1
        return created

    # ── 6. Server-weite Channel-Verweise ──────────────────────────────────────

    async def _apply_guild_channel_links(self) -> None:
        """AFK-, System- und Regel-Channel nachträglich verknüpfen."""
        await self._emit(Stage.FINALIZING, "Verknüpfe Spezial-Channels", 0, 1)

        payload: dict[str, object] = {}
        afk = self.channel_map.get(self.blueprint.afk_channel_key or "")
        if isinstance(afk, discord.VoiceChannel):
            payload["afk_channel"] = afk

        system = self.channel_map.get(self.blueprint.system_channel_key or "")
        if isinstance(system, discord.TextChannel):
            payload["system_channel"] = system
            payload["system_channel_flags"] = discord.SystemChannelFlags._from_value(
                self.blueprint.system_channel_flags
            )

        if payload:
            try:
                await self._call(self.target.edit(reason=self.reason, **payload))  # type: ignore[arg-type]
            except discord.HTTPException as exc:
                self.report.warn(f"Spezial-Channels nicht gesetzt: {exc.text or exc}")

        await self._emit(detail="erledigt", current=1)
        await self._tick()

    # ── 7. Emojis & Sticker ───────────────────────────────────────────────────

    async def _apply_emojis(self) -> None:
        emojis = self.blueprint.emojis
        limit = self.target.emoji_limit
        await self._emit(Stage.EMOJIS, "Kopiere Emojis", 0, len(emojis))

        if len(emojis) > limit:
            self.report.warn(
                f"Der Bauplan enthält {len(emojis)} Emojis, der Ziel-Server fasst nur "
                f"{limit}. Die überzähligen wurden ausgelassen."
            )

        existing = {e.name for e in self.target.emojis}
        created_here = 0
        for index, emoji in enumerate(emojis, start=1):
            if len(existing) + created_here >= limit:
                break
            if emoji.name in self.state.emojis or emoji.name in existing:
                await self._emit(detail=f":{emoji.name}: (vorhanden)", current=index)
                continue
            data = emoji.image_bytes
            if not data:
                await self._emit(detail=f":{emoji.name}: (kein Bild)", current=index)
                continue
            try:
                await self._call(
                    self.target.create_custom_emoji(
                        name=emoji.name, image=data, reason=self.reason
                    )
                )
                self.report.emojis_created += 1
                created_here += 1
                self.state.emojis.append(emoji.name)
                self.state.save()
            except discord.HTTPException as exc:
                self.report.warn(f"Emoji :{emoji.name}: — {exc.text or exc}")
            await self._emit(detail=f":{emoji.name}:", current=index)
            await self._tick()

    async def _apply_stickers(self) -> None:
        stickers = self.blueprint.stickers
        limit = self.target.sticker_limit
        await self._emit(Stage.STICKERS, "Kopiere Sticker", 0, len(stickers))

        if len(stickers) > limit:
            self.report.warn(
                f"Der Bauplan enthält {len(stickers)} Sticker, der Ziel-Server fasst "
                f"nur {limit}. Die überzähligen wurden ausgelassen."
            )

        existing = {s.name for s in self.target.stickers}
        created_here = 0
        for index, sticker in enumerate(stickers, start=1):
            if len(existing) + created_here >= limit:
                break
            if sticker.name in self.state.stickers or sticker.name in existing:
                await self._emit(detail=f"{sticker.name} (vorhanden)", current=index)
                continue
            data = sticker.image_bytes
            if not data:
                await self._emit(detail=f"{sticker.name} (kein Bild)", current=index)
                continue
            try:
                suffix = "gif" if sticker.file_format == "gif" else "png"
                await self._call(
                    self.target.create_sticker(
                        name=sticker.name,
                        description=sticker.description,
                        emoji=sticker.emoji,
                        file=discord.File(
                            io.BytesIO(data), filename=f"{sticker.name}.{suffix}"
                        ),
                        reason=self.reason,
                    )
                )
                self.report.stickers_created += 1
                created_here += 1
                self.state.stickers.append(sticker.name)
                self.state.save()
            except discord.HTTPException as exc:
                self.report.warn(f"Sticker {sticker.name} — {exc.text or exc}")
            await self._emit(detail=sticker.name, current=index)
            await self._tick()

    # ── 8. Nachrichten & Rollen-Zuweisungen ───────────────────────────────────

    async def _apply_extras(self) -> None:
        """Schritte, die einen echten Quell-Server voraussetzen."""
        wants_extras = self.options.messages_per_channel > 0 or self.options.member_roles
        if not wants_extras:
            return

        if self.source_guild is None:
            self.report.warn(
                "Nachrichten-Nachbau und Rollen-Zuweisung brauchen einen echten "
                "Quell-Server und stehen beim Wiederherstellen aus einer Datei "
                "nicht zur Verfügung."
            )
            return

        # Import bewusst hier: `extras` importiert seinerseits aus diesem Modul.
        from . import extras

        if self.options.messages_per_channel > 0:
            await extras.copy_messages(
                self, self.source_guild, self.options.messages_per_channel
            )
        if self.options.member_roles:
            await extras.assign_member_roles(self, self.source_guild)

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _build_overwrites(
        self, overwrites: list[OverwriteData]
    ) -> dict[discord.Role, discord.PermissionOverwrite]:
        """
        Übersetzt die Overrides des Bauplans auf die Rollen des Ziel-Servers.

        Rollen ohne Gegenstück im Ziel werden still ausgelassen — sie existieren
        dort schlicht nicht.
        """
        result: dict[discord.Role, discord.PermissionOverwrite] = {}
        for entry in overwrites:
            role = self.role_map.get(entry.role_key)
            if role is not None:
                result[role] = entry.to_overwrite()
        return result


def estimate_duration(blueprint: GuildBlueprint, delay: float, *, wipe: int = 0) -> float:
    """Grobe Laufzeitschätzung in Sekunden."""
    return (blueprint.total_objects + wipe) * (delay + 0.25)

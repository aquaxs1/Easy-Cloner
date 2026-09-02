"""
Die Klon-Engine.

Applies a `GuildBlueprint` to a target server. Where the blueprint came from
— a live server or a backup file — is irrelevant to the engine, which is how
`/clone`, `/restore` and `/sync` share the same code.

The class knows Discord, but neither slash commands nor embeds: the binding
runs through a progress callback.
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

# Permissions without which a clone is guaranteed to fail.
REQUIRED_PERMISSIONS = ("manage_roles", "manage_channels", "manage_guild")


class CloneError(RuntimeError):
    """An abort condition that can be shown to the user directly."""


class ServerCloner:
    """Rebuilds a blueprint on a target server."""

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
        # Only needed for message re-staging and role assignment; when
        # restoring from a file there is no source server.
        self.source_guild = source_guild
        self.options = options or CloneOptions()
        self.reason = reason
        self._on_progress = on_progress
        self._cancel = asyncio.Event()
        self.pacer = AdaptivePacer(delay, cancel_event=self._cancel)

        self.progress = Progress()
        self.report = self.progress.report

        # Checkpoint: empty on a fresh run, populated when resuming.
        self.state = state or CloneState(
            target_id=target.id, source_name=blueprint.source_name or blueprint.name
        )
        self.resuming = bool(state and not state.is_empty)

        # Blueprint key -> the object created in the target server
        self.role_map: dict[str, discord.Role] = {}
        self.channel_map: dict[str, discord.abc.GuildChannel] = {}

    # ── Steuerung ─────────────────────────────────────────────────────────────

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Attaches the progress display — usually only once the status
        message exists, whose object the callback needs."""
        self._on_progress = callback

    def cancel(self) -> None:
        """Cancels the running job at the next intermediate step."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise asyncio.CancelledError

    async def _tick(self) -> None:
        """Pause after an API call, reacting to a cancellation at once."""
        await self.pacer.wait()

    async def _call(self, coro):
        """An API call, timed to feed the adaptive pacer."""
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
            except Exception:  # the display must never stop the clone
                log.exception("Progress callback failed")

    # ── Einstiegspunkt ────────────────────────────────────────────────────────

    async def run(self) -> CloneReport:
        """Runs the whole job."""
        try:
            await self._validate()
            self._restore_maps_from_state()

            if self.options.dry_run:
                self.progress.stage = Stage.DONE
                return self.report

            # Resuming must not wipe again — that would remove exactly what
            # the first attempt already achieved.
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
                    f"Discord throttled {self.pacer.throttle_events}× — the pause "
                    f"was raised automatically to {self.pacer.delay:.2f}s."
                )

            await self._emit(Stage.DONE, "Fertig")
            # Finished cleanly: the checkpoint is no longer needed.
            self.state.clear()
        except asyncio.CancelledError:
            self.progress.stage = Stage.CANCELLED
            self.state.save()
            self.report.warn(
                "Job cancelled. The checkpoint has been saved — "
                "`/clone resume:True` picks up there."
            )
            raise
        except CloneError as exc:
            self.progress.stage = Stage.FAILED
            self.state.save()
            self.report.fail(str(exc))
            raise
        except Exception as exc:  # pragma: no cover - Sicherheitsnetz
            log.exception("Unexpected error while cloning")
            self.progress.stage = Stage.FAILED
            self.state.save()
            self.report.fail(f"Unexpected error: {exc}")
            raise CloneError(str(exc)) from exc
        finally:
            self.report.finished_at = time.monotonic()
            self.report.throttle_events = self.pacer.throttle_events

        return self.report

    # ── 0. Preflight checks ───────────────────────────────────────────────────

    async def _validate(self) -> None:
        await self._emit(Stage.VALIDATING, "Checking servers and permissions")

        # By far the most expensive mistake: overwriting yourself.
        if self.blueprint.source_id and self.blueprint.source_id == str(self.target.id):
            raise CloneError(
                "Source and target server are the same. That would delete the "
                "server instead of copying it — job aborted."
            )

        me = self.target.me
        if me is None:
            raise CloneError(
                "The bot is not a member of the target server. Invite it first."
            )

        perms = me.guild_permissions
        if not perms.administrator:
            missing = [p for p in REQUIRED_PERMISSIONS if not getattr(perms, p)]
            if missing:
                pretty = ", ".join(p.replace("_", " ").title() for p in missing)
                raise CloneError(
                    f"The bot is missing these permissions on the target: {pretty}.\n"
                    f"The simplest fix is to give it the Administrator role."
                )

        if self.options.roles:
            wanted = len(self.blueprint.creatable_roles)
            room = me.top_role.position - 1
            if wanted > room:
                self.report.warn(
                    f"The blueprint has {wanted} roles, but there is only room for "
                    f"{max(room, 0)} above the bot's role. The hierarchy will be "
                    f"compressed — move the bot's role further up."
                )

        if not self.blueprint.has_images and (self.options.emojis or self.options.icon):
            self.report.warn(
                "This blueprint was created without images, so icon, banner, "
                "emojis and stickers are missing."
            )

        await self._emit(detail="Prerequisites met")

    def _restore_maps_from_state(self) -> None:
        """Rebuilds the mappings from the checkpoint after an interruption."""
        if not self.resuming:
            return

        recovered_roles = 0
        for key, role_id in list(self.state.roles.items()):
            role = self.target.get_role(role_id)
            if role is None:
                # Deleted in the meantime — just create it again.
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
            f"Resuming an interrupted run: {recovered_roles} roles and "
            f"{recovered_channels} channels already existed and were "
            f"skipped."
        )

    # ── 1. wipe the target server ─────────────────────────────────────────────

    async def _wipe_target(self) -> None:
        channels = list(self.target.channels)
        roles = [
            r
            for r in self.target.roles
            if not r.is_default() and not r.managed and r < self.target.me.top_role
        ]
        total = len(channels) + len(roles)
        await self._emit(Stage.WIPING, "Deleting existing content", 0, total)

        done = 0
        for channel in channels:
            try:
                await self._call(channel.delete(reason=self.reason))
                self.report.channels_deleted += 1
            except discord.Forbidden:
                self.report.warn(f"No permission to delete #{channel.name}.")
            except discord.HTTPException as exc:
                self.report.warn(f"#{channel.name} not deleted: {exc.text or exc}")
            done += 1
            await self._emit(detail=f"#{channel.name}", current=done)
            await self._tick()

        for role in roles:
            try:
                await self._call(role.delete(reason=self.reason))
                self.report.roles_deleted += 1
            except discord.Forbidden:
                self.report.warn(f"No permission to delete the role @{role.name}.")
            except discord.HTTPException as exc:
                self.report.warn(f"@{role.name} not deleted: {exc.text or exc}")
            done += 1
            await self._emit(detail=f"@{role.name}", current=done)
            await self._tick()

    # ── 2. server settings ────────────────────────────────────────────────────

    async def _apply_settings(self) -> None:
        await self._emit(Stage.SETTINGS, "Name, icon and base settings", 0, 1)
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
            # Important: only set assets the blueprint actually has. An
            # `icon=None` would actively delete the target's icon.
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
                "Server settings could not be changed "
                "(fehlendes 'Server verwalten')."
            )
        except discord.HTTPException as exc:
            # Banner and splash need a boost level. Rather than giving up
            # entirely, retry without the optional assets.
            fallback = {
                k: v for k, v in payload.items() if k not in ("banner", "splash", "description")
            }
            self.report.warn(
                f"Some server settings were rejected ({exc.text or exc}). "
                f"Banner and splash require a server boost level."
            )
            if fallback:
                try:
                    await self._call(self.target.edit(reason=self.reason, **fallback))  # type: ignore[arg-type]
                except discord.HTTPException as exc2:
                    self.report.fail(f"The server settings failed: {exc2.text or exc2}")

        await self._emit(detail=bp.name, current=1)
        await self._tick()

    # ── 3. Roles ──────────────────────────────────────────────────────────────

    async def _apply_roles(self) -> None:
        roles = self.blueprint.creatable_roles
        await self._emit(Stage.ROLES, "Creating roles", 0, len(roles))

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
        # @everyone already exists on every server — only the permissions
        # are copied.
        self.role_map[default_bp.key] = self.target.default_role
        try:
            await self._call(
                self.target.default_role.edit(
                    permissions=discord.Permissions(default_bp.permissions),
                    reason=self.reason,
                )
            )
        except discord.Forbidden:
            self.report.warn("Could not set the permissions of the @everyone role.")
        except discord.HTTPException as exc:
            self.report.warn(f"@everyone not updated: {exc.text or exc}")

    async def _create_role(self, role_bp: RoleBlueprint) -> discord.Role | None:
        kwargs: dict[str, object] = {
            "name": role_bp.name,
            "permissions": discord.Permissions(role_bp.permissions),
            "colour": discord.Colour(role_bp.colour),
            "hoist": role_bp.hoist,
            "mentionable": role_bp.mentionable,
            "reason": self.reason,
        }
        # Role icons need boost level 2.
        if "ROLE_ICONS" in self.target.features:
            if role_bp.unicode_emoji:
                kwargs["display_icon"] = role_bp.unicode_emoji
            elif role_bp.icon_bytes:
                kwargs["display_icon"] = role_bp.icon_bytes

        try:
            return await self._call(self.target.create_role(**kwargs))  # type: ignore[arg-type]
        except discord.Forbidden:
            # A bot cannot grant permissions it does not hold itself.
            # A role without permissions beats no role at all.
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
                self.report.fail(f"Role @{role_bp.name}: {exc.text or exc}")
        except discord.HTTPException as exc:
            self.report.fail(f"Role @{role_bp.name}: {exc.text or exc}")
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
                # No explicit `position`: the blueprint's positions do not fit
                # an empty target server. Creation order sorts more reliably.

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
        """Finds a channel of the same name in the same category in the target."""
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
            label = "Stage channel" if channel.kind == KIND_STAGE else "Forum channel"
            self.report.warn(
                f"{label} \"{channel.name}\" skipped: the target server has to be "
                f"a Community server for that."
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
                f"Channel type \"{channel.kind}\" of #{channel.name} is not "
                f"supported and was skipped."
            )
        except discord.Forbidden:
            self.report.fail(f"#{channel.name}: no permission.")
        except discord.HTTPException as exc:
            self.report.fail(f"#{channel.name}: {exc.text or exc}")
        return None

    async def _create_text_channel(self, channel: ChannelBlueprint, common: dict):
        # Announcement channels only exist on Community servers.
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
                f"#{channel.name} was an announcement channel — the target is not a "
                f"Community server, so a normal text channel was created instead."
            )
        self.report.text_channels_created += 1
        return created

    async def _create_voice_channel(self, channel: ChannelBlueprint, common: dict):
        created = await self._call(
            self.target.create_voice_channel(
                # The bitrate limit depends on the TARGET server's boost level,
                # not on a fixed value. This keeps quality where possible.
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
        # Tags are rebuilt — the originals carry source-server IDs.
        tags = [
            discord.ForumTag(name=tag.name, emoji=tag.emoji, moderated=tag.moderated)
            for tag in channel.available_tags
        ]
        extra: dict[str, object] = {}
        # The enum value 0 is falsy, so check against None explicitly.
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

    # ── 6. server-wide channel references ─────────────────────────────────────

    async def _apply_guild_channel_links(self) -> None:
        """Links the AFK, system and rules channels afterwards."""
        await self._emit(Stage.FINALIZING, "Linking special channels", 0, 1)

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
                self.report.warn(f"Special channels not set: {exc.text or exc}")

        await self._emit(detail="erledigt", current=1)
        await self._tick()

    # ── 7. Emojis & Sticker ───────────────────────────────────────────────────

    async def _apply_emojis(self) -> None:
        emojis = self.blueprint.emojis
        limit = self.target.emoji_limit
        await self._emit(Stage.EMOJIS, "Kopiere Emojis", 0, len(emojis))

        if len(emojis) > limit:
            self.report.warn(
                f"The blueprint has {len(emojis)} emojis but the target only holds "
                f"{limit}. The surplus was left out."
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
                await self._emit(detail=f":{emoji.name}: (no image)", current=index)
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
                f"The blueprint has {len(stickers)} stickers but the target only "
                f"holds {limit}. The surplus was left out."
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
                await self._emit(detail=f"{sticker.name} (no image)", current=index)
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

    # ── 8. Messages & role assignments ────────────────────────────────────────

    async def _apply_extras(self) -> None:
        """Steps that require a real source server."""
        wants_extras = self.options.messages_per_channel > 0 or self.options.member_roles
        if not wants_extras:
            return

        if self.source_guild is None:
            self.report.warn(
                "Message re-staging and role assignment need a real source "
                "server and are not available when restoring from a file."

            )
            return

        # imported here on purpose: `extras` imports from this module in turn.
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
        Translates the blueprint's overrides onto the target server's roles.

        Roles with no counterpart in the target are silently skipped — they
        simply do not exist there.
        """
        result: dict[discord.Role, discord.PermissionOverwrite] = {}
        for entry in overwrites:
            role = self.role_map.get(entry.role_key)
            if role is not None:
                result[role] = entry.to_overwrite()
        return result


def estimate_duration(blueprint: GuildBlueprint, delay: float, *, wipe: int = 0) -> float:
    """A rough runtime estimate in seconds."""
    return (blueprint.total_objects + wipe) * (delay + 0.25)

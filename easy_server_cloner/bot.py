"""The Discord bot: slash commands, permissions and the live display."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands

from . import embeds, snapshot
from .blueprint import GuildBlueprint
from .cloner import CloneError, ServerCloner, estimate_duration
from .config import Config
from .models import CloneOptions, Stage
from .state import CloneState
from .views import CancelView, ConfirmView

log = logging.getLogger(__name__)

# Discord IDs (snowflakes) are larger than a slash-command integer can carry
# safely, so they are taken as text — autocomplete makes that invisible to
# the user.
GUILD_HINT = "Pick a server — type to filter"

# Minimum gap between two edits of the progress message.
PROGRESS_INTERVAL = 2.5

# Discord shows at most 25 suggestions.
MAX_CHOICES = 25


class CloneBot(discord.Client):
    """The client with its slash commands registered."""

    def __init__(self, config: Config) -> None:
        # Default intents are enough. The two privileged intents are added only
        # when the matching feature is switched on in .env — otherwise Discord
        # refuses the login until they are enabled in the Developer Portal.

        intents = discord.Intents.none()
        intents.guilds = True
        intents.emojis_and_stickers = True
        if config.enable_message_clone:
            intents.message_content = True
        if config.enable_member_roles:
            intents.members = True

        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        # Only one run at a time is allowed per target server.
        self.active: dict[int, ServerCloner] = {}
        self._greeted = False

        register_commands(self)

    async def setup_hook(self) -> None:
        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands registered for test server %s.", self.config.dev_guild_id)
        else:
            await self.tree.sync()
            log.info("Global slash commands registered (up to 1h propagation delay).")

    async def on_ready(self) -> None:
        # on_ready feuert bei jedem Reconnect erneut — einmalige Aktionen
        # and therefore need guarding.
        if not self._greeted:
            self._greeted = True
            log.info("Eingeloggt als %s (ID %s)", self.user, self.user.id if self.user else "?")
            log.info("Aktiv auf %d Server(n).", len(self.guilds))

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="/help · Server klonen"
            )
        )


# ── Permission checks ─────────────────────────────────────────────────────────


def _resolve_guild(bot: CloneBot, raw: str, label: str = "Server") -> discord.Guild:
    raw = raw.strip()
    if not raw:
        raise CloneError(
            f"No **{label}** was given and no default is configured in `.env`. "
            f"Use `/servers` to see the IDs."
        )
    if not raw.isdigit():
        raise CloneError(
            f"`{raw}` is not a valid server ID. Pick the server from the "
            f"suggestion list instead of typing it."
        )
    guild = bot.get_guild(int(raw))
    if guild is None:
        raise CloneError(
            f"Server `{raw}` not found. Is the bot invited there?\n"
            f"Mit `/servers` siehst du alle erreichbaren Server."
        )
    return guild


async def _authorize(
    bot: CloneBot, interaction: discord.Interaction, target: discord.Guild
) -> None:
    """Only bot owners or administrators of the target server may write."""
    user_id = interaction.user.id

    if not bot.config.is_owner(user_id):
        raise CloneError(
            "You are not on the list of authorised users "
            "(`OWNER_IDS` in `.env`)."
        )

    if target.owner_id == user_id:
        return

    # Without the privileged members intent the member cache is empty,
    # so fall back to asking Discord directly.
    member = target.get_member(user_id)
    if member is None:
        try:
            member = await target.fetch_member(user_id)
        except discord.NotFound:
            raise CloneError(
                f"You are not a member of **{target.name}** and cannot change "
                f"anything there."
            ) from None
        except discord.HTTPException as exc:
            raise CloneError(f"Membership could not be checked: {exc.text or exc}") from None

    if not member.guild_permissions.administrator:
        raise CloneError(
            f"You need **administrator** rights on **{target.name}**, "
            f"to change anything there."
        )


# ── Command-Registrierung ─────────────────────────────────────────────────────


def register_commands(bot: CloneBot) -> None:  # noqa: C901 - one registration site
    tree = bot.tree
    color = bot.config.embed_color

    async def send_error(interaction: discord.Interaction, exc: Exception) -> None:
        embed = embeds.error("That did not work", str(exc))
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not deliver the error message: %s", exc)

    async def guild_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggests the servers the bot is on, so nobody types an ID."""
        if not bot.config.is_owner(interaction.user.id):
            return []

        needle = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for guild in sorted(bot.guilds, key=lambda g: g.name.lower()):
            if needle and needle not in guild.name.lower() and needle not in str(guild.id):
                continue
            me = guild.me
            ready = me is not None and me.guild_permissions.administrator
            # The name is capped at 100 characters; the symbol shows at a
            # glance whether the bot can work there at all.
            label = f"{'👑' if ready else '⚠️'} {guild.name} · {len(guild.channels)} channels"
            choices.append(app_commands.Choice(name=label[:100], value=str(guild.id)))
            if len(choices) >= MAX_CHOICES:
                break
        return choices

    # ── /help ─────────────────────────────────────────────────────────────────

    @tree.command(name="help", description="An overview of every command and the quick start")
    async def help_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=embeds.help_embed(color), ephemeral=True)

    # ── /servers ──────────────────────────────────────────────────────────────

    @tree.command(name="servers", description="Lists every server the bot is on")
    async def servers_cmd(interaction: discord.Interaction) -> None:
        if not bot.config.is_owner(interaction.user.id):
            await send_error(
                interaction, CloneError("You are not on the list of authorised users.")
            )
            return
        await interaction.response.send_message(
            embed=embeds.servers_embed(list(bot.guilds), bot.user, color),  # type: ignore[arg-type]
            ephemeral=True,
        )

    # ── /status ───────────────────────────────────────────────────────────────

    @tree.command(name="status", description="Shows running jobs and saved checkpoints")
    async def status_cmd(interaction: discord.Interaction) -> None:
        lines: list[str] = []
        for guild_id, cloner in bot.active.items():
            guild = bot.get_guild(guild_id)
            p = cloner.progress
            lines.append(
                f"🔄 **{guild.name if guild else guild_id}** — {p.stage.value} "
                f"({p.current}/{p.total or '?'}) · {p.fraction * 100:.0f}%"
            )

        for guild in bot.guilds:
            if guild.id in bot.active:
                continue
            state = CloneState.load(guild.id)
            if state is not None and not state.is_empty:
                minutes = int(state.age_seconds // 60)
                lines.append(
                    f"♻️ **{guild.name}** — interrupted run with "
                    f"{state.object_count} Objekten, vor {minutes} Min. "
                    f"→ `/clone resume:True`"
                )

        if not lines:
            await interaction.response.send_message(
                embed=embeds.info(
                    "💤 Nothing to do",
                    "Nothing is running and there are no open checkpoints.",
                    color=embeds.COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.info("📊 Status", "\n".join(lines), color=color), ephemeral=True
        )

    # ── /preview ──────────────────────────────────────────────────────────────

    @tree.command(
        name="preview",
        description="Dry run: shows what a clone would copy and delete",
    )
    @app_commands.describe(source=GUILD_HINT, target=GUILD_HINT)
    @app_commands.autocomplete(source=guild_autocomplete, target=guild_autocomplete)
    async def preview_cmd(
        interaction: discord.Interaction,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            src = _resolve_guild(
                bot, source or str(bot.config.default_source_id or ""), "source server"
            )
            tgt = _resolve_guild(
                bot, target or str(bot.config.default_target_id or ""), "target server"
            )
            await _authorize(bot, interaction, tgt)
            if src.id == tgt.id:
                raise CloneError("Source and target must not be the same server.")

            # Without images: the preview only needs the numbers and is
            # deutlich schneller.
            blueprint = await GuildBlueprint.from_guild(src, include_images=False)
            est = estimate_duration(blueprint, bot.config.clone_delay, wipe=len(tgt.channels))
            await interaction.followup.send(
                embed=embeds.preview_embed(
                    blueprint,
                    tgt,
                    est,
                    color,
                    source_icon_url=src.icon.url if src.icon else None,
                ),
                ephemeral=True,
            )
        except CloneError as exc:
            await send_error(interaction, exc)

    # ── /backup ───────────────────────────────────────────────────────────────

    @tree.command(name="backup", description="Saves a server to a file")
    @app_commands.describe(
        guild=GUILD_HINT,
        include_images="Include icon, emojis and stickers (default: yes, makes the file larger)",
    )
    @app_commands.autocomplete(guild=guild_autocomplete)
    async def backup_cmd(
        interaction: discord.Interaction,
        guild: str | None = None,
        include_images: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            src = _resolve_guild(
                bot, guild or str(bot.config.default_source_id or ""), "Server"
            )
            # A backup changes nothing, but it exposes the complete permission
            # structure — hence the same bar as cloning.
            await _authorize(bot, interaction, src)
        except CloneError as exc:
            await send_error(interaction, exc)
            return

        blueprint = await GuildBlueprint.from_guild(src, include_images=include_images)
        shot = snapshot.dump(blueprint)
        local = snapshot.save_to_disk(shot)

        if shot.too_large_for_discord:
            await interaction.followup.send(
                embed=embeds.backup_too_large_embed(
                    snapshot.human_size(shot.size),
                    snapshot.human_size(snapshot.DISCORD_UPLOAD_LIMIT),
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=embeds.backup_embed(
                blueprint,
                shot.filename,
                snapshot.human_size(shot.size),
                shot.compressed,
                str(local) if local else None,
                color,
            ),
            file=discord.File(fp=_as_buffer(shot.data), filename=shot.filename),
            ephemeral=True,
        )

    # ── /restore ──────────────────────────────────────────────────────────────

    @tree.command(name="restore", description="Applies a backup file to a server")
    @app_commands.describe(
        file="The file created by /backup",
        target=GUILD_HINT,
        wipe_target="Wipe the target completely first (default: yes)",
    )
    @app_commands.autocomplete(target=guild_autocomplete)
    async def restore_cmd(
        interaction: discord.Interaction,
        file: discord.Attachment,
        target: str | None = None,
        wipe_target: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            tgt = _resolve_guild(
                bot, target or str(bot.config.default_target_id or ""), "target server"
            )
            await _authorize(bot, interaction, tgt)
            if tgt.id in bot.active:
                raise CloneError(f"A job is already running on **{tgt.name}**.")

            if file.size > snapshot.DISCORD_UPLOAD_LIMIT:
                raise CloneError("That file is too large to process.")

            raw = await file.read()
            blueprint = snapshot.load(raw)
        except snapshot.SnapshotError as exc:
            await send_error(interaction, CloneError(str(exc)))
            return
        except (CloneError, discord.HTTPException) as exc:
            await send_error(interaction, exc if isinstance(exc, CloneError) else CloneError(str(exc)))
            return

        options = CloneOptions(wipe_target=wipe_target)
        await _confirm_and_run(
            bot,
            interaction,
            blueprint,
            tgt,
            options,
            title="⚠️ Confirm the restore",
        )

    # ── /sync ─────────────────────────────────────────────────────────────────

    @tree.command(
        name="sync",
        description="Adds only what is missing in the target — nothing is deleted",
    )
    @app_commands.describe(source=GUILD_HINT, target=GUILD_HINT)
    @app_commands.autocomplete(source=guild_autocomplete, target=guild_autocomplete)
    async def sync_cmd(
        interaction: discord.Interaction,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            src, tgt = await _resolve_pair(bot, interaction, source, target)
        except CloneError as exc:
            await send_error(interaction, exc)
            return

        blueprint = await GuildBlueprint.from_guild(src)
        options = CloneOptions(
            wipe_target=False,
            skip_existing=True,
            # A sync must not overwrite the target server's identity.
            settings=False,
            icon=False,
        )
        await _confirm_and_run(
            bot,
            interaction,
            blueprint,
            tgt,
            options,
            source_guild=src,
            title="🔁 Confirm the sync",
        )

    # ── /clone ────────────────────────────────────────────────────────────────

    @tree.command(name="clone", description="Copies one server onto another")
    @app_commands.describe(
        source=GUILD_HINT,
        target=GUILD_HINT,
        wipe_target="Wipe the target completely first (default: yes)",
        resume="Resume an interrupted run instead of starting over",
        roles="Copy roles as well (default: yes)",
        channels="Copy categories and channels as well (default: yes)",
        emojis="Copy emojis as well (default: yes)",
        stickers="Copy stickers as well (default: yes)",
        settings="Copy name, icon and server settings (default: yes)",
        messages="How many messages per channel to re-stage (0 = off)",
        member_roles="Give members present on both servers their roles",
    )
    @app_commands.autocomplete(source=guild_autocomplete, target=guild_autocomplete)
    async def clone_cmd(
        interaction: discord.Interaction,
        source: str | None = None,
        target: str | None = None,
        wipe_target: bool = True,
        resume: bool = False,
        roles: bool = True,
        channels: bool = True,
        emojis: bool = True,
        stickers: bool = True,
        settings: bool = True,
        messages: app_commands.Range[int, 0, 500] = 0,
        member_roles: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            src, tgt = await _resolve_pair(bot, interaction, source, target)

            if messages and not bot.config.enable_message_clone:
                raise CloneError(
                    "Message re-staging is switched off.\n\n"
                    "To enable it: put `ENABLE_MESSAGE_CLONE=true` in `.env` "
                    "**and** switch on the **Message Content Intent** under "
                    "\"Bot\" in the Developer Portal. Then restart the bot."
                )
            if member_roles and not bot.config.enable_member_roles:
                raise CloneError(
                    "Role assignment is switched off.\n\n"
                    "To enable it: put `ENABLE_MEMBER_ROLES=true` in `.env` "
                    "**and** switch on the **Message Content Intent** under "
                    "\"Bot\" in the Developer Portal. Then restart the bot."
                )
        except CloneError as exc:
            await send_error(interaction, exc)
            return

        state: CloneState | None = None
        if resume:
            state = CloneState.load(tgt.id)
            if state is None or state.is_empty:
                await send_error(
                    interaction,
                    CloneError(
                        f"There is no checkpoint to resume for **{tgt.name}**. "
                        f"Start the clone without `resume`."
                    ),
                )
                return

        options = CloneOptions(
            # Wiping again on resume would be exactly wrong: it would delete
            # what the first attempt already achieved.
            wipe_target=wipe_target and not resume,
            settings=settings,
            icon=settings,
            roles=roles,
            channels=channels,
            emojis=emojis,
            stickers=stickers,
            skip_existing=resume,
            messages_per_channel=messages,
            member_roles=member_roles,
        )

        blueprint = await GuildBlueprint.from_guild(src)
        await _confirm_and_run(
            bot, interaction, blueprint, tgt, options, source_guild=src, state=state
        )


# ── Shared flows ──────────────────────────────────────────────────────────────


def _as_buffer(data: bytes):
    import io

    return io.BytesIO(data)


async def _resolve_pair(
    bot: CloneBot,
    interaction: discord.Interaction,
    source: str | None,
    target: str | None,
) -> tuple[discord.Guild, discord.Guild]:
    """Resolves source and target and checks every precondition."""
    src = _resolve_guild(bot, source or str(bot.config.default_source_id or ""), "source server")
    tgt = _resolve_guild(bot, target or str(bot.config.default_target_id or ""), "target server")
    await _authorize(bot, interaction, tgt)

    if src.id == tgt.id:
        raise CloneError(
            "Source and target are the same. That would delete the server "
            "instead of copying it."
        )
    if tgt.id in bot.active:
        raise CloneError(
            f"A job is already running on **{tgt.name}**. "
            f"Use `/status` to see its progress."
        )
    return src, tgt


async def _confirm_and_run(
    bot: CloneBot,
    interaction: discord.Interaction,
    blueprint: GuildBlueprint,
    target: discord.Guild,
    options: CloneOptions,
    *,
    source_guild: discord.Guild | None = None,
    state: CloneState | None = None,
    title: str = "⚠️ Confirm the clone",
) -> None:
    view = ConfirmView(interaction.user.id)
    await interaction.followup.send(
        embed=embeds.confirm_embed(
            blueprint, target, options, title=title, resume_state=state
        ),
        view=view,
        ephemeral=True,
    )
    await view.wait()

    if not view.value:
        return

    await _run_clone(
        bot,
        interaction,
        view.interaction,
        blueprint,
        target,
        options,
        source_guild=source_guild,
        state=state,
    )


async def _run_clone(
    bot: CloneBot,
    interaction: discord.Interaction,
    confirm_interaction: discord.Interaction | None,
    blueprint: GuildBlueprint,
    target: discord.Guild,
    options: CloneOptions,
    *,
    source_guild: discord.Guild | None = None,
    state: CloneState | None = None,
) -> None:
    color = bot.config.embed_color
    source_name = blueprint.name
    last_edit = 0.0
    edit_lock = asyncio.Lock()

    cloner = ServerCloner(
        blueprint,
        target,
        options,
        delay=bot.config.clone_delay,
        reason=f"Easy Server Cloner · started by {interaction.user}",
        state=state,
        source_guild=source_guild,
    )
    bot.active[target.id] = cloner

    if confirm_interaction is not None:
        try:
            await confirm_interaction.response.edit_message(
                embed=embeds.info(
                    "🚀 Started",
                    "Progress is shown below, in the channel.",
                    color=color,
                ),
                view=None,
            )
        except discord.HTTPException:
            pass

    # Progress deliberately goes through an ordinary channel message:
    # interaction tokens expire after 15 minutes, and large servers take longer.
    cancel_view = CancelView(cloner, interaction.user.id)
    first_embed = embeds.progress_embed(cloner.progress, source_name, target, color)
    status_message: discord.Message | None = None

    channel = interaction.channel
    if isinstance(channel, discord.abc.Messageable):
        try:
            status_message = await channel.send(embed=first_embed, view=cancel_view)
        except discord.HTTPException:
            status_message = None
    if status_message is None:
        try:
            status_message = await interaction.followup.send(
                embed=first_embed, view=cancel_view, wait=True
            )
        except discord.HTTPException:
            log.warning("Could not send the progress display.")

    async def on_progress(progress) -> None:
        nonlocal last_edit
        if status_message is None:
            return
        now = time.monotonic()
        if now - last_edit < PROGRESS_INTERVAL or edit_lock.locked():
            return
        async with edit_lock:
            last_edit = now
            try:
                await status_message.edit(
                    embed=embeds.progress_embed(progress, source_name, target, color),
                    view=cancel_view,
                )
            except discord.HTTPException:
                pass  # the display is trimming; the job carries on

    cloner.set_progress_callback(on_progress)

    try:
        await cloner.run()
        final_stage = Stage.DONE
    except asyncio.CancelledError:
        final_stage = Stage.CANCELLED
    except CloneError:
        final_stage = Stage.FAILED
    except Exception:  # pragma: no cover - Sicherheitsnetz
        log.exception("The run aborted unexpectedly")
        final_stage = Stage.FAILED
    finally:
        bot.active.pop(target.id, None)
        cancel_view.stop()

    result = embeds.result_embed(cloner.report, final_stage, source_name, target)
    if status_message is not None:
        async with edit_lock:
            try:
                await status_message.edit(embed=result, view=None)
                return
            except discord.HTTPException:
                pass
    try:
        await interaction.followup.send(embed=result, ephemeral=True)
    except discord.HTTPException:
        log.info("Could not deliver the result: %s", cloner.report)

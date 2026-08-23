"""Der Discord-Bot: Slash-Commands, Berechtigungen und Live-Anzeige."""

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

# Discord-IDs (Snowflakes) sind größer als das, was Slash-Command-Integer
# sicher transportieren. Deshalb werden sie als Text entgegengenommen — die
# Autovervollständigung macht das für den Nutzer unsichtbar.
GUILD_HINT = "Server auswählen — tippe zum Filtern"

# Mindestabstand zwischen zwei Bearbeitungen der Fortschritts-Nachricht.
PROGRESS_INTERVAL = 2.5

# Discord zeigt höchstens 25 Vorschläge an.
MAX_CHOICES = 25


class CloneBot(discord.Client):
    """Client mit registrierten Slash-Commands."""

    def __init__(self, config: Config) -> None:
        # Standard-Intents genügen. Die beiden privilegierten Intents kommen nur
        # dazu, wenn die entsprechende Funktion in der .env eingeschaltet ist —
        # sonst verweigert Discord den Login, solange sie nicht im Developer
        # Portal freigeschaltet sind.
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
        # Pro Ziel-Server darf immer nur ein Vorgang gleichzeitig laufen.
        self.active: dict[int, ServerCloner] = {}
        self._greeted = False

        register_commands(self)

    async def setup_hook(self) -> None:
        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash-Commands für Test-Server %s registriert.", self.config.dev_guild_id)
        else:
            await self.tree.sync()
            log.info("Globale Slash-Commands registriert (bis zu 1h Verzögerung).")

    async def on_ready(self) -> None:
        # on_ready feuert bei jedem Reconnect erneut — einmalige Aktionen
        # müssen deshalb abgesichert werden.
        if not self._greeted:
            self._greeted = True
            log.info("Eingeloggt als %s (ID %s)", self.user, self.user.id if self.user else "?")
            log.info("Aktiv auf %d Server(n).", len(self.guilds))

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="/help · Server klonen"
            )
        )


# ── Berechtigungsprüfung ──────────────────────────────────────────────────────


def _resolve_guild(bot: CloneBot, raw: str, label: str = "Server") -> discord.Guild:
    raw = raw.strip()
    if not raw:
        raise CloneError(
            f"Es wurde kein **{label}** angegeben und in der `.env` ist auch keine "
            f"Vorgabe hinterlegt. Nutze `/servers`, um die IDs zu sehen."
        )
    if not raw.isdigit():
        raise CloneError(
            f"`{raw}` ist keine gültige Server-ID. Wähle den Server aus der "
            f"Vorschlagsliste aus, statt ihn zu tippen."
        )
    guild = bot.get_guild(int(raw))
    if guild is None:
        raise CloneError(
            f"Server `{raw}` nicht gefunden. Ist der Bot dort eingeladen?\n"
            f"Mit `/servers` siehst du alle erreichbaren Server."
        )
    return guild


async def _authorize(
    bot: CloneBot, interaction: discord.Interaction, target: discord.Guild
) -> None:
    """Nur Bot-Owner bzw. Administratoren des Ziel-Servers dürfen schreiben."""
    user_id = interaction.user.id

    if not bot.config.is_owner(user_id):
        raise CloneError(
            "Du stehst nicht auf der Liste der berechtigten Nutzer "
            "(`OWNER_IDS` in der `.env`)."
        )

    if target.owner_id == user_id:
        return

    # Ohne den privilegierten Members-Intent ist der Member-Cache leer,
    # deshalb notfalls direkt bei Discord nachfragen.
    member = target.get_member(user_id)
    if member is None:
        try:
            member = await target.fetch_member(user_id)
        except discord.NotFound:
            raise CloneError(
                f"Du bist kein Mitglied von **{target.name}** und kannst dort "
                f"nichts verändern."
            ) from None
        except discord.HTTPException as exc:
            raise CloneError(f"Mitgliedschaft nicht prüfbar: {exc.text or exc}") from None

    if not member.guild_permissions.administrator:
        raise CloneError(
            f"Du brauchst **Administrator**-Rechte auf **{target.name}**, "
            f"um dort etwas zu verändern."
        )


# ── Command-Registrierung ─────────────────────────────────────────────────────


def register_commands(bot: CloneBot) -> None:  # noqa: C901 - eine Registrierstelle
    tree = bot.tree
    color = bot.config.embed_color

    async def send_error(interaction: discord.Interaction, exc: Exception) -> None:
        embed = embeds.error("Das hat nicht geklappt", str(exc))
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.warning("Fehlermeldung konnte nicht zugestellt werden: %s", exc)

    async def guild_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Schlägt die Server vor, auf denen der Bot ist — kein ID-Tippen nötig."""
        if not bot.config.is_owner(interaction.user.id):
            return []

        needle = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for guild in sorted(bot.guilds, key=lambda g: g.name.lower()):
            if needle and needle not in guild.name.lower() and needle not in str(guild.id):
                continue
            me = guild.me
            ready = me is not None and me.guild_permissions.administrator
            # Der Name ist auf 100 Zeichen begrenzt; das Symbol zeigt sofort,
            # ob der Bot dort überhaupt arbeiten kann.
            label = f"{'👑' if ready else '⚠️'} {guild.name} · {len(guild.channels)} Channels"
            choices.append(app_commands.Choice(name=label[:100], value=str(guild.id)))
            if len(choices) >= MAX_CHOICES:
                break
        return choices

    # ── /help ─────────────────────────────────────────────────────────────────

    @tree.command(name="help", description="Übersicht über alle Befehle und den Schnellstart")
    async def help_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=embeds.help_embed(color), ephemeral=True)

    # ── /servers ──────────────────────────────────────────────────────────────

    @tree.command(name="servers", description="Zeigt alle Server, auf denen der Bot aktiv ist")
    async def servers_cmd(interaction: discord.Interaction) -> None:
        if not bot.config.is_owner(interaction.user.id):
            await send_error(
                interaction, CloneError("Du stehst nicht auf der Liste der berechtigten Nutzer.")
            )
            return
        await interaction.response.send_message(
            embed=embeds.servers_embed(list(bot.guilds), bot.user, color),  # type: ignore[arg-type]
            ephemeral=True,
        )

    # ── /status ───────────────────────────────────────────────────────────────

    @tree.command(name="status", description="Zeigt laufende Vorgänge und gespeicherte Zwischenstände")
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
                    f"♻️ **{guild.name}** — abgebrochener Lauf mit "
                    f"{state.object_count} Objekten, vor {minutes} Min. "
                    f"→ `/clone resume:True`"
                )

        if not lines:
            await interaction.response.send_message(
                embed=embeds.info(
                    "💤 Nichts zu tun",
                    "Es läuft kein Vorgang und es gibt keine offenen Zwischenstände.",
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
        description="Trockenlauf: zeigt, was ein Klon kopieren und löschen würde",
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
                bot, source or str(bot.config.default_source_id or ""), "Quell-Server"
            )
            tgt = _resolve_guild(
                bot, target or str(bot.config.default_target_id or ""), "Ziel-Server"
            )
            await _authorize(bot, interaction, tgt)
            if src.id == tgt.id:
                raise CloneError("Quelle und Ziel dürfen nicht derselbe Server sein.")

            # Ohne Bilder: die Vorschau braucht nur die Zahlen und ist so
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

    @tree.command(name="backup", description="Sichert einen Server als Datei")
    @app_commands.describe(
        guild=GUILD_HINT,
        include_images="Icon, Emojis und Sticker mitsichern (Standard: Ja, macht die Datei größer)",
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
            # Ein Backup verändert nichts, verlangt aber Einblick in die
            # komplette Rechtestruktur — deshalb dieselbe Hürde wie beim Klonen.
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

    @tree.command(name="restore", description="Wendet eine Backup-Datei auf einen Server an")
    @app_commands.describe(
        file="Die mit /backup erstellte Datei",
        target=GUILD_HINT,
        wipe_target="Ziel vorher komplett leeren (Standard: Ja)",
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
                bot, target or str(bot.config.default_target_id or ""), "Ziel-Server"
            )
            await _authorize(bot, interaction, tgt)
            if tgt.id in bot.active:
                raise CloneError(f"Auf **{tgt.name}** läuft bereits ein Vorgang.")

            if file.size > snapshot.DISCORD_UPLOAD_LIMIT:
                raise CloneError("Die Datei ist zu groß, um sie zu verarbeiten.")

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
            title="⚠️ Wiederherstellung bestätigen",
        )

    # ── /sync ─────────────────────────────────────────────────────────────────

    @tree.command(
        name="sync",
        description="Ergänzt im Ziel nur, was fehlt — es wird nichts gelöscht",
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
            # Ein Abgleich soll die Identität des Ziel-Servers nicht überschreiben.
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
            title="🔁 Abgleich bestätigen",
        )

    # ── /clone ────────────────────────────────────────────────────────────────

    @tree.command(name="clone", description="Kopiert einen Server auf einen anderen")
    @app_commands.describe(
        source=GUILD_HINT,
        target=GUILD_HINT,
        wipe_target="Ziel vorher komplett leeren (Standard: Ja)",
        resume="Einen abgebrochenen Lauf fortsetzen statt neu zu beginnen",
        roles="Rollen mitkopieren (Standard: Ja)",
        channels="Kategorien und Channels mitkopieren (Standard: Ja)",
        emojis="Emojis mitkopieren (Standard: Ja)",
        stickers="Sticker mitkopieren (Standard: Ja)",
        settings="Name, Icon und Server-Einstellungen übernehmen (Standard: Ja)",
        messages="Wie viele Nachrichten je Channel nachbauen (0 = aus)",
        member_roles="Mitgliedern auf beiden Servern ihre Rollen zuweisen",
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
                    "Der Nachrichten-Nachbau ist ausgeschaltet.\n\n"
                    "Zum Aktivieren: `ENABLE_MESSAGE_CLONE=true` in die `.env` "
                    "eintragen **und** im Developer Portal unter „Bot“ den "
                    "**Message Content Intent** einschalten. Danach den Bot neu starten."
                )
            if member_roles and not bot.config.enable_member_roles:
                raise CloneError(
                    "Die Rollen-Zuweisung ist ausgeschaltet.\n\n"
                    "Zum Aktivieren: `ENABLE_MEMBER_ROLES=true` in die `.env` "
                    "eintragen **und** im Developer Portal unter „Bot“ den "
                    "**Server Members Intent** einschalten. Danach den Bot neu starten."
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
                        f"Für **{tgt.name}** gibt es keinen Zwischenstand zum Fortsetzen. "
                        f"Starte den Klon ohne `resume`."
                    ),
                )
                return

        options = CloneOptions(
            # Beim Fortsetzen wäre erneutes Leeren genau falsch: es würde das
            # löschen, was der erste Anlauf schon geschafft hat.
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


# ── Gemeinsame Abläufe ────────────────────────────────────────────────────────


def _as_buffer(data: bytes):
    import io

    return io.BytesIO(data)


async def _resolve_pair(
    bot: CloneBot,
    interaction: discord.Interaction,
    source: str | None,
    target: str | None,
) -> tuple[discord.Guild, discord.Guild]:
    """Löst Quelle und Ziel auf und prüft alle Vorbedingungen."""
    src = _resolve_guild(bot, source or str(bot.config.default_source_id or ""), "Quell-Server")
    tgt = _resolve_guild(bot, target or str(bot.config.default_target_id or ""), "Ziel-Server")
    await _authorize(bot, interaction, tgt)

    if src.id == tgt.id:
        raise CloneError(
            "Quelle und Ziel sind identisch. Das würde den Server löschen "
            "statt ihn zu kopieren."
        )
    if tgt.id in bot.active:
        raise CloneError(
            f"Auf **{tgt.name}** läuft bereits ein Vorgang. "
            f"Mit `/status` siehst du den Fortschritt."
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
    title: str = "⚠️ Klon-Vorgang bestätigen",
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
        reason=f"Easy Server Cloner · gestartet von {interaction.user}",
        state=state,
        source_guild=source_guild,
    )
    bot.active[target.id] = cloner

    if confirm_interaction is not None:
        try:
            await confirm_interaction.response.edit_message(
                embed=embeds.info(
                    "🚀 Gestartet",
                    "Der Fortschritt wird unten im Kanal angezeigt.",
                    color=color,
                ),
                view=None,
            )
        except discord.HTTPException:
            pass

    # Der Fortschritt läuft absichtlich über eine normale Kanal-Nachricht:
    # Interaction-Tokens verfallen nach 15 Minuten, große Server brauchen länger.
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
            log.warning("Fortschrittsanzeige konnte nicht gesendet werden.")

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
                pass  # Anzeige ist Beiwerk, der Vorgang läuft weiter

    cloner.set_progress_callback(on_progress)

    try:
        await cloner.run()
        final_stage = Stage.DONE
    except asyncio.CancelledError:
        final_stage = Stage.CANCELLED
    except CloneError:
        final_stage = Stage.FAILED
    except Exception:  # pragma: no cover - Sicherheitsnetz
        log.exception("Vorgang unerwartet abgebrochen")
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
        log.info("Ergebnis konnte nicht zugestellt werden: %s", cloner.report)

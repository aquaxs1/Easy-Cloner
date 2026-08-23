"""Der Discord-Bot: Slash-Commands, Berechtigungen und Live-Anzeige."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands

from . import embeds
from .cloner import CloneError, ServerCloner, estimate_duration, preview_counts
from .config import Config
from .models import CloneOptions, Stage
from .views import CancelView, ConfirmView

log = logging.getLogger(__name__)

# Discord-IDs (Snowflakes) sind größer als das, was Slash-Command-Integer
# sicher transportieren. Deshalb werden sie als Text entgegengenommen.
GUILD_ID_HINT = "Server-ID (18–20 Ziffern) — findest du über /servers"

# Mindestabstand zwischen zwei Bearbeitungen der Fortschritts-Nachricht.
# Discord limitiert Message-Edits; häufigeres Aktualisieren bringt nichts.
PROGRESS_INTERVAL = 2.5


class CloneBot(discord.Client):
    """Client mit registrierten Slash-Commands."""

    def __init__(self, config: Config) -> None:
        # Der Bot liest keine Nachrichten und braucht keine Mitgliederliste —
        # die Standard-Intents genügen und erfordern keine Portal-Freischaltung.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.emojis_and_stickers = True

        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        # Pro Ziel-Server darf immer nur ein Klon gleichzeitig laufen.
        self.active: dict[int, ServerCloner] = {}
        self._synced = False

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
        if not self._synced:
            self._synced = True
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
            f"Vorgabe hinterlegt. Nutze `/servers`, um die passende ID zu finden."
        )
    if not raw.isdigit():
        raise CloneError(
            f"`{raw}` ist keine gültige Server-ID. Erwartet werden nur Ziffern — "
            f"nutze `/servers`, um die IDs anzuzeigen."
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
    """Nur Bot-Owner bzw. Administratoren des Ziel-Servers dürfen klonen."""
    user_id = interaction.user.id

    if not bot.config.is_owner(user_id):
        raise CloneError(
            "Du stehst nicht auf der Liste der berechtigten Nutzer "
            "(`OWNER_IDS` in der `.env`)."
        )

    if target.owner_id == user_id:
        return

    # Ohne den (privilegierten) Members-Intent ist der Member-Cache leer,
    # deshalb notfalls direkt bei Discord nachfragen.
    member = target.get_member(user_id)
    if member is None:
        try:
            member = await target.fetch_member(user_id)
        except discord.NotFound:
            raise CloneError(
                f"Du bist kein Mitglied von **{target.name}** und kannst dort "
                f"keinen Klon starten."
            ) from None
        except discord.HTTPException as exc:
            raise CloneError(f"Mitgliedschaft nicht prüfbar: {exc.text or exc}") from None

    if not member.guild_permissions.administrator:
        raise CloneError(
            f"Du brauchst **Administrator**-Rechte auf **{target.name}**, "
            f"um dort einen Klon zu starten."
        )


# ── Command-Registrierung ─────────────────────────────────────────────────────


def register_commands(bot: CloneBot) -> None:
    tree = bot.tree
    color = bot.config.embed_color

    async def send_error(interaction: discord.Interaction, exc: Exception) -> None:
        embed = embeds.error("Das hat nicht geklappt", str(exc))
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /help ─────────────────────────────────────────────────────────────────

    @tree.command(name="help", description="Übersicht über alle Befehle und den Schnellstart")
    async def help_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=embeds.help_embed(color), ephemeral=True
        )

    # ── /servers ──────────────────────────────────────────────────────────────

    @tree.command(
        name="servers", description="Zeigt alle Server, auf denen der Bot aktiv ist"
    )
    async def servers_cmd(interaction: discord.Interaction) -> None:
        if not bot.config.is_owner(interaction.user.id):
            await send_error(
                interaction,
                CloneError("Du stehst nicht auf der Liste der berechtigten Nutzer."),
            )
            return
        await interaction.response.send_message(
            embed=embeds.servers_embed(list(bot.guilds), bot.user, color),  # type: ignore[arg-type]
            ephemeral=True,
        )

    # ── /status ───────────────────────────────────────────────────────────────

    @tree.command(name="status", description="Zeigt laufende Klon-Vorgänge")
    async def status_cmd(interaction: discord.Interaction) -> None:
        if not bot.active:
            await interaction.response.send_message(
                embed=embeds.info(
                    "💤 Nichts zu tun",
                    "Aktuell läuft kein Klon-Vorgang.",
                    color=embeds.COLOR_MUTED,
                ),
                ephemeral=True,
            )
            return

        lines = []
        for guild_id, cloner in bot.active.items():
            guild = bot.get_guild(guild_id)
            name = guild.name if guild else str(guild_id)
            p = cloner.progress
            lines.append(
                f"**{name}** — {p.stage.value} "
                f"({p.current}/{p.total or '?'}) · {p.fraction * 100:.0f}%"
            )
        await interaction.response.send_message(
            embed=embeds.info("🔄 Laufende Vorgänge", "\n".join(lines), color=color),
            ephemeral=True,
        )

    # ── /preview ──────────────────────────────────────────────────────────────

    @tree.command(
        name="preview",
        description="Trockenlauf: zeigt, was ein Klon kopieren und löschen würde",
    )
    @app_commands.describe(source=GUILD_ID_HINT, target=GUILD_ID_HINT)
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

            counts = preview_counts(src)
            est = estimate_duration(counts.values(), bot.config.clone_delay)
            est += len(tgt.channels) * bot.config.clone_delay
            await interaction.followup.send(
                embed=embeds.preview_embed(src, tgt, counts, est, color), ephemeral=True
            )
        except CloneError as exc:
            await send_error(interaction, exc)

    # ── /clone ────────────────────────────────────────────────────────────────

    @tree.command(name="clone", description="Kopiert einen Server auf einen anderen")
    @app_commands.describe(
        source=GUILD_ID_HINT,
        target=GUILD_ID_HINT,
        wipe_target="Ziel vorher komplett leeren (Standard: Ja)",
        roles="Rollen mitkopieren (Standard: Ja)",
        channels="Kategorien und Channels mitkopieren (Standard: Ja)",
        emojis="Emojis mitkopieren (Standard: Ja)",
        stickers="Sticker mitkopieren (Standard: Ja)",
        settings="Name, Icon und Server-Einstellungen übernehmen (Standard: Ja)",
    )
    async def clone_cmd(
        interaction: discord.Interaction,
        source: str | None = None,
        target: str | None = None,
        wipe_target: bool = True,
        roles: bool = True,
        channels: bool = True,
        emojis: bool = True,
        stickers: bool = True,
        settings: bool = True,
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
                raise CloneError(
                    "Quelle und Ziel sind identisch. Das würde den Server löschen "
                    "statt ihn zu kopieren."
                )
            if tgt.id in bot.active:
                raise CloneError(
                    f"Auf **{tgt.name}** läuft bereits ein Klon-Vorgang. "
                    f"Mit `/status` siehst du den Fortschritt."
                )
        except CloneError as exc:
            await send_error(interaction, exc)
            return

        options = CloneOptions(
            wipe_target=wipe_target,
            settings=settings,
            icon=settings,
            roles=roles,
            channels=channels,
            emojis=emojis,
            stickers=stickers,
        )
        counts = preview_counts(src)

        view = ConfirmView(interaction.user.id)
        await interaction.followup.send(
            embed=embeds.confirm_embed(src, tgt, options, counts),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.value:
            return

        await _run_clone(bot, interaction, view.interaction, src, tgt, options)


# ── Ausführung mit Live-Anzeige ───────────────────────────────────────────────


async def _run_clone(
    bot: CloneBot,
    interaction: discord.Interaction,
    confirm_interaction: discord.Interaction | None,
    source: discord.Guild,
    target: discord.Guild,
    options: CloneOptions,
) -> None:
    color = bot.config.embed_color
    last_edit = 0.0
    edit_lock = asyncio.Lock()

    cloner = ServerCloner(
        source,
        target,
        options,
        delay=bot.config.clone_delay,
        reason=f"Easy Server Cloner · gestartet von {interaction.user}",
    )
    bot.active[target.id] = cloner

    # Der Fortschritt läuft absichtlich über eine normale Kanal-Nachricht:
    # Interaction-Tokens verfallen nach 15 Minuten, große Server brauchen länger.
    status_message: discord.Message | None = None
    cancel_view = CancelView(cloner, interaction.user.id)
    first_embed = embeds.progress_embed(cloner.progress, source, target, color)

    if confirm_interaction is not None:
        try:
            await confirm_interaction.response.edit_message(
                embed=embeds.info(
                    "🚀 Klon gestartet",
                    "Der Fortschritt wird unten im Kanal angezeigt.",
                    color=color,
                ),
                view=None,
            )
        except discord.HTTPException:
            pass

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
        if now - last_edit < PROGRESS_INTERVAL:
            return
        if edit_lock.locked():
            return
        async with edit_lock:
            last_edit = now
            try:
                await status_message.edit(
                    embed=embeds.progress_embed(progress, source, target, color),
                    view=cancel_view,
                )
            except discord.HTTPException:
                pass  # Anzeige ist Beiwerk, der Klon läuft weiter

    cloner.set_progress_callback(on_progress)

    try:
        await cloner.run()
        final_stage = Stage.DONE
    except asyncio.CancelledError:
        final_stage = Stage.CANCELLED
    except CloneError:
        final_stage = Stage.FAILED
    except Exception:  # pragma: no cover - Sicherheitsnetz
        log.exception("Klon-Vorgang unerwartet abgebrochen")
        final_stage = Stage.FAILED
    finally:
        bot.active.pop(target.id, None)
        cancel_view.stop()

    result = embeds.result_embed(cloner.report, final_stage, source, target)
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

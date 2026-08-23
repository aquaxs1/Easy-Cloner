"""Alle Embeds des Bots an einer Stelle — damit das Design konsistent bleibt."""

from __future__ import annotations

import discord

from .models import STAGE_ORDER, CloneOptions, CloneReport, Progress, Stage

BRAND = "Easy Server Cloner"
BRAND_ICON = "🧬"

COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_MUTED = 0x2B2D31

BAR_FILLED = "█"
BAR_EMPTY = "░"
BAR_WIDTH = 18


def progress_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return f"`{BAR_FILLED * filled}{BAR_EMPTY * (width - filled)}` **{fraction * 100:.0f}%**"


def _footer(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=f"{BRAND_ICON} {BRAND}")
    return embed


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _truncate(lines: list[str], limit: int = 1000) -> str:
    """Kürzt eine Liste so, dass sie in ein Embed-Feld passt."""
    out: list[str] = []
    length = 0
    for line in lines:
        if length + len(line) + 1 > limit:
            out.append(f"… und {len(lines) - len(out)} weitere")
            break
        out.append(line)
        length += len(line) + 1
    return "\n".join(out) or "—"


# ── Allgemein ─────────────────────────────────────────────────────────────────


def error(title: str, description: str) -> discord.Embed:
    return _footer(
        discord.Embed(title=f"❌ {title}", description=description, color=COLOR_DANGER)
    )


def info(title: str, description: str, color: int = COLOR_INFO) -> discord.Embed:
    return _footer(discord.Embed(title=title, description=description, color=color))


# ── /help ─────────────────────────────────────────────────────────────────────


def help_embed(color: int = COLOR_INFO) -> discord.Embed:
    embed = discord.Embed(
        title=f"{BRAND_ICON} {BRAND}",
        description=(
            "Kopiert die komplette Struktur eines Discord-Servers auf einen "
            "anderen: Rollen, Kategorien, Channels, Rechte, Emojis und Sticker."
        ),
        color=color,
    )
    embed.add_field(
        name="📋 Befehle",
        value=(
            "**`/clone`** — Server klonen (mit Sicherheitsabfrage)\n"
            "**`/preview`** — Vorschau: was würde kopiert werden?\n"
            "**`/servers`** — Alle Server auflisten, auf denen der Bot ist\n"
            "**`/status`** — Läuft gerade ein Klon-Vorgang?\n"
            "**`/help`** — Diese Übersicht"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚀 Schnellstart",
        value=(
            "1. Bot auf **beide** Server einladen (Quelle & Ziel)\n"
            "2. `/servers` ausführen und beide Server-IDs kopieren\n"
            "3. `/preview` für einen Trockenlauf\n"
            "4. `/clone` ausführen und bestätigen"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Wichtig zu wissen",
        value=(
            "• Der Bot braucht auf beiden Servern **Administrator**\n"
            "• Kopierte Rollen landen immer **unterhalb** der Bot-Rolle — "
            "schiebe sie also möglichst weit nach oben\n"
            "• `wipe_target: True` **löscht** alle Channels und Rollen im Ziel\n"
            "• **Nachrichten und Mitglieder** lassen sich technisch nicht klonen"
        ),
        inline=False,
    )
    return _footer(embed)


# ── /preview ──────────────────────────────────────────────────────────────────


def preview_embed(
    source: discord.Guild,
    target: discord.Guild,
    counts: dict[str, int],
    est_seconds: float,
    color: int = COLOR_INFO,
) -> discord.Embed:
    embed = discord.Embed(
        title="🔍 Vorschau — es wird noch nichts verändert",
        description=(
            f"**Quelle** → {source.name} `{source.id}`\n"
            f"**Ziel** → {target.name} `{target.id}`"
        ),
        color=color,
    )
    if source.icon:
        embed.set_thumbnail(url=source.icon.url)

    embed.add_field(
        name="📦 Wird kopiert",
        value="\n".join(f"`{value:>4}` × {label}" for label, value in counts.items()),
        inline=True,
    )
    embed.add_field(
        name="🗑️ Wird im Ziel gelöscht",
        value=(
            f"`{len(target.channels):>4}` × Channels\n"
            f"`{len([r for r in target.roles if not r.is_default() and not r.managed]):>4}` × Rollen"
        ),
        inline=True,
    )
    embed.add_field(
        name="⏱️ Geschätzte Dauer",
        value=f"ca. **{_fmt_duration(est_seconds)}**",
        inline=False,
    )
    embed.add_field(
        name="🚫 Nicht übertragbar",
        value=(
            "Nachrichten · Mitglieder · Rollen-Zuweisungen · "
            "Webhooks · Bots · Server-Boosts · Audit-Log"
        ),
        inline=False,
    )
    return _footer(embed)


# ── Bestätigung ───────────────────────────────────────────────────────────────


def confirm_embed(
    source: discord.Guild,
    target: discord.Guild,
    options: CloneOptions,
    counts: dict[str, int],
) -> discord.Embed:
    total = sum(counts.values())
    embed = discord.Embed(
        title="⚠️ Klon-Vorgang bestätigen",
        description=(
            f"Du bist dabei, **{target.name}** mit dem Inhalt von "
            f"**{source.name}** zu überschreiben."
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(
        name="📥 Quelle",
        value=f"{source.name}\n`{source.id}`\n{total} Objekte",
        inline=True,
    )
    embed.add_field(
        name="📤 Ziel",
        value=f"{target.name}\n`{target.id}`",
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(name="⚙️ Optionen", value="\n".join(options.summary()), inline=True)

    if options.wipe_target:
        embed.add_field(
            name="🗑️ Achtung: Ziel wird geleert",
            value=(
                f"**{len(target.channels)} Channels** und "
                f"**{len([r for r in target.roles if not r.is_default() and not r.managed])} "
                f"Rollen** werden **unwiderruflich gelöscht**."
            ),
            inline=True,
        )
    else:
        embed.add_field(
            name="➕ Modus",
            value="Bestehende Inhalte im Ziel bleiben erhalten.",
            inline=True,
        )

    embed.add_field(
        name="​",
        value="Bestätige innerhalb von **60 Sekunden**, sonst wird abgebrochen.",
        inline=False,
    )
    return _footer(embed)


# ── Live-Fortschritt ──────────────────────────────────────────────────────────


def progress_embed(
    progress: Progress,
    source: discord.Guild,
    target: discord.Guild,
    color: int = COLOR_INFO,
) -> discord.Embed:
    stage = progress.stage
    report = progress.report

    embed = discord.Embed(
        title="🔄 Klon-Vorgang läuft…",
        description=(
            f"**{source.name}** → **{target.name}**\n\n"
            f"{progress_bar(progress.fraction)}"
        ),
        color=color,
    )

    counter = f" · {progress.current}/{progress.total}" if progress.total else ""
    embed.add_field(
        name=f"Schritt {progress.stage_index}/{len(STAGE_ORDER)}",
        value=f"**{stage.value}**{counter}\n{progress.detail or '…'}",
        inline=False,
    )

    created = [
        ("🎭 Rollen", report.roles_created),
        ("📁 Kategorien", report.categories_created),
        ("💬 Text", report.text_channels_created),
        ("🔊 Voice", report.voice_channels_created),
        ("😀 Emojis", report.emojis_created),
        ("🏷️ Sticker", report.stickers_created),
    ]
    embed.add_field(
        name="✅ Bisher erstellt",
        value=" · ".join(f"{label} `{n}`" for label, n in created if n) or "—",
        inline=False,
    )

    if report.warning_count or report.error_count:
        embed.add_field(
            name="📋 Meldungen",
            value=f"⚠️ `{report.warning_count}` Warnungen · ❌ `{report.error_count}` Fehler",
            inline=False,
        )

    return _footer(embed)


# ── Ergebnis ──────────────────────────────────────────────────────────────────


def result_embed(
    report: CloneReport,
    stage: Stage,
    source: discord.Guild,
    target: discord.Guild,
) -> discord.Embed:
    if stage is Stage.CANCELLED:
        title, color = "🛑 Klon-Vorgang abgebrochen", COLOR_WARNING
    elif stage is Stage.FAILED:
        title, color = "❌ Klon-Vorgang fehlgeschlagen", COLOR_DANGER
    elif report.error_count:
        title, color = "⚠️ Klon-Vorgang mit Fehlern beendet", COLOR_WARNING
    else:
        title, color = "✅ Klon-Vorgang abgeschlossen", COLOR_SUCCESS

    embed = discord.Embed(
        title=title,
        description=(
            f"**{source.name}** → **{target.name}**\n"
            f"Dauer: **{_fmt_duration(report.duration)}** · "
            f"**{report.total_created}** Objekte erstellt"
        ),
        color=color,
    )
    if target.icon:
        embed.set_thumbnail(url=target.icon.url)

    embed.add_field(
        name="📊 Erstellt",
        value=(
            f"🎭 Rollen `{report.roles_created}`\n"
            f"📁 Kategorien `{report.categories_created}`\n"
            f"💬 Text-Channels `{report.text_channels_created}`\n"
            f"🔊 Voice-Channels `{report.voice_channels_created}`\n"
            f"🎙️ Bühnen `{report.stage_channels_created}`\n"
            f"🗣️ Foren `{report.forum_channels_created}`\n"
            f"😀 Emojis `{report.emojis_created}`\n"
            f"🏷️ Sticker `{report.stickers_created}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🗑️ Gelöscht",
        value=(
            f"Channels `{report.channels_deleted}`\n"
            f"Rollen `{report.roles_deleted}`"
        ),
        inline=True,
    )

    if report.errors:
        embed.add_field(
            name=f"❌ Fehler ({report.error_count})",
            value=_truncate([f"• {e}" for e in report.errors]),
            inline=False,
        )
    if report.warnings:
        embed.add_field(
            name=f"⚠️ Hinweise ({report.warning_count})",
            value=_truncate([f"• {w}" for w in report.warnings]),
            inline=False,
        )

    if stage is Stage.DONE and not report.error_count:
        embed.add_field(
            name="💡 Nächste Schritte",
            value=(
                "• Rollen-Rangfolge prüfen — sie liegt komplett unter der Bot-Rolle\n"
                "• Mitgliedern ihre Rollen neu zuweisen\n"
                "• Community-Funktionen (Regeln, Onboarding) manuell aktivieren"
            ),
            inline=False,
        )

    return _footer(embed)


# ── /servers ──────────────────────────────────────────────────────────────────


def servers_embed(
    guilds: list[discord.Guild], bot_user: discord.abc.User, color: int = COLOR_INFO
) -> discord.Embed:
    embed = discord.Embed(
        title="🌐 Verfügbare Server",
        description=(
            f"**{bot_user}** ist auf **{len(guilds)}** Server(n).\n"
            "Kopiere die IDs für `/clone` und `/preview`."
            if guilds
            else "Der Bot ist auf keinem Server. Lade ihn zuerst ein."
        ),
        color=color,
    )
    for guild in sorted(guilds, key=lambda g: g.name.lower())[:24]:
        me = guild.me
        admin = me is not None and me.guild_permissions.administrator
        embed.add_field(
            name=f"{'👑' if admin else '⚠️'} {guild.name}",
            value=(
                f"`{guild.id}`\n"
                f"{len(guild.channels)} Channels · {len(guild.roles)} Rollen\n"
                f"{'Bereit' if admin else 'Kein Administrator'}"
            ),
            inline=True,
        )
    if len(guilds) > 24:
        embed.add_field(
            name="​", value=f"… und {len(guilds) - 24} weitere", inline=False
        )
    return _footer(embed)

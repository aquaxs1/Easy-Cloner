"""
Alle Embeds des Bots an einer Stelle — damit das Design konsistent bleibt.

Die Funktionen nehmen bewusst einen *Namen* als Quelle entgegen, keinen
`discord.Guild`: beim Wiederherstellen aus einer Backup-Datei gibt es keinen
Quell-Server mehr, wohl aber einen Bauplan mit Namen.
"""

from __future__ import annotations

import discord

from .blueprint import GuildBlueprint
from .models import STAGE_ORDER, CloneOptions, CloneReport, Progress, Stage
from .state import CloneState

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


def _deletable(guild: discord.Guild) -> tuple[int, int]:
    """Wie viele Channels und Rollen im Ziel gelöscht würden."""
    roles = [r for r in guild.roles if not r.is_default() and not r.managed]
    return len(guild.channels), len(roles)


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
            "Kopiert die komplette Struktur eines Discord-Servers: Rollen, "
            "Kategorien, Channels, Rechte, Emojis und Sticker — live von Server "
            "zu Server oder über eine Backup-Datei."
        ),
        color=color,
    )
    embed.add_field(
        name="📋 Befehle",
        value=(
            "**`/clone`** — Server auf einen anderen kopieren\n"
            "**`/sync`** — nur Fehlendes ergänzen, nichts löschen\n"
            "**`/backup`** — Server als Datei sichern\n"
            "**`/restore`** — Backup-Datei auf einen Server anwenden\n"
            "**`/preview`** — Trockenlauf, verändert nichts\n"
            "**`/servers`** · **`/status`** · **`/help`**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚀 Schnellstart",
        value=(
            "1. Bot auf **beide** Server einladen (Quelle & Ziel)\n"
            "2. `/backup` auf dem Quell-Server — erst sichern\n"
            "3. `/preview` für den Trockenlauf\n"
            "4. `/clone` ausführen und bestätigen\n\n"
            "IDs tippen musst du nicht: die Server-Felder schlagen dir alle "
            "erreichbaren Server zur Auswahl vor."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Wichtig zu wissen",
        value=(
            "• Der Bot braucht auf beiden Servern **Administrator**\n"
            "• Kopierte Rollen landen immer **unterhalb** der Bot-Rolle — "
            "schiebe sie möglichst weit nach oben\n"
            "• `wipe_target: True` **löscht** alle Channels und Rollen im Ziel\n"
            "• Ein Abbruch kostet keine Arbeit: `/clone resume:True` macht weiter\n"
            "• **Mitglieder** lassen sich nicht klonen, Nachrichten nur nachbauen"
        ),
        inline=False,
    )
    return _footer(embed)


# ── /preview ──────────────────────────────────────────────────────────────────


def preview_embed(
    blueprint: GuildBlueprint,
    target: discord.Guild,
    est_seconds: float,
    color: int = COLOR_INFO,
    *,
    source_icon_url: str | None = None,
) -> discord.Embed:
    channels, roles = _deletable(target)
    embed = discord.Embed(
        title="🔍 Vorschau — es wird noch nichts verändert",
        description=(
            f"**Quelle** → {blueprint.name}\n"
            f"**Ziel** → {target.name} `{target.id}`"
        ),
        color=color,
    )
    if source_icon_url:
        embed.set_thumbnail(url=source_icon_url)

    embed.add_field(
        name="📦 Wird kopiert",
        value="\n".join(f"`{value:>4}` × {label}" for label, value in blueprint.counts().items()),
        inline=True,
    )
    embed.add_field(
        name="🗑️ Wird im Ziel gelöscht",
        value=f"`{channels:>4}` × Channels\n`{roles:>4}` × Rollen",
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
            "Mitglieder · Bots · Webhooks · Server-Boosts · Audit-Log · "
            "Reaktionen und Threads"
        ),
        inline=False,
    )
    return _footer(embed)


# ── Bestätigung ───────────────────────────────────────────────────────────────


def confirm_embed(
    blueprint: GuildBlueprint,
    target: discord.Guild,
    options: CloneOptions,
    *,
    title: str = "⚠️ Klon-Vorgang bestätigen",
    resume_state: CloneState | None = None,
) -> discord.Embed:
    channels, roles = _deletable(target)
    embed = discord.Embed(
        title=title,
        description=(
            f"Du bist dabei, **{target.name}** mit dem Inhalt von "
            f"**{blueprint.name}** zu überschreiben."
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(
        name="📥 Quelle",
        value=f"{blueprint.name}\n{blueprint.total_objects} Objekte",
        inline=True,
    )
    embed.add_field(name="📤 Ziel", value=f"{target.name}\n`{target.id}`", inline=True)
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(name="⚙️ Optionen", value="\n".join(options.summary()), inline=True)

    if resume_state is not None:
        embed.add_field(
            name="♻️ Fortsetzung",
            value=(
                f"**{resume_state.object_count} Objekte** aus einem abgebrochenen "
                f"Lauf werden übersprungen. Es wird **nichts gelöscht**."
            ),
            inline=True,
        )
    elif options.wipe_target:
        embed.add_field(
            name="🗑️ Achtung: Ziel wird geleert",
            value=(
                f"**{channels} Channels** und **{roles} Rollen** werden "
                f"**unwiderruflich gelöscht**."
            ),
            inline=True,
        )
    else:
        embed.add_field(
            name="➕ Modus",
            value="Bestehende Inhalte im Ziel bleiben erhalten.",
            inline=True,
        )

    if options.messages_per_channel:
        embed.add_field(
            name="💬 Nachrichten",
            value=(
                f"Die letzten **{options.messages_per_channel}** Nachrichten je "
                f"Channel werden per Webhook **nachgestellt** — nicht migriert. "
                f"Autor und Avatar sehen echt aus, sind es aber nicht."
            ),
            inline=False,
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
    source_name: str,
    target: discord.Guild,
    color: int = COLOR_INFO,
) -> discord.Embed:
    report = progress.report

    embed = discord.Embed(
        title="🔄 Vorgang läuft…",
        description=(
            f"**{source_name}** → **{target.name}**\n\n{progress_bar(progress.fraction)}"
        ),
        color=color,
    )

    counter = f" · {progress.current}/{progress.total}" if progress.total else ""
    embed.add_field(
        name=f"Schritt {progress.stage_index}/{len(STAGE_ORDER)}",
        value=f"**{progress.stage.value}**{counter}\n{progress.detail or '…'}",
        inline=False,
    )

    created = [
        ("🎭 Rollen", report.roles_created),
        ("📁 Kategorien", report.categories_created),
        ("💬 Text", report.text_channels_created),
        ("🔊 Voice", report.voice_channels_created),
        ("😀 Emojis", report.emojis_created),
        ("🏷️ Sticker", report.stickers_created),
        ("✉️ Nachrichten", report.messages_copied),
        ("👥 Mitglieder", report.members_updated),
    ]
    embed.add_field(
        name="✅ Bisher erledigt",
        value=" · ".join(f"{label} `{n}`" for label, n in created if n) or "—",
        inline=False,
    )

    footer_bits = []
    if report.warning_count or report.error_count:
        footer_bits.append(
            f"⚠️ `{report.warning_count}` Warnungen · ❌ `{report.error_count}` Fehler"
        )
    if progress.throttled:
        footer_bits.append(
            f"🐢 Discord bremst — Pause automatisch auf `{progress.delay:.2f}s` erhöht"
        )
    if footer_bits:
        embed.add_field(name="📋 Meldungen", value="\n".join(footer_bits), inline=False)

    return _footer(embed)


# ── Ergebnis ──────────────────────────────────────────────────────────────────


def result_embed(
    report: CloneReport,
    stage: Stage,
    source_name: str,
    target: discord.Guild,
) -> discord.Embed:
    if stage is Stage.CANCELLED:
        title, color = "🛑 Vorgang abgebrochen", COLOR_WARNING
    elif stage is Stage.FAILED:
        title, color = "❌ Vorgang fehlgeschlagen", COLOR_DANGER
    elif report.error_count:
        title, color = "⚠️ Vorgang mit Fehlern beendet", COLOR_WARNING
    else:
        title, color = "✅ Vorgang abgeschlossen", COLOR_SUCCESS

    embed = discord.Embed(
        title=title,
        description=(
            f"**{source_name}** → **{target.name}**\n"
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

    extra = [
        f"Channels gelöscht `{report.channels_deleted}`",
        f"Rollen gelöscht `{report.roles_deleted}`",
    ]
    if report.messages_copied:
        extra.append(f"Nachrichten `{report.messages_copied}`")
    if report.members_updated:
        extra.append(f"Mitglieder `{report.members_updated}`")
    if report.throttle_events:
        extra.append(f"Rate-Limit `{report.throttle_events}×`")
    embed.add_field(name="🔧 Weiteres", value="\n".join(extra), inline=True)

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

    if stage is Stage.CANCELLED:
        embed.add_field(
            name="♻️ Weitermachen",
            value="`/clone resume:True` setzt genau hier fort — nichts geht verloren.",
            inline=False,
        )
    elif stage is Stage.DONE and not report.error_count:
        embed.add_field(
            name="💡 Nächste Schritte",
            value=(
                "• Rollen-Rangfolge prüfen — sie liegt komplett unter der Bot-Rolle\n"
                "• Mitgliedern ihre Rollen zuweisen (`/clone member_roles:True`)\n"
                "• Community-Funktionen (Regeln, Onboarding) manuell aktivieren"
            ),
            inline=False,
        )

    return _footer(embed)


# ── /backup ───────────────────────────────────────────────────────────────────


def backup_embed(
    blueprint: GuildBlueprint,
    filename: str,
    size_text: str,
    compressed: bool,
    local_path: str | None,
    color: int = COLOR_INFO,
) -> discord.Embed:
    embed = discord.Embed(
        title="💾 Backup erstellt",
        description=(
            f"**{blueprint.name}** wurde vollständig gesichert.\n"
            f"Mit `/restore` lässt sich daraus jederzeit ein Server aufbauen."
        ),
        color=COLOR_SUCCESS,
    )
    embed.add_field(
        name="📦 Inhalt",
        value="\n".join(
            f"`{value:>4}` × {label}" for label, value in blueprint.counts().items()
        ),
        inline=True,
    )
    embed.add_field(
        name="📄 Datei",
        value=(
            f"`{filename}`\n"
            f"Größe: **{size_text}**\n"
            f"{'gzip-komprimiert' if compressed else 'Klartext-JSON'}\n"
            f"{'Bilder enthalten' if blueprint.has_images else 'ohne Bilder'}"
        ),
        inline=True,
    )
    if local_path:
        embed.add_field(
            name="🗄️ Zusätzlich gespeichert",
            value=f"`{local_path}`",
            inline=False,
        )
    embed.add_field(
        name="🔐 Achtung",
        value=(
            "Die Datei enthält die komplette Rechtestruktur deines Servers. "
            "Behandle sie so vertraulich wie den Server selbst."
        ),
        inline=False,
    )
    return _footer(embed)


def backup_too_large_embed(size_text: str, limit_text: str) -> discord.Embed:
    return _footer(
        discord.Embed(
            title="📦 Backup zu groß für Discord",
            description=(
                f"Das Backup ist **{size_text}** groß, Discord nimmt hier aber nur "
                f"bis **{limit_text}** an.\n\n"
                f"**Lösung:** `/backup include_images:False` — dann bleiben Icon, "
                f"Emojis und Sticker außen vor und die Struktur passt problemlos. "
                f"Eine vollständige Kopie liegt weiterhin im Ordner `backups/` "
                f"neben dem Bot."
            ),
            color=COLOR_WARNING,
        )
    )


# ── /servers ──────────────────────────────────────────────────────────────────


def servers_embed(
    guilds: list[discord.Guild], bot_user: discord.abc.User, color: int = COLOR_INFO
) -> discord.Embed:
    embed = discord.Embed(
        title="🌐 Verfügbare Server",
        description=(
            f"**{bot_user}** ist auf **{len(guilds)}** Server(n)."
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
        embed.add_field(name="​", value=f"… und {len(guilds) - 24} weitere", inline=False)
    return _footer(embed)

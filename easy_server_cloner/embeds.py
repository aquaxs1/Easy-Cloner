"""
Every embed the bot sends, in one place, so the design stays consistent.

The functions deliberately take a source *name* rather than a
`discord.Guild`: when restoring from a backup file there is no source server
any more, but there is still a blueprint with a name.
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
    """Trims a list so it fits into an embed field."""
    out: list[str] = []
    length = 0
    for line in lines:
        if length + len(line) + 1 > limit:
            out.append(f"… and {len(lines) - len(out)} more")
            break
        out.append(line)
        length += len(line) + 1
    return "\n".join(out) or "—"


def _deletable(guild: discord.Guild) -> tuple[int, int]:
    """How many channels and roles would be deleted in the target."""
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
            "Copies the complete structure of a Discord server: roles, "
            "categories, channels, permissions, emojis and stickers — live from "
            "server to server, or through a backup file."
        ),
        color=color,
    )
    embed.add_field(
        name="📋 Commands",
        value=(
            "**`/clone`** — copy one server onto another\n"
            "**`/sync`** — add only what is missing, delete nothing\n"
            "**`/backup`** — save a server to a file\n"
            "**`/restore`** — apply a backup file to a server\n"
            "**`/preview`** — dry run, changes nothing\n"
            "**`/servers`** · **`/status`** · **`/help`**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚀 Schnellstart",
        value=(
            "1. Invite the bot to **both** servers (source & target)\n"
            "2. `/backup` on the source server — save it first\n"
            "3. `/preview` for the dry run\n"
            "4. run `/clone` and confirm\n\n"
            "You never type IDs: the server fields suggest every server the "
            "bot can reach."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Wichtig zu wissen",
        value=(
            "• Der Bot braucht auf beiden Servern **Administrator**\n"
            "• Copied roles always land **below** the bot's own role — "
            "move it as high as you can\n"
            "• `wipe_target: True` **deletes** every channel and role in the target\n"
            "• Cancelling costs nothing: `/clone resume:True` picks up where it stopped\n"
            "• **Members** cannot be cloned; messages can only be re-staged"
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
        title="🔍 Preview — nothing is changed yet",
        description=(
            f"**Source** → {blueprint.name}\n"
            f"**Target** → {target.name} `{target.id}`"
        ),
        color=color,
    )
    if source_icon_url:
        embed.set_thumbnail(url=source_icon_url)

    embed.add_field(
        name="📦 What gets copied",
        value="\n".join(f"`{value:>4}` × {label}" for label, value in blueprint.counts().items()),
        inline=True,
    )
    embed.add_field(
        name="🗑️ Would be deleted in the target",
        value=f"`{channels:>4}` × channels\n`{roles:>4}` × roles",
        inline=True,
    )
    embed.add_field(
        name="⏱️ Estimated duration",
        value=f"ca. **{_fmt_duration(est_seconds)}**",
        inline=False,
    )
    embed.add_field(
        name="🚫 Cannot be transferred",
        value=(
            "Members · bots · webhooks · server boosts · audit log · "
            "reactions and threads"
        ),
        inline=False,
    )
    return _footer(embed)


# ── Confirmation ──────────────────────────────────────────────────────────────


def confirm_embed(
    blueprint: GuildBlueprint,
    target: discord.Guild,
    options: CloneOptions,
    *,
    title: str = "⚠️ Confirm the clone",
    resume_state: CloneState | None = None,
) -> discord.Embed:
    channels, roles = _deletable(target)
    embed = discord.Embed(
        title=title,
        description=(
            f"You are about to overwrite **{target.name}** with the contents "
            f"of **{blueprint.name}**."
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(
        name="📥 Source",
        value=f"{blueprint.name}\n{blueprint.total_objects} objects",
        inline=True,
    )
    embed.add_field(name="📤 Target", value=f"{target.name}\n`{target.id}`", inline=True)
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(name="⚙️ Optionen", value="\n".join(options.summary()), inline=True)

    if resume_state is not None:
        embed.add_field(
            name="♻️ Resuming",
            value=(
                f"**{resume_state.object_count} objects** from an interrupted "
                f"run are skipped. **Nothing is deleted.**"
            ),
            inline=True,
        )
    elif options.wipe_target:
        embed.add_field(
            name="🗑️ Careful: the target will be emptied",
            value=(
                f"**{channels} channels** and **{roles} roles** will be "
                f"**deleted irreversibly**."
            ),
            inline=True,
        )
    else:
        embed.add_field(
            name="➕ Modus",
            value="Existing content on the target is left alone.",
            inline=True,
        )

    if options.messages_per_channel:
        embed.add_field(
            name="💬 Messages",
            value=(
                f"The last **{options.messages_per_channel}** messages per "
                f"channel are **re-staged** through a webhook, not migrated. "
                f"Author and avatar look real, but they are not."
            ),
            inline=False,
        )

    embed.add_field(
        name="​",
        value="Confirm within **60 seconds**, otherwise this is cancelled.",
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
        title="🔄 Running…",
        description=(
            f"**{source_name}** → **{target.name}**\n\n{progress_bar(progress.fraction)}"
        ),
        color=color,
    )

    counter = f" · {progress.current}/{progress.total}" if progress.total else ""
    embed.add_field(
        name=f"Step {progress.stage_index}/{len(STAGE_ORDER)}",
        value=f"**{progress.stage.value}**{counter}\n{progress.detail or '…'}",
        inline=False,
    )

    created = [
        ("🎭 Roles", report.roles_created),
        ("📁 Kategorien", report.categories_created),
        ("💬 Text", report.text_channels_created),
        ("🔊 Voice", report.voice_channels_created),
        ("😀 Emojis", report.emojis_created),
        ("🏷️ Sticker", report.stickers_created),
        ("✉️ Messages", report.messages_copied),
        ("👥 Members", report.members_updated),
    ]
    embed.add_field(
        name="✅ Bisher erledigt",
        value=" · ".join(f"{label} `{n}`" for label, n in created if n) or "—",
        inline=False,
    )

    footer_bits = []
    if report.warning_count or report.error_count:
        footer_bits.append(
            f"⚠️ `{report.warning_count}` warnings · ❌ `{report.error_count}` errors"
        )
    if progress.throttled:
        footer_bits.append(
            f"🐢 Discord is throttling — pause raised automatically to `{progress.delay:.2f}s`"
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
        title, color = "🛑 Run cancelled", COLOR_WARNING
    elif stage is Stage.FAILED:
        title, color = "❌ Run failed", COLOR_DANGER
    elif report.error_count:
        title, color = "⚠️ Finished with errors", COLOR_WARNING
    else:
        title, color = "✅ Run complete", COLOR_SUCCESS

    embed = discord.Embed(
        title=title,
        description=(
            f"**{source_name}** → **{target.name}**\n"
            f"Dauer: **{_fmt_duration(report.duration)}** · "
            f"**{report.total_created}** objects created"
        ),
        color=color,
    )
    if target.icon:
        embed.set_thumbnail(url=target.icon.url)

    embed.add_field(
        name="📊 Created",
        value=(
            f"🎭 Roles `{report.roles_created}`\n"
            f"📁 Kategorien `{report.categories_created}`\n"
            f"💬 Text-Channels `{report.text_channels_created}`\n"
            f"🔊 Voice-Channels `{report.voice_channels_created}`\n"
            f"🎙️ Stages `{report.stage_channels_created}`\n"
            f"🗣️ Foren `{report.forum_channels_created}`\n"
            f"😀 Emojis `{report.emojis_created}`\n"
            f"🏷️ Sticker `{report.stickers_created}`"
        ),
        inline=True,
    )

    extra = [
        f"Channels deleted `{report.channels_deleted}`",
        f"Roles deleted `{report.roles_deleted}`",
    ]
    if report.messages_copied:
        extra.append(f"Messages `{report.messages_copied}`")
    if report.members_updated:
        extra.append(f"Members `{report.members_updated}`")
    if report.throttle_events:
        extra.append(f"Rate-Limit `{report.throttle_events}×`")
    embed.add_field(name="🔧 Weiteres", value="\n".join(extra), inline=True)

    if report.errors:
        embed.add_field(
            name=f"❌ Errors ({report.error_count})",
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
            name="💡 Next steps",
            value=(
                "• Check the role order — everything sits below the bot's role\n"
                "• Assign members their roles (`/clone member_roles:True`)\n"
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
        title="💾 Backup created",
        description=(
            f"**{blueprint.name}** has been saved in full.\n"
            f"`/restore` can rebuild a server from it at any time."
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
        name="📄 File",
        value=(
            f"`{filename}`\n"
            f"Size: **{size_text}**\n"
            f"{'gzip-komprimiert' if compressed else 'Klartext-JSON'}\n"
            f"{'images included' if blueprint.has_images else 'without images'}"
        ),
        inline=True,
    )
    if local_path:
        embed.add_field(
            name="🗄️ Also saved",
            value=f"`{local_path}`",
            inline=False,
        )
    embed.add_field(
        name="🔐 Achtung",
        value=(
            "The file holds your server's complete permission structure. "
            "Treat it as confidentially as the server itself."
        ),
        inline=False,
    )
    return _footer(embed)


def backup_too_large_embed(size_text: str, limit_text: str) -> discord.Embed:
    return _footer(
        discord.Embed(
            title="📦 Backup too large for Discord",
            description=(
                f"The backup is **{size_text}**, but Discord only accepts "
                f"bis **{limit_text}** an.\n\n"
                f"**Fix:** `/backup include_images:False` — that leaves out the icon, "
                f"emojis and stickers, and the structure fits comfortably. "
                f"A complete copy still sits in the `backups/` folder "
                f"next to the bot."
            ),
            color=COLOR_WARNING,
        )
    )


# ── /servers ──────────────────────────────────────────────────────────────────


def servers_embed(
    guilds: list[discord.Guild], bot_user: discord.abc.User, color: int = COLOR_INFO
) -> discord.Embed:
    embed = discord.Embed(
        title="🌐 Available servers",
        description=(
            f"**{bot_user}** is on **{len(guilds)}** server(s)."
            if guilds
            else "The bot is not on any server. Invite it first."
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
                f"{len(guild.channels)} channels · {len(guild.roles)} roles\n"
                f"{'Bereit' if admin else 'Kein Administrator'}"
            ),
            inline=True,
        )
    if len(guilds) > 24:
        embed.add_field(name="​", value=f"… and {len(guilds) - 24} more", inline=False)
    return _footer(embed)

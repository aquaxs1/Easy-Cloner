"""
Extra steps that need a real source server.

Both need privileged intents and therefore only work when cloning directly
from server to server — a backup file can restore neither messages nor
members.

* **Message re-staging** - the last N messages per text channel are recreated
  through a webhook with the original name and avatar. This is a
  reconstruction, not a migration: reactions, threads, edit history and the
  real authors stay behind.
* **Role assignment** - members present on both servers get the roles in the
  target that they had in the source.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .blueprint import EVERYONE_KEY

if TYPE_CHECKING:  # pragma: no cover
    from .cloner import ServerCloner

log = logging.getLogger(__name__)

# Discord allows 2000 characters per message.
MAX_CONTENT = 2000

# Attachments are only copied up to this size — anything larger would slow the
# run down disproportionately.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

# Hard cap per channel, regardless of what was requested.
HARD_MESSAGE_CAP = 500

WEBHOOK_NAME = "Easy Server Cloner"


def _clean(content: str) -> str:
    """Defuses mass mentions in the re-staged text."""
    return content.replace("@everyone", "@​everyone").replace("@here", "@​here")


async def copy_messages(cloner: "ServerCloner", source: discord.Guild, limit: int) -> None:
    """Re-stages the last `limit` messages per text channel via webhook."""
    from .models import Stage

    limit = min(limit, HARD_MESSAGE_CAP)

    pairs: list[tuple[discord.TextChannel, discord.TextChannel]] = []
    for blueprint in cloner.blueprint.non_categories:
        if not blueprint.is_text_like:
            continue
        origin = source.get_channel(int(blueprint.key)) if blueprint.key.isdigit() else None
        mirror = cloner.channel_map.get(blueprint.key)
        if isinstance(origin, discord.TextChannel) and isinstance(mirror, discord.TextChannel):
            pairs.append((origin, mirror))

    await cloner._emit(Stage.MESSAGES, "Re-staging messages", 0, len(pairs))

    for index, (origin, mirror) in enumerate(pairs, start=1):
        await _copy_channel_messages(cloner, origin, mirror, limit)
        await cloner._emit(detail=f"#{origin.name}", current=index)


async def _copy_channel_messages(
    cloner: "ServerCloner",
    origin: discord.TextChannel,
    mirror: discord.TextChannel,
    limit: int,
) -> None:
    try:
        # oldest_first so the history lands in the right order in the target.
        history = [
            message
            async for message in origin.history(limit=limit, oldest_first=False)
        ]
    except discord.Forbidden:
        cloner.report.warn(f"No access to the history of #{origin.name}.")
        return
    except discord.HTTPException as exc:
        cloner.report.warn(f"History of #{origin.name} not readable: {exc.text or exc}")
        return

    history.reverse()
    if not history:
        return

    try:
        webhook = await cloner._call(
            mirror.create_webhook(name=WEBHOOK_NAME, reason=cloner.reason)
        )
    except discord.HTTPException as exc:
        cloner.report.warn(f"Webhook for #{mirror.name} failed: {exc.text or exc}")
        return

    try:
        for message in history:
            await _send_one(cloner, webhook, message)
            await cloner._tick()
    finally:
        # The webhook has served its purpose and would otherwise stay in the
        # channel settings forever.
        try:
            await webhook.delete(reason=cloner.reason)
        except discord.HTTPException:
            pass


async def _send_one(
    cloner: "ServerCloner", webhook: discord.Webhook, message: discord.Message
) -> None:
    content = _clean(message.content or "")[:MAX_CONTENT]

    files: list[discord.File] = []
    for attachment in message.attachments[:5]:
        if attachment.size > MAX_ATTACHMENT_BYTES:
            content = f"{content}\n*[attachment {attachment.filename} too large]*"[:MAX_CONTENT]
            continue
        try:
            files.append(await attachment.to_file())
        except discord.HTTPException:
            pass

    embeds = [e for e in message.embeds if e.type == "rich"][:10]

    if not content and not files and not embeds:
        return

    try:
        await cloner._call(
            webhook.send(
                content=content or discord.utils.MISSING,
                username=message.author.display_name[:80] or "Unbekannt",
                avatar_url=message.author.display_avatar.url,
                files=files or discord.utils.MISSING,
                embeds=embeds or discord.utils.MISSING,
                # Der Nachbau darf niemanden anpingen.
                allowed_mentions=discord.AllowedMentions.none(),
                wait=False,
            )
        )
        cloner.report.messages_copied += 1
    except discord.HTTPException as exc:
        cloner.report.warn(f"Message could not be transferred: {exc.text or exc}")


async def assign_member_roles(cloner: "ServerCloner", source: discord.Guild) -> None:
    """Gives members in the target the roles they had in the source."""
    from .models import Stage

    await cloner._emit(Stage.MEMBER_ROLES, "Assigning roles", 0, 0)

    try:
        target_members = [m async for m in cloner.target.fetch_members(limit=None)]
    except discord.HTTPException as exc:
        cloner.report.warn(
            f"Member list unavailable ({exc.text or exc}). Is the "
            f"Members-Intent im Developer Portal aktiviert?"
        )
        return

    await cloner._emit(total=len(target_members))

    for index, member in enumerate(target_members, start=1):
        if member.bot:
            continue

        origin = source.get_member(member.id)
        if origin is None:
            continue

        wanted: list[discord.Role] = []
        for role in origin.roles:
            if role.is_default() or role.managed:
                continue
            key = EVERYONE_KEY if role.is_default() else str(role.id)
            mapped = cloner.role_map.get(key)
            # The bot cannot grant roles above its own.
            if mapped is not None and mapped < cloner.target.me.top_role:
                wanted.append(mapped)

        new_roles = [r for r in wanted if r not in member.roles]
        if not new_roles:
            continue

        try:
            await cloner._call(member.add_roles(*new_roles, reason=cloner.reason))
            cloner.report.members_updated += 1
        except discord.Forbidden:
            cloner.report.warn(f"Roles for {member} cannot be granted (hierarchy).")
        except discord.HTTPException as exc:
            cloner.report.warn(f"Roles for {member}: {exc.text or exc}")

        await cloner._emit(detail=str(member), current=index)
        await cloner._tick()

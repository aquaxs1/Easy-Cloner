"""Interactive components: the safety prompt and the cancel button."""

from __future__ import annotations

import discord

from . import embeds


class ConfirmView(discord.ui.View):
    """A two-button prompt only the invoking user may operate."""

    def __init__(self, requester_id: int, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.value: bool | None = None
        self.interaction: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Not your run",
                    "Only the person who ran the command can confirm it.",
                ),
                ephemeral=True,
            )
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Jetzt klonen", style=discord.ButtonStyle.danger, emoji="🧬"
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.value = True
        self.interaction = interaction
        self._disable_all()
        self.stop()

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️"
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.value = False
        self.interaction = interaction
        self._disable_all()
        await interaction.response.edit_message(
            embed=embeds.info(
                "✖️ Cancelled",
                "Nothing was changed.",
                color=embeds.COLOR_MUTED,
            ),
            view=self,
        )
        self.stop()

    async def on_timeout(self) -> None:
        self.value = False
        self._disable_all()


class CancelView(discord.ui.View):
    """The cancel button shown while a clone is running."""

    def __init__(self, cloner, requester_id: int) -> None:
        super().__init__(timeout=None)
        self.cloner = cloner
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Not your run",
                    "Only the person who started the clone can cancel it.",
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.danger, emoji="🛑"
    )
    async def stop_clone(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.cloner.cancel()
        button.disabled = True
        button.label = "Cancelling…"
        await interaction.response.edit_message(view=self)
        self.stop()

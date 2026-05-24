"""Share-with-channel affordance for ephemeral responses.

Ephemeral messages cannot be promoted in place, so the share button posts a
new public message into the channel with the current embeds, then disables
itself so the same view can't be shared twice.
"""

import discord


class ShareButton(discord.ui.Button):
    def __init__(self, row: int = 4) -> None:
        super().__init__(
            label="Share with channel",
            style=discord.ButtonStyle.secondary,
            emoji="\N{PUBLIC ADDRESS LOUDSPEAKER}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        msg = interaction.message
        embeds = list(msg.embeds) if msg and msg.embeds else None
        content = f"Shared by {interaction.user.mention}"
        await interaction.channel.send(content=content, embeds=embeds)
        self.disabled = True
        self.label = "Shared"
        await interaction.response.edit_message(view=self.view)


class ShareableView(discord.ui.View):
    """Lightweight view for ephemeral responses whose only interaction is sharing."""

    def __init__(self, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.add_item(ShareButton(row=0))

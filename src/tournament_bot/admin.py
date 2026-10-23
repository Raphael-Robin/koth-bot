"""Admin slash commands. Only members with the admin role (or Administrator) can use them."""

import discord
from discord import app_commands
from discord.ext import commands

from . import embeds
from .views.public_panel import PublicPanelView


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.client.is_admin(interaction.user)

    return app_commands.check(predicate)


class Admin(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        raise error

    setup = app_commands.Group(name="setup", description="Set up the tournament bot")
    signups = app_commands.Group(name="signups", description="Open or close team sign-ups")

    @setup.command(name="panel", description="Post the public sign-up panel in this channel")
    @is_admin()
    async def setup_panel(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        old_channel = await db.get_setting("panel_channel_id")
        old_message = await db.get_setting("panel_message_id")
        if old_channel and old_message:
            await self.bot._delete_message(int(old_channel), int(old_message))

        message = await interaction.channel.send(
            embed=embeds.public_panel([], await db.signups_open()), view=PublicPanelView()
        )
        await db.set_setting("panel_channel_id", str(interaction.channel.id))
        await db.set_setting("panel_message_id", str(message.id))
        await self.bot.refresh_public_panel()
        await interaction.response.send_message("Panel posted.", ephemeral=True)
        await self.bot.audit(
            f"{interaction.user.mention} posted the panel in {interaction.channel.mention}",
            "panel_posted",
            actor=interaction.user,
        )

    @signups.command(name="open", description="Allow players to create and apply to teams")
    @is_admin()
    async def signups_open(self, interaction: discord.Interaction) -> None:
        await self._set_signups(interaction, True)

    @signups.command(name="close", description="Stop players creating and applying to teams")
    @is_admin()
    async def signups_close(self, interaction: discord.Interaction) -> None:
        await self._set_signups(interaction, False)

    async def _set_signups(self, interaction: discord.Interaction, open_: bool) -> None:
        await self.bot.db.set_setting("signups_open", "1" if open_ else "0")
        await self.bot.refresh_public_panel()
        word = "opened" if open_ else "closed"
        await interaction.response.send_message(f"Sign-ups {word}.", ephemeral=True)
        await self.bot.audit(
            f"{interaction.user.mention} {word} sign-ups", f"signups_{word}", actor=interaction.user
        )


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))

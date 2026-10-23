"""Slash commands any player can use."""

import discord
from discord import app_commands
from discord.ext import commands

from .albion import Character
from .views.character import lookup_character


class Players(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    character = app_commands.Group(name="character", description="Your linked Albion character")

    @character.command(name="change", description="Link a different Albion character")
    @app_commands.describe(name="Your character's name on the Europe server")
    async def change(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def on_pick(select_interaction: discord.Interaction, character: Character) -> None:
            bot = select_interaction.client
            await select_interaction.response.edit_message(
                content=f"Your character is now **{character.name}**.", view=None
            )
            team = await bot.db.current_team_of(select_interaction.user.id)
            if team:
                await bot.refresh_manage_panel(team)
            await bot.audit(
                f"{select_interaction.user.mention} linked the character {character.name}",
                "character_changed",
                actor=select_interaction.user,
                team=team,
            )

        await lookup_character(interaction, name.strip(), on_pick)


async def setup(bot) -> None:
    await bot.add_cog(Players(bot))

"""Character lookup: the player types a name, then picks their character from API results."""

from collections.abc import Awaitable, Callable

import discord

from ..albion import AlbionAPIError, Character

OnPick = Callable[[discord.Interaction, Character], Awaitable[None]]


def character_name_input() -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label="Albion character name (Europe server)",
        placeholder="Exact spelling helps, but you'll pick from a list",
        min_length=3,
        max_length=32,
    )


class CharacterSelect(discord.ui.Select):
    def __init__(self, characters: list[Character], on_pick: OnPick) -> None:
        self.characters = {c.id: c for c in characters}
        self.on_pick = on_pick
        super().__init__(
            placeholder="Pick your character",
            options=[
                discord.SelectOption(label=c.name, description=c.guild or "No guild", value=c.id)
                for c in characters
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        character = self.characters[self.values[0]]
        await interaction.client.db.set_player_character(
            interaction.user.id, character.id, character.name, character.guild
        )
        self.view.stop()
        await self.on_pick(interaction, character)


class CharacterSelectView(discord.ui.View):
    def __init__(self, characters: list[Character], on_pick: OnPick) -> None:
        super().__init__(timeout=300)
        self.add_item(CharacterSelect(characters, on_pick))


async def lookup_character(interaction: discord.Interaction, name: str, on_pick: OnPick) -> None:
    """Search the Albion API and show the results as a select menu.

    The interaction must already be deferred. on_pick runs with the select's interaction
    once the player chooses, after their character has been saved.
    """
    try:
        characters = await interaction.client.albion.search(name)
    except AlbionAPIError:
        await interaction.followup.send(
            "The Albion API isn't answering right now. Please try again in a few minutes.",
            ephemeral=True,
        )
        return
    if not characters:
        await interaction.followup.send(
            f"No character called **{name}** was found on the Europe server. "
            "Check the spelling and try again.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        "Which one is you? Each option shows the character's guild.",
        view=CharacterSelectView(characters, on_pick),
        ephemeral=True,
    )

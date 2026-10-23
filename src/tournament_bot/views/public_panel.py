"""The public sign-up panel: Create team and Apply to team."""

import logging

import discord

from .. import embeds
from ..albion import Character
from ..db import TeamError
from ..discord_setup import DISCORD_CATEGORY_LIMIT, create_team_space
from .applications import application_view
from .character import character_name_input, lookup_character
from .manage_panel import manage_view

log = logging.getLogger(__name__)


async def _signup_blocker(interaction: discord.Interaction) -> str | None:
    """Why this player can't create or join a team right now, if anything."""
    bot = interaction.client
    if not await bot.db.signups_open():
        return "Sign-ups are closed."
    current = await bot.db.current_team_of(interaction.user.id)
    if current and current.status != "disbanded":
        return f"You are already in **{current.name}**. Leave it first."
    return None


class PublicPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create team", style=discord.ButtonStyle.success, custom_id="panel:create"
    )
    async def create_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if reason := await _signup_blocker(interaction):
            await interaction.response.send_message(reason, ephemeral=True)
            return
        if len(interaction.guild.categories) >= DISCORD_CATEGORY_LIMIT:
            await interaction.response.send_message(
                "The server is full: Discord allows 50 categories. Please contact an admin.",
                ephemeral=True,
            )
            await interaction.client.audit(
                f"{interaction.user.mention} could not create a team: category limit reached",
                "category_limit",
                actor=interaction.user,
            )
            return
        bot = interaction.client
        player = await bot.db.get_player(interaction.user.id)
        old_team = await bot.db.disbanded_team_led_by(interaction.user.id)
        current = await bot.db.current_team_of(interaction.user.id)
        if old_team and current and current.id == old_team.id:
            await interaction.response.send_message(
                f"You led **{old_team.name}**, which is disbanded. Restore it, or start a new "
                "team? Starting a new one deletes the old team for good.",
                view=RestoreOrNewView(old_team.id, player.albion_name if player else None),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            CreateTeamModal(player.albion_name if player else None)
        )

    @discord.ui.button(
        label="Leave team", style=discord.ButtonStyle.secondary, custom_id="panel:leave"
    )
    async def leave(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        team = await bot.db.current_team_of(interaction.user.id)
        if team is None or team.status == "disbanded":
            await interaction.response.send_message("You aren't in a team.", ephemeral=True)
            return
        if await bot.db.rosters_locked():
            await interaction.response.send_message("Rosters are locked.", ephemeral=True)
            return
        if team.leader_id == interaction.user.id:
            await interaction.response.send_message(
                "Leaders can't leave. Transfer leadership or disband the team from "
                f"<#{team.manage_channel_id}> first.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Leave **{team.name}**?", view=ConfirmLeaveView(team.id), ephemeral=True
        )

    @discord.ui.button(
        label="Apply to team", style=discord.ButtonStyle.primary, custom_id="panel:apply"
    )
    async def apply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if reason := await _signup_blocker(interaction):
            await interaction.response.send_message(reason, ephemeral=True)
            return
        teams = await interaction.client.db.list_teams(("forming", "active"))
        if not teams:
            await interaction.response.send_message(
                "There are no teams to apply to yet.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Which team do you want to apply to?", view=TeamPickerView(teams), ephemeral=True
        )


class ConfirmLeaveView(discord.ui.View):
    def __init__(self, team_id: int) -> None:
        super().__init__(timeout=120)
        self.team_id = team_id

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        team = await bot.db.get_team(self.team_id)
        await interaction.response.edit_message(content="Leaving...", view=None)
        try:
            await bot.remove_member(team, interaction.user, actor=interaction.user, how="left")
        except TeamError as exc:
            await interaction.edit_original_response(content=str(exc))
            return
        await interaction.edit_original_response(content=f"You left **{team.name}**.")


class RestoreOrNewView(discord.ui.View):
    def __init__(self, old_team_id: int, known_character: str | None) -> None:
        super().__init__(timeout=300)
        self.old_team_id = old_team_id
        self.known_character = known_character

    @discord.ui.button(label="Restore my old team", style=discord.ButtonStyle.success)
    async def restore(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        team = await bot.db.get_team(self.old_team_id)
        if team is None or team.status != "disbanded":
            await interaction.response.edit_message(content="That team is gone.", view=None)
            return
        await interaction.response.edit_message(content=f"Restoring **{team.name}**...", view=None)
        try:
            team = await bot.restore(team, actor=interaction.user)
        except TeamError as exc:
            await interaction.edit_original_response(content=str(exc))
            return
        await interaction.edit_original_response(
            content=f"**{team.name}** is back. Head to <#{team.manage_channel_id}>."
        )

    @discord.ui.button(label="Create a new team", style=discord.ButtonStyle.primary)
    async def new(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateTeamModal(self.known_character, replaces=self.old_team_id)
        )


class CreateTeamModal(discord.ui.Modal, title="Create a team"):
    def __init__(self, known_character: str | None, replaces: int | None = None) -> None:
        super().__init__()
        self.replaces = replaces
        self.team_name = discord.ui.TextInput(label="Team name", min_length=2, max_length=32)
        self.character = character_name_input()
        self.character.default = known_character
        self.add_item(self.team_name)
        self.add_item(self.character)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = " ".join(self.team_name.value.split())
        if await interaction.client.db.name_taken(name):
            await interaction.response.send_message(
                f"A team called **{name}** already exists. Pick another name.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def on_pick(select_interaction: discord.Interaction, character: Character) -> None:
            await finish_create(select_interaction, name, character, self.replaces)

        await lookup_character(interaction, self.character.value.strip(), on_pick)


async def finish_create(
    interaction: discord.Interaction, name: str, character: Character, replaces: int | None
) -> None:
    bot = interaction.client
    await interaction.response.edit_message(content=f"Creating **{name}**...", view=None)
    if replaces:
        old_team = await bot.db.get_team(replaces)
        if (
            old_team
            and old_team.status == "disbanded"
            and old_team.leader_id == interaction.user.id
        ):
            await bot.hard_delete(
                old_team, actor=interaction.user, why="its leader started a new team"
            )
    try:
        team = await bot.db.create_team(name, interaction.user.id)
    except TeamError as exc:
        await interaction.edit_original_response(content=str(exc))
        return

    try:
        ids = await create_team_space(bot.guild, team, interaction.user, bot.admin_role)
    except discord.HTTPException:
        log.exception("Creating channels for %s failed", team.label)
        await bot.db.hard_delete_team(team.id)
        await interaction.edit_original_response(
            content="Discord refused to create your team's channels. Please try again, "
            "and tell an admin if it keeps failing."
        )
        return
    await bot.db.set_team_discord_ids(team.id, **ids)
    team = await bot.db.get_team(team.id)

    manage_channel = bot.guild.get_channel(team.manage_channel_id)
    panel = await manage_channel.send(
        embed=embeds.manage_panel(team, await bot.db.list_members(team.id)),
        view=manage_view(team),
    )
    await bot.db.set_team_discord_ids(team.id, panel_message_id=panel.id)
    await manage_channel.send(
        embeds.welcome(team), allowed_mentions=discord.AllowedMentions(users=True)
    )

    await interaction.edit_original_response(
        content=f"**{team.name}** is ready. Head to {manage_channel.mention} to invite players."
    )
    await bot.audit(
        f"{interaction.user.mention} created **{team.label}** as {character.name}",
        "team_created",
        actor=interaction.user,
        team=team,
    )


class TeamPickerView(discord.ui.View):
    def __init__(self, teams) -> None:
        super().__init__(timeout=300)
        # A select holds 25 options; a message holds 5 rows.
        for start in range(0, min(len(teams), 125), 25):
            self.add_item(TeamSelect(teams[start : start + 25]))


class TeamSelect(discord.ui.Select):
    def __init__(self, teams) -> None:
        super().__init__(
            placeholder=f"{teams[0].name} to {teams[-1].name}" if len(teams) > 1 else teams[0].name,
            options=[discord.SelectOption(label=t.name[:100], value=str(t.id)) for t in teams],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        team = await interaction.client.db.get_team(int(self.values[0]))
        if not team or team.status == "disbanded":
            await interaction.response.send_message("That team no longer exists.", ephemeral=True)
            return
        player = await interaction.client.db.get_player(interaction.user.id)
        await interaction.response.send_modal(
            ApplyModal(team.id, team.name, player.albion_name if player else None)
        )


class ApplyModal(discord.ui.Modal):
    def __init__(self, team_id: int, team_name: str, known_character: str | None) -> None:
        super().__init__(title=f"Apply to {team_name}"[:45])
        self.team_id = team_id
        self.character = character_name_input()
        self.character.default = known_character
        self.add_item(self.character)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def on_pick(select_interaction: discord.Interaction, character: Character) -> None:
            await finish_apply(select_interaction, self.team_id)

        await lookup_character(interaction, self.character.value.strip(), on_pick)


async def finish_apply(interaction: discord.Interaction, team_id: int) -> None:
    bot = interaction.client
    await interaction.response.edit_message(content="Sending your application...", view=None)
    if reason := await _signup_blocker(interaction):
        await interaction.edit_original_response(content=reason)
        return
    team = await bot.db.get_team(team_id)
    channel = bot.guild.get_channel(team.applications_channel_id) if team else None
    if not team or team.status == "disbanded" or channel is None:
        await interaction.edit_original_response(content="That team no longer exists.")
        return

    application, replaced = await bot.db.create_application(team.id, interaction.user.id)
    if replaced:
        await bot._delete_message(replaced.channel_id, replaced.message_id)

    player = await bot.db.get_player(interaction.user.id)
    message = await channel.send(
        f"<@&{team.staff_role_id}>, {interaction.user.mention} wants to join.",
        embed=embeds.application(interaction.user, player),
        view=application_view(application.id),
        allowed_mentions=discord.AllowedMentions(roles=True, users=False),
    )
    await bot.db.set_application_message(application.id, channel.id, message.id)

    note = " Your previous application was withdrawn." if replaced else ""
    await interaction.edit_original_response(
        content=f"Your application to **{team.name}** is in. "
        f"You'll get a DM when they decide.{note}"
    )
    await bot.audit(
        f"{interaction.user.mention} applied to **{team.label}**",
        "application_created",
        actor=interaction.user,
        team=team,
    )

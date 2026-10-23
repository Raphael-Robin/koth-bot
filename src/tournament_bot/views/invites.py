"""Accept and Reject buttons on the invite a player receives."""

import re

import discord

from ..albion import Character
from ..db import TeamError
from .character import character_name_input, lookup_character


def invite_view(invite_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(InviteButton("accept", invite_id))
    view.add_item(InviteButton("reject", invite_id))
    return view


class InviteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"invite:(?P<action>accept|reject):(?P<id>\d+)",
):
    def __init__(self, action: str, invite_id: int) -> None:
        accept = action == "accept"
        super().__init__(
            discord.ui.Button(
                label="Accept" if accept else "Reject",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                custom_id=f"invite:{action}:{invite_id}",
            )
        )
        self.action = action
        self.invite_id = invite_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        invite = await bot.db.get_invite(self.invite_id)
        if invite is None or interaction.user.id != invite.user_id:
            await interaction.response.send_message("This invite isn't for you.", ephemeral=True)
            return
        if invite.status != "pending":
            await interaction.response.edit_message(
                content="This invite is no longer valid.", view=None
            )
            return
        team = await bot.db.get_team(invite.team_id)
        if team is None or team.status == "disbanded":
            await bot.db.answer_invite(invite.id, "expired")
            await interaction.response.edit_message(
                content="That team no longer exists.", view=None
            )
            return

        if self.action == "reject":
            await bot.db.answer_invite(invite.id, "rejected")
            await interaction.response.edit_message(
                content=f"You declined the invite to **{team.name}**.", view=None
            )
            await bot.audit(
                f"{interaction.user.mention} declined the invite to **{team.label}**",
                "invite_rejected",
                actor=interaction.user,
                team=team,
            )
            return

        player = await bot.db.get_player(interaction.user.id)
        await interaction.response.send_modal(
            AcceptInviteModal(invite.id, team.name, player.albion_name if player else None)
        )


class AcceptInviteModal(discord.ui.Modal):
    def __init__(self, invite_id: int, team_name: str, known_character: str | None) -> None:
        super().__init__(title=f"Join {team_name}"[:45])
        self.invite_id = invite_id
        self.character = character_name_input()
        self.character.default = known_character
        self.add_item(self.character)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def on_pick(select_interaction: discord.Interaction, character: Character) -> None:
            await finish_accept(select_interaction, self.invite_id)

        await lookup_character(interaction, self.character.value.strip(), on_pick)


async def finish_accept(interaction: discord.Interaction, invite_id: int) -> None:
    bot = interaction.client
    await interaction.response.edit_message(content="Joining...", view=None)
    if await bot.db.rosters_locked():
        await interaction.edit_original_response(content="Rosters are locked.")
        return
    invite = await bot.db.get_invite(invite_id)
    team = await bot.db.get_team(invite.team_id)
    if not await bot.db.answer_invite(invite_id, "accepted"):
        await interaction.edit_original_response(content="This invite is no longer valid.")
        return
    inviter = bot.get_user(invite.invited_by) or await bot.fetch_user(invite.invited_by)
    try:
        await bot.add_member(team, interaction.user, actor=inviter, how="invite")
    except TeamError as exc:
        await bot.db.answer_invite(invite_id, "expired", was="accepted")
        await interaction.edit_original_response(content=str(exc))
        return

    await interaction.edit_original_response(content=f"You joined **{team.name}**. Welcome!")
    try:
        channel = bot.get_channel(invite.channel_id) or await bot.fetch_channel(invite.channel_id)
        await channel.get_partial_message(invite.message_id).edit(
            content=f"You accepted the invite to **{team.name}**.", view=None
        )
    except discord.HTTPException:
        pass

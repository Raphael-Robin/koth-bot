"""The leader-only panel in #manage-team."""

import re

import discord

from ..db import Team, TeamError
from .invites import invite_view


def manage_view(team: Team) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ManageButton("invite", team.id))
    return view


BUTTONS = {
    "invite": ("Invite member", discord.ButtonStyle.primary),
}


class ManageButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"team:(?P<action>[a-z]+):(?P<id>\d+)"
):
    def __init__(self, action: str, team_id: int) -> None:
        label, style = BUTTONS[action]
        super().__init__(
            discord.ui.Button(label=label, style=style, custom_id=f"team:{action}:{team_id}")
        )
        self.action = action
        self.team_id = team_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        team = await interaction.client.db.get_team(self.team_id)
        if team is None or team.status == "disbanded":
            await interaction.response.send_message("This team no longer exists.", ephemeral=True)
            return
        if not await interaction.client.can_manage(team, interaction.user):
            await interaction.response.send_message(
                "Only the team leader can use this panel.", ephemeral=True
            )
            return
        if self.action == "invite":
            await interaction.response.send_message(
                "Who do you want to invite?", view=InvitePickerView(team.id), ephemeral=True
            )


class InvitePickerView(discord.ui.View):
    def __init__(self, team_id: int) -> None:
        super().__init__(timeout=300)
        self.team_id = team_id

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Pick a player")
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        bot = interaction.client
        target = select.values[0]
        team = await bot.db.get_team(self.team_id)
        if target.bot:
            await interaction.response.edit_message(content="Bots can't join teams.", view=None)
            return
        if await bot.db.rosters_locked():
            await interaction.response.edit_message(content="Rosters are locked.", view=None)
            return
        current = await bot.db.current_team_of(target.id)
        if current and current.status != "disbanded":
            where = "your team" if current.id == team.id else "another team"
            await interaction.response.edit_message(
                content=f"{target.mention} is already in {where}.", view=None
            )
            return
        try:
            invite = await bot.db.create_invite(team.id, target.id, interaction.user.id)
        except TeamError as exc:
            await interaction.response.edit_message(content=str(exc), view=None)
            return

        await interaction.response.defer()
        text = (
            f"{target.mention}, {interaction.user.mention} invited you to join **{team.name}** "
            "for King of the Hill."
        )
        message = await bot.dm(target, text, view=invite_view(invite.id))
        where = "by DM"
        if message is None:
            # DMs closed: fall back to a mention in the public panel channel.
            channel_id = await bot.db.get_setting("panel_channel_id")
            channel = bot.guild.get_channel(int(channel_id)) if channel_id else None
            if channel is not None:
                message = await channel.send(
                    text,
                    view=invite_view(invite.id),
                    allowed_mentions=discord.AllowedMentions(users=[target]),
                )
                where = f"in {channel.mention} (their DMs are closed)"
        if message is None:
            await bot.db.answer_invite(invite.id, "expired")
            await interaction.edit_original_response(
                content=f"Couldn't reach {target.mention}: their DMs are closed.", view=None
            )
            return
        await bot.db.set_invite_message(invite.id, message.channel.id, message.id)
        await interaction.edit_original_response(
            content=f"Invite sent to {target.mention} {where}.", view=None
        )
        await bot.audit(
            f"{interaction.user.mention} invited {target.mention} to **{team.label}**",
            "invite_sent",
            actor=interaction.user,
            team=team,
            target=target,
        )

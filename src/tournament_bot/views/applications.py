"""Accept and Deny buttons on each application in #team-applications."""

import re

import discord

from ..db import TeamError


def application_view(application_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ApplicationButton("accept", application_id))
    view.add_item(ApplicationButton("deny", application_id))
    return view


class ApplicationButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"app:(?P<action>accept|deny):(?P<id>\d+)"
):
    def __init__(self, action: str, application_id: int) -> None:
        accept = action == "accept"
        super().__init__(
            discord.ui.Button(
                label="Accept" if accept else "Deny",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.danger,
                custom_id=f"app:{action}:{application_id}",
            )
        )
        self.action = action
        self.application_id = application_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        application = await bot.db.get_application(self.application_id)
        if application is None or application.status != "pending":
            await interaction.response.edit_message(view=None)
            await interaction.followup.send("This application was already handled.", ephemeral=True)
            return
        team = await bot.db.get_team(application.team_id)
        if not await bot.can_staff(team, interaction.user):
            await interaction.response.send_message(
                "Only the team leader and helpers can do that.", ephemeral=True
            )
            return

        await interaction.response.defer()
        status = "accepted" if self.action == "accept" else "denied"
        if not await bot.db.decide_application(application.id, status, interaction.user.id):
            await interaction.followup.send("This application was already handled.", ephemeral=True)
            return

        applicant = bot.get_user(application.user_id) or await bot.fetch_user(application.user_id)
        if status == "accepted":
            try:
                await bot.add_member(team, applicant, actor=interaction.user, how="application")
            except TeamError as exc:
                await bot.db.reopen_application(application.id)
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await bot.dm(applicant, f"Your application to **{team.name}** was accepted. Welcome!")
        else:
            await bot.dm(applicant, f"Your application to **{team.name}** was declined.")
            await bot.audit(
                f"{interaction.user.mention} denied {applicant.mention}'s application to "
                f"**{team.label}**",
                "application_denied",
                actor=interaction.user,
                team=team,
                target=applicant,
            )

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.set_footer(text=f"{status.capitalize()} by {interaction.user.display_name}")
        await interaction.message.edit(embed=embed, view=None)

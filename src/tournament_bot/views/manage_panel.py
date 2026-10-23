"""The leader-only panel in #manage-team."""

import re
from collections.abc import Awaitable, Callable

import discord

from ..db import Member, Team, TeamError
from ..embeds import character_line
from .invites import invite_view

BUTTONS = {
    "invite": ("Invite member", discord.ButtonStyle.primary),
    "remove": ("Remove member", discord.ButtonStyle.secondary),
    "helpers": ("Helpers", discord.ButtonStyle.secondary),
    "colour": ("Role colour", discord.ButtonStyle.secondary),
    "transfer": ("Transfer leadership", discord.ButtonStyle.secondary),
    "disband": ("Disband team", discord.ButtonStyle.danger),
}


def manage_view(team: Team) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in BUTTONS:
        view.add_item(ManageButton(action, team.id))
    return view


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
        bot = interaction.client
        team = await bot.db.get_team(self.team_id)
        if team is None or team.status == "disbanded":
            await interaction.response.send_message("This team no longer exists.", ephemeral=True)
            return
        if not await bot.can_manage(team, interaction.user):
            await interaction.response.send_message(
                "Only the team leader can use this panel.", ephemeral=True
            )
            return

        if self.action == "invite":
            await interaction.response.send_message(
                "Who do you want to invite?", view=InvitePickerView(team.id), ephemeral=True
            )
        elif self.action == "colour":
            await interaction.response.send_modal(ColourModal(team.id))
        elif self.action == "disband":
            await interaction.response.send_message(
                f"Disband **{team.name}**? Everyone loses the team roles and the channels become "
                "read-only. You can restore it later from the Create team button.",
                view=ConfirmView(team.id, _disband),
                ephemeral=True,
            )
        else:
            others = [m for m in await bot.db.list_members(team.id) if m.user_id != team.leader_id]
            if not others:
                await interaction.response.send_message(
                    "There's nobody else in the team yet.", ephemeral=True
                )
                return
            prompt, on_pick = {
                "remove": ("Who do you want to remove?", _remove),
                "helpers": ("Pick a member to make or unmake a helper.", _toggle_helper),
                "transfer": ("Who should lead the team?", _ask_transfer),
            }[self.action]
            await interaction.response.send_message(
                prompt,
                view=MemberPickerView(team.id, others, on_pick, bot.guild),
                ephemeral=True,
            )


OnMember = Callable[[discord.Interaction, Team, int], Awaitable[None]]


class MemberPickerView(discord.ui.View):
    def __init__(
        self, team_id: int, members: list[Member], on_pick: OnMember, guild: discord.Guild
    ) -> None:
        super().__init__(timeout=300)
        # A select holds 25 options and a message 5 selects; teams this large are unlikely.
        for start in range(0, min(len(members), 125), 25):
            self.add_item(MemberSelect(team_id, members[start : start + 25], on_pick, guild))


class MemberSelect(discord.ui.Select):
    def __init__(
        self, team_id: int, members: list[Member], on_pick: OnMember, guild: discord.Guild
    ) -> None:
        self.team_id = team_id
        self.on_pick = on_pick
        options = []
        for m in members:
            user = guild.get_member(m.user_id)
            name = user.display_name if user else f"User {m.user_id}"
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    description=(("Helper · " if m.is_helper else "") + character_line(m))[:100],
                    value=str(m.user_id),
                )
            )
        super().__init__(placeholder="Pick a member", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        team = await interaction.client.db.get_team(self.team_id)
        if team is None or team.status == "disbanded":
            await interaction.response.edit_message(
                content="This team no longer exists.", view=None
            )
            return
        if not await interaction.client.can_manage(team, interaction.user):
            await interaction.response.edit_message(
                content="You're no longer the leader.", view=None
            )
            return
        await self.on_pick(interaction, team, int(self.values[0]))


async def _user(interaction: discord.Interaction, user_id: int) -> discord.abc.User:
    bot = interaction.client
    return bot.guild.get_member(user_id) or bot.get_user(user_id) or await bot.fetch_user(user_id)


async def _remove(interaction: discord.Interaction, team: Team, user_id: int) -> None:
    bot = interaction.client
    if await bot.db.rosters_locked() and not bot.is_admin(interaction.user):
        await interaction.response.edit_message(content="Rosters are locked.", view=None)
        return
    user = await _user(interaction, user_id)
    await interaction.response.edit_message(content=f"Removing {user.mention}...", view=None)
    try:
        await bot.remove_member(team, user, actor=interaction.user, how="was removed from")
    except TeamError as exc:
        await interaction.edit_original_response(content=str(exc))
        return
    await bot.dm(user, f"You were removed from **{team.name}**.")
    await interaction.edit_original_response(content=f"{user.mention} was removed.")


async def _toggle_helper(interaction: discord.Interaction, team: Team, user_id: int) -> None:
    bot = interaction.client
    members = {m.user_id: m for m in await bot.db.list_members(team.id)}
    if user_id not in members:
        await interaction.response.edit_message(content="They're no longer in the team.", view=None)
        return
    helper = not members[user_id].is_helper
    user = await _user(interaction, user_id)
    await interaction.response.defer()
    await bot.set_helper(team, user, helper, actor=interaction.user)
    if helper:
        await bot.dm(
            user,
            f"You're now a helper of **{team.name}**: you can handle applications and post "
            "in #mass.",
        )
    word = "is now a helper" if helper else "is no longer a helper"
    await interaction.edit_original_response(content=f"{user.mention} {word}.", view=None)


async def _ask_transfer(interaction: discord.Interaction, team: Team, user_id: int) -> None:
    user = await _user(interaction, user_id)

    async def confirm(confirm_interaction: discord.Interaction, team: Team) -> None:
        bot = confirm_interaction.client
        member = bot.guild.get_member(user_id)
        if member is None:
            await confirm_interaction.edit_original_response(
                content="They're no longer on the server.", view=None
            )
            return
        try:
            await bot.transfer_leader(team, member, actor=confirm_interaction.user)
        except TeamError as exc:
            await confirm_interaction.edit_original_response(content=str(exc), view=None)
            return
        await confirm_interaction.edit_original_response(
            content=f"{member.mention} now leads **{team.name}**.", view=None
        )

    await interaction.response.edit_message(
        content=f"Make {user.mention} the leader of **{team.name}**? You'll lose access to this "
        "panel.",
        view=ConfirmView(team.id, confirm),
    )


async def _disband(interaction: discord.Interaction, team: Team) -> None:
    await interaction.client.disband(team, actor=interaction.user)
    await interaction.edit_original_response(content=f"**{team.name}** was disbanded.", view=None)


class ConfirmView(discord.ui.View):
    def __init__(
        self, team_id: int, on_confirm: Callable[[discord.Interaction, Team], Awaitable[None]]
    ) -> None:
        super().__init__(timeout=120)
        self.team_id = team_id
        self.on_confirm = on_confirm

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        team = await interaction.client.db.get_team(self.team_id)
        if team is None or team.status == "disbanded":
            await interaction.response.edit_message(
                content="This team no longer exists.", view=None
            )
            return
        if not await interaction.client.can_manage(team, interaction.user):
            await interaction.response.edit_message(
                content="You're no longer the leader.", view=None
            )
            return
        await interaction.response.edit_message(content="Working on it...", view=None)
        await self.on_confirm(interaction, team)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)


class ColourModal(discord.ui.Modal, title="Team role colour"):
    colour = discord.ui.TextInput(label="Hex colour", placeholder="#E67E22", max_length=7)

    def __init__(self, team_id: int) -> None:
        super().__init__()
        self.team_id = team_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        try:
            colour = discord.Colour.from_str(self.colour.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "That isn't a hex colour. Use the form `#E67E22`.", ephemeral=True
            )
            return
        team = await bot.db.get_team(self.team_id)
        role = bot.guild.get_role(team.role_id or 0) if team else None
        if role is None:
            await interaction.response.send_message("The team role is missing.", ephemeral=True)
            return
        await role.edit(colour=colour, reason=f"Colour set by {interaction.user}")
        await interaction.response.send_message(f"Team colour set to `{colour}`.", ephemeral=True)
        await bot.audit(
            f"{interaction.user.mention} set **{team.label}**'s colour to `{colour}`",
            "colour_set",
            actor=interaction.user,
            team=team,
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

"""Admin slash commands. Only members with the admin role (or Administrator) can use them."""

import csv
import io

import discord
from discord import app_commands
from discord.ext import commands

from . import embeds
from .albion import Character
from .db import Team, TeamError
from .discord_setup import rename_team_space
from .views.character import lookup_character
from .views.public_panel import PublicPanelView


async def team_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    teams = await interaction.client.db.list_teams(("forming", "active", "disbanded"))
    current = current.lower()
    return [
        app_commands.Choice(name=f"{t.label} ({t.status})"[:100], value=str(t.id))
        for t in teams
        if current in t.label.lower()
    ][:25]


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
    rosters = app_commands.Group(name="rosters", description="Lock or unlock team rosters")
    team = app_commands.Group(name="team", description="Manage any team (admins)")
    character = app_commands.Group(
        name="admin-character", description="Manage players' Albion characters (admins)"
    )

    async def _team(self, interaction: discord.Interaction, value: str) -> Team | None:
        team = await self.bot.db.get_team(int(value)) if value.isdigit() else None
        if team is None:
            await interaction.response.send_message(
                "No such team. Pick one from the list.", ephemeral=True
            )
        return team

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

    @rosters.command(name="lock", description="Freeze every team's members")
    @is_admin()
    async def rosters_lock(self, interaction: discord.Interaction) -> None:
        await self._set_rosters(interaction, True)

    @rosters.command(name="unlock", description="Let teams change members again")
    @is_admin()
    async def rosters_unlock(self, interaction: discord.Interaction) -> None:
        await self._set_rosters(interaction, False)

    async def _set_rosters(self, interaction: discord.Interaction, locked: bool) -> None:
        await self.bot.db.set_setting("rosters_locked", "1" if locked else "0")
        word = "locked" if locked else "unlocked"
        await interaction.response.send_message(f"Rosters {word}.", ephemeral=True)
        await self.bot.audit(
            f"{interaction.user.mention} {word} rosters", f"rosters_{word}", actor=interaction.user
        )

    @team.command(name="info", description="Show a team's full roster")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_info(self, interaction: discord.Interaction, team: str) -> None:
        if found := await self._team(interaction, team):
            members = await self.bot.db.list_members(found.id)
            await interaction.response.send_message(
                embed=embeds.manage_panel(found, members), ephemeral=True
            )

    @team.command(name="disband", description="Disband a team (soft delete)")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_disband(self, interaction: discord.Interaction, team: str) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        if found.status == "disbanded":
            await interaction.response.send_message("Already disbanded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.disband(found, actor=interaction.user)
        await interaction.followup.send(f"**{found.label}** disbanded.", ephemeral=True)

    @team.command(name="restore", description="Restore a disbanded team")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_restore(self, interaction: discord.Interaction, team: str) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            restored = await self.bot.restore(found, actor=interaction.user)
        except TeamError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"**{restored.label}** restored ({restored.status}).", ephemeral=True
        )

    @team.command(name="delete", description="Delete a team and its channels for good")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_delete(self, interaction: discord.Interaction, team: str) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.hard_delete(found, actor=interaction.user, why="deleted by an admin")
        await interaction.followup.send(f"**{found.label}** deleted.", ephemeral=True)

    @team.command(name="rename", description="Rename a team, its roles and category")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_rename(
        self,
        interaction: discord.Interaction,
        team: str,
        name: app_commands.Range[str, 2, 32],
    ) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        name = " ".join(name.split())
        try:
            await self.bot.db.rename_team(found.id, name)
        except TeamError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        renamed = await self.bot.db.get_team(found.id)
        await rename_team_space(self.bot.guild, renamed)
        await self.bot.refresh_manage_panel(renamed)
        await self.bot.refresh_public_panel()
        await interaction.followup.send(f"Renamed to **{renamed.label}**.", ephemeral=True)
        await self.bot.audit(
            f"{interaction.user.mention} renamed **{found.label}** to **{renamed.label}**",
            "team_renamed",
            actor=interaction.user,
            team=renamed,
        )

    @team.command(name="set-leader", description="Make a member the team leader")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_set_leader(
        self, interaction: discord.Interaction, team: str, member: discord.Member
    ) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.transfer_leader(found, member, actor=interaction.user)
        except TeamError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"{member.mention} now leads **{found.label}**.", ephemeral=True
        )

    @team.command(name="add", description="Put a player in a team, skipping every check")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_add(
        self, interaction: discord.Interaction, team: str, member: discord.Member
    ) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        await interaction.response.defer(ephemeral=True)
        current = await self.bot.db.current_team_of(member.id)
        try:
            if current and current.status != "disbanded" and current.id != found.id:
                await self.bot.remove_member(
                    current, member, actor=interaction.user, how="was moved out of"
                )
            await self.bot.add_member(found, member, actor=interaction.user, how="admin")
        except TeamError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.bot.dm(member, f"An admin added you to **{found.name}**.")
        await interaction.followup.send(
            f"{member.mention} added to **{found.label}**.", ephemeral=True
        )

    @team.command(name="remove", description="Take a player out of their team")
    @is_admin()
    async def team_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        current = await self.bot.db.current_team_of(member.id)
        if current is None or current.status == "disbanded":
            await interaction.response.send_message(
                f"{member.mention} isn't in a team.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.remove_member(
                current, member, actor=interaction.user, how="was removed from"
            )
        except TeamError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.bot.dm(member, f"An admin removed you from **{current.name}**.")
        await interaction.followup.send(
            f"{member.mention} removed from **{current.label}**.", ephemeral=True
        )

    @team.command(name="extend", description="Give a forming team more time to reach 3 members")
    @app_commands.autocomplete(team=team_autocomplete)
    @is_admin()
    async def team_extend(
        self,
        interaction: discord.Interaction,
        team: str,
        hours: app_commands.Range[int, 1, 336],
    ) -> None:
        found = await self._team(interaction, team)
        if not found:
            return
        if found.status != "forming":
            await interaction.response.send_message(
                "Only forming teams have a deadline.", ephemeral=True
            )
            return
        await self.bot.db.extend_deadline(found.id, hours)
        await self.bot.refresh_manage_panel(found)
        await interaction.response.send_message(
            f"**{found.label}** has {hours} more hours.", ephemeral=True
        )
        await self.bot.audit(
            f"{interaction.user.mention} gave **{found.label}** {hours} more hours",
            "deadline_extended",
            actor=interaction.user,
            team=found,
        )

    @character.command(name="set", description="Link an Albion character for another player")
    @app_commands.describe(name="The character's name on the Europe server")
    @is_admin()
    async def character_set(
        self, interaction: discord.Interaction, member: discord.Member, name: str
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def on_pick(select_interaction: discord.Interaction, picked: Character) -> None:
            bot = select_interaction.client
            await select_interaction.response.edit_message(
                content=f"{member.mention} is now linked to **{picked.name}**.", view=None
            )
            team = await bot.db.current_team_of(member.id)
            if team:
                await bot.refresh_manage_panel(team)
            await bot.audit(
                f"{select_interaction.user.mention} linked {member.mention} to {picked.name}",
                "character_set",
                actor=select_interaction.user,
                team=team,
                target=member,
            )

        await lookup_character(interaction, name.strip(), on_pick, for_user_id=member.id)

    @app_commands.command(name="export", description="Download every team and member as CSV")
    @is_admin()
    async def export(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "team_number",
                "team_name",
                "status",
                "role",
                "discord_id",
                "discord_name",
                "albion_name",
                "albion_id",
                "albion_guild",
            ]
        )
        for t in await db.list_teams(("forming", "active", "disbanded")):
            for m in await db.list_members(t.id):
                user = self.bot.guild.get_member(m.user_id)
                role = (
                    "leader" if m.user_id == t.leader_id else "helper" if m.is_helper else "member"
                )
                writer.writerow(
                    [
                        t.number,
                        t.name,
                        t.status,
                        role,
                        m.user_id,
                        user.display_name if user else "",
                        m.albion_name or "",
                        m.albion_id or "",
                        m.albion_guild or "",
                    ]
                )
        file = discord.File(io.BytesIO(out.getvalue().encode()), filename="teams.csv")
        await interaction.response.send_message(file=file, ephemeral=True)

    @app_commands.command(name="resync", description="Fix team roles and redraw every panel")
    @is_admin()
    async def resync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        fixed = 0
        guild = self.bot.guild
        for t in await self.bot.db.list_teams(("forming", "active")):
            members = await self.bot.db.list_members(t.id)
            wanted = {m.user_id for m in members}
            staff = {t.leader_id} | {m.user_id for m in members if m.is_helper}
            for role_id, should_have in ((t.role_id, wanted), (t.staff_role_id, staff)):
                role = guild.get_role(role_id or 0)
                if role is None:
                    continue
                for member in role.members:
                    if member.id not in should_have:
                        await member.remove_roles(role, reason="Resync")
                        fixed += 1
                for user_id in should_have:
                    member = guild.get_member(user_id)
                    if member and role not in member.roles:
                        await member.add_roles(role, reason="Resync")
                        fixed += 1
            await self.bot.refresh_manage_panel(t)
        await self.bot.refresh_public_panel()
        await interaction.followup.send(
            f"Resync done: {fixed} role changes, panels redrawn.", ephemeral=True
        )
        await self.bot.audit(
            f"{interaction.user.mention} ran a resync ({fixed} role changes)",
            "resync",
            actor=interaction.user,
        )


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))

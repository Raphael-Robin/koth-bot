import logging

import discord
from discord.ext import commands

from . import embeds
from .albion import AlbionClient
from .config import Config
from .db import MIN_MEMBERS, Database, JoinResult, Team, TeamError
from .discord_setup import (
    archive_team_space,
    delete_team_space,
    move_leader_access,
    restore_team_space,
)

log = logging.getLogger(__name__)


class TournamentBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.db = Database(config.db_path)
        self.albion = AlbionClient(config.albion_api_base)

    async def setup_hook(self) -> None:
        from .views import applications, invites, manage_panel, public_panel

        await self.db.connect()
        await self.albion.start()
        self.add_view(public_panel.PublicPanelView())
        self.add_dynamic_items(
            applications.ApplicationButton, invites.InviteButton, manage_panel.ManageButton
        )
        await self.load_extension("tournament_bot.admin")
        await self.load_extension("tournament_bot.lifecycle")
        await self.load_extension("tournament_bot.players")
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def close(self) -> None:
        await self.albion.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s environment)", self.user, self.config.env_name)
        for guild in self.guilds:
            await self._leave_if_foreign(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._leave_if_foreign(guild)

    async def _leave_if_foreign(self, guild: discord.Guild) -> None:
        # Each environment is its own bot application bound to one server. Staying in a second
        # server would let its buttons reach this environment's database.
        if guild.id != self.config.guild_id:
            log.warning("Leaving %s (%s): not this environment's server", guild.name, guild.id)
            await guild.leave()

    @property
    def guild(self) -> discord.Guild:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            raise RuntimeError("The bot is not in the configured guild")
        return guild

    @property
    def admin_role(self) -> discord.Role | None:
        return self.guild.get_role(self.config.admin_role_id)

    def is_admin(self, user: discord.abc.User) -> bool:
        if not isinstance(user, discord.Member):
            user = self.guild.get_member(user.id)
        if user is None:
            return False
        return user.guild_permissions.administrator or any(
            r.id == self.config.admin_role_id for r in user.roles
        )

    async def can_manage(self, team: Team, user: discord.abc.User) -> bool:
        return user.id == team.leader_id or self.is_admin(user)

    async def can_staff(self, team: Team, user: discord.abc.User) -> bool:
        return await self.db.is_staff(team.id, user.id) or self.is_admin(user)

    # Audit log

    async def audit(
        self,
        text: str,
        action: str,
        *,
        actor: discord.abc.User | None = None,
        team: Team | None = None,
        target: discord.abc.User | None = None,
        ping_admins: bool = False,
    ) -> None:
        await self.db.audit(
            action,
            actor_id=actor.id if actor else None,
            team_id=team.id if team else None,
            target_id=target.id if target else None,
            details=text,
        )
        channel = self.guild.get_channel(self.config.log_channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                mentions = discord.AllowedMentions.none()
                if ping_admins and self.admin_role:
                    mentions = discord.AllowedMentions(users=False, roles=[self.admin_role])
                await channel.send(text, allowed_mentions=mentions)
            except discord.HTTPException:
                log.exception("Could not post to the log channel")

    # Panels

    async def refresh_public_panel(self) -> None:
        channel_id = await self.db.get_setting("panel_channel_id")
        message_id = await self.db.get_setting("panel_message_id")
        if not channel_id or not message_id:
            return
        channel = self.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        teams = []
        for team in await self.db.list_teams(("active",)):
            count = await self.db.member_count(team.id)
            if count >= MIN_MEMBERS:
                teams.append((team, count))
        embed = embeds.public_panel(teams, await self.db.signups_open(), self.config.is_test)
        try:
            await channel.get_partial_message(int(message_id)).edit(embed=embed)
        except discord.NotFound:
            log.warning("Public panel message is gone; run /setup panel again")

    async def refresh_manage_panel(self, team: Team) -> None:
        from .views.manage_panel import manage_view

        team = await self.db.get_team(team.id)
        if not team or not team.manage_channel_id or not team.panel_message_id:
            return
        channel = self.guild.get_channel(team.manage_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        members = await self.db.list_members(team.id)
        try:
            await channel.get_partial_message(team.panel_message_id).edit(
                embed=embeds.manage_panel(team, members),
                view=manage_view(team) if team.status != "disbanded" else None,
            )
        except discord.NotFound:
            log.warning("Manage panel for %s is gone", team.label)

    # Membership side effects

    async def add_member(
        self, team: Team, user: discord.abc.User, *, actor: discord.abc.User, how: str
    ) -> JoinResult:
        """Add a player to a team in the database, then bring Discord in line with it.

        Raises TeamError when a rule forbids the join.
        """
        result = await self.db.join_team(team.id, user.id)
        member = self.guild.get_member(user.id)
        role = self.guild.get_role(team.role_id) if team.role_id else None
        if member and role:
            await member.add_roles(role, reason=f"Joined {team.label}")

        for app in result.removed_applications:
            await self._delete_message(app.channel_id, app.message_id)
        for invite in result.expired_invites:
            await self._expire_invite_message(invite.channel_id, invite.message_id)

        await self.refresh_manage_panel(team)
        if result.became_active or (await self.db.get_team(team.id)).status == "active":
            await self.refresh_public_panel()
        await self.audit(
            f"{user.mention} joined **{team.label}** ({how}, by {actor.mention})",
            "member_joined",
            actor=actor,
            team=team,
            target=user,
        )
        if result.became_active:
            await self.audit(
                f"**{team.label}** reached {MIN_MEMBERS} members and is now on the panel",
                "team_validated",
                team=team,
            )
        return result

    async def remove_member(
        self,
        team: Team,
        user: discord.abc.User,
        *,
        actor: discord.abc.User,
        how: str,
        allow_leader: bool = False,
    ) -> None:
        """Take a player out of a team. `how` reads as "<player> <how> <team>"."""
        await self.db.leave_team(team.id, user.id, allow_leader=allow_leader)
        member = self.guild.get_member(user.id)
        roles = [
            r
            for r in (
                self.guild.get_role(team.role_id or 0),
                self.guild.get_role(team.staff_role_id or 0),
            )
            if r
        ]
        if member and roles:
            await member.remove_roles(*roles, reason=f"{how} {team.label}")
        await self.refresh_manage_panel(team)
        await self.refresh_public_panel()
        await self.audit(
            f"{user.mention} {how} **{team.label}**"
            + (f" (by {actor.mention})" if actor.id != user.id else ""),
            "member_left",
            actor=actor,
            team=team,
            target=user,
        )

    async def set_helper(
        self, team: Team, user: discord.abc.User, helper: bool, *, actor: discord.abc.User
    ) -> None:
        await self.db.set_helper(team.id, user.id, helper)
        member = self.guild.get_member(user.id)
        staff_role = self.guild.get_role(team.staff_role_id or 0)
        if member and staff_role:
            if helper:
                await member.add_roles(staff_role, reason=f"Helper of {team.label}")
            else:
                await member.remove_roles(staff_role, reason=f"No longer helper of {team.label}")
        await self.refresh_manage_panel(team)
        word = "made" if helper else "removed"
        await self.audit(
            f"{actor.mention} {word} {user.mention} {'a helper of' if helper else 'as helper of'} "
            f"**{team.label}**",
            "helper_set" if helper else "helper_unset",
            actor=actor,
            team=team,
            target=user,
        )

    async def transfer_leader(
        self, team: Team, new_leader: discord.Member, *, actor: discord.abc.User
    ) -> None:
        old_leader_id = team.leader_id
        await self.db.transfer_leader(team.id, new_leader.id)
        await move_leader_access(self.guild, team, old_leader_id, new_leader)
        team = await self.db.get_team(team.id)
        await self.refresh_manage_panel(team)
        await self.refresh_public_panel()
        channel = self.guild.get_channel(team.manage_channel_id or 0)
        if channel:
            await channel.send(
                f"{new_leader.mention}, you are now the leader of **{team.name}**. "
                "This panel and its buttons are yours.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        await self.audit(
            f"<@{old_leader_id}> handed **{team.label}** to {new_leader.mention} "
            f"(by {actor.mention})",
            "leader_changed",
            actor=actor,
            team=team,
            target=new_leader,
        )

    async def disband(self, team: Team, *, actor: discord.abc.User) -> None:
        apps, invites = await self.db.disband_team(team.id)
        for app in apps:
            await self._delete_message(app.channel_id, app.message_id)
        for invite in invites:
            await self._expire_invite_message(
                invite.channel_id, invite.message_id, "This invite expired: the team disbanded."
            )
        await archive_team_space(self.guild, team, self.admin_role)
        team = await self.db.get_team(team.id)
        await self.refresh_manage_panel(team)
        await self.refresh_public_panel()
        await self.audit(
            f"{actor.mention} disbanded **{team.label}**", "team_disbanded", actor=actor, team=team
        )

    async def restore(self, team: Team, *, actor: discord.abc.User) -> Team:
        """Raises TeamError if the team can't come back."""
        team = await self.db.restore_team(team.id)
        members = await self.db.list_members(team.id)
        staff = [team.leader_id, *(m.user_id for m in members if m.is_helper)]
        try:
            ids = await restore_team_space(
                self.guild, team, [m.user_id for m in members], staff, self.admin_role
            )
        except discord.HTTPException as exc:
            await self.db.disband_team(team.id)
            raise TeamError(
                "Discord refused to recreate the team's roles. Please try again."
            ) from exc
        await self.db.set_team_discord_ids(team.id, **ids)
        team = await self.db.get_team(team.id)
        await self.refresh_manage_panel(team)
        await self.refresh_public_panel()
        channel = self.guild.get_channel(team.manage_channel_id or 0)
        if channel:
            note = (
                ""
                if team.status == "active"
                else f" You have 3 days to get back to {MIN_MEMBERS} members."
            )
            await channel.send(
                f"<@{team.leader_id}>, **{team.name}** is back with {len(members)} members.{note}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        await self.audit(
            f"{actor.mention} restored **{team.label}** ({len(members)} members)",
            "team_restored",
            actor=actor,
            team=team,
        )
        return team

    async def hard_delete(self, team: Team, *, actor: discord.abc.User | None, why: str) -> None:
        _, invites = await self.db.pending_for_team(team.id)
        for invite in invites:
            await self._expire_invite_message(
                invite.channel_id, invite.message_id, "This invite expired: the team was deleted."
            )
        await delete_team_space(self.guild, team)
        await self.db.hard_delete_team(team.id)
        await self.refresh_public_panel()
        by = f" by {actor.mention}" if actor else ""
        await self.audit(
            f"**{team.label}** was deleted{by}: {why}",
            "team_deleted",
            actor=actor,
            team=team,
        )

    async def _delete_message(self, channel_id: int | None, message_id: int | None) -> None:
        if not channel_id or not message_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.HTTPException:
            pass

    async def _expire_invite_message(
        self,
        channel_id: int | None,
        message_id: int | None,
        text: str = "This invite has expired because you joined a team.",
    ) -> None:
        if not channel_id or not message_id:
            return
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            await channel.get_partial_message(message_id).edit(content=text, view=None)
        except discord.HTTPException:
            pass

    async def dm(self, user: discord.abc.User, content: str, **kwargs) -> discord.Message | None:
        try:
            return await user.send(content, **kwargs)
        except discord.HTTPException:
            return None

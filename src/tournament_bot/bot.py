import logging

import discord
from discord.ext import commands

from . import embeds
from .albion import AlbionClient
from .config import Config
from .db import MIN_MEMBERS, Database, JoinResult, Team

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
                await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
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
                embed=embeds.manage_panel(team, members), view=manage_view(team)
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

    async def _expire_invite_message(self, channel_id: int | None, message_id: int | None) -> None:
        if not channel_id or not message_id:
            return
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            await channel.get_partial_message(message_id).edit(
                content="This invite has expired because you joined a team.", view=None
            )
        except discord.HTTPException:
            pass

    async def dm(self, user: discord.abc.User, content: str, **kwargs) -> discord.Message | None:
        try:
            return await user.send(content, **kwargs)
        except discord.HTTPException:
            return None

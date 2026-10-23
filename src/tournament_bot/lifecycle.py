"""Background rules: the 3-day minimum, invite expiry, and players leaving the server."""

import logging
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands, tasks

from .db import MIN_MEMBERS, TeamError
from .embeds import FORMING_DEADLINE

log = logging.getLogger(__name__)

WARNING_AFTER = timedelta(hours=48)
INVITE_LIFETIME = timedelta(hours=48)


class Lifecycle(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.sweep.start()

    async def cog_unload(self) -> None:
        self.sweep.cancel()

    @tasks.loop(minutes=5)
    async def sweep(self) -> None:
        try:
            await self.check_forming_teams()
            await self.expire_invites()
        except Exception:
            log.exception("Lifecycle sweep failed")

    @sweep.before_loop
    async def before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def check_forming_teams(self) -> None:
        bot = self.bot
        now = datetime.now(UTC)
        for team in await bot.db.list_teams(("forming",)):
            age = now - datetime.fromisoformat(team.created_at)
            if age >= FORMING_DEADLINE:
                leader = bot.get_user(team.leader_id)
                await bot.hard_delete(
                    team, actor=None, why=f"fewer than {MIN_MEMBERS} members after 3 days"
                )
                if leader:
                    await bot.dm(
                        leader,
                        f"**{team.name}** was deleted because it didn't reach {MIN_MEMBERS} "
                        "members within 3 days. You can create a new team any time.",
                    )
            elif age >= WARNING_AFTER and not team.warned_at:
                await bot.db.mark_warned(team.id)
                deadline = int(
                    (datetime.fromisoformat(team.created_at) + FORMING_DEADLINE).timestamp()
                )
                text = (
                    f"<@{team.leader_id}>, **{team.name}** has "
                    f"{await bot.db.member_count(team.id)} members. It needs {MIN_MEMBERS} by "
                    f"<t:{deadline}:F> (<t:{deadline}:R>) or it will be deleted."
                )
                channel = bot.guild.get_channel(team.manage_channel_id or 0)
                if channel:
                    await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
                leader = bot.get_user(team.leader_id)
                if leader:
                    await bot.dm(leader, text)

    async def expire_invites(self) -> None:
        cutoff = (datetime.now(UTC) - INVITE_LIFETIME).isoformat(timespec="seconds")
        for invite in await self.bot.db.expire_old_invites(cutoff):
            await self.bot._expire_invite_message(
                invite.channel_id, invite.message_id, "This invite expired after 48 hours."
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        bot = self.bot
        if member.guild.id != bot.config.guild_id:
            return
        team = await bot.db.current_team_of(member.id)
        if team is None or team.status == "disbanded":
            return

        if team.leader_id == member.id:
            helpers = [m for m in await bot.db.list_members(team.id) if m.is_helper]
            successor = next(
                (
                    bot.guild.get_member(h.user_id)
                    for h in helpers
                    if bot.guild.get_member(h.user_id)
                ),
                None,
            )
            if successor:
                await bot.transfer_leader(team, successor, actor=bot.user)
            else:
                admin = bot.admin_role
                await bot.audit(
                    f"{admin.mention if admin else 'Admins'}: the leader of **{team.label}** left "
                    "the server and the team has no helper to take over. Use /team set-leader.",
                    "leader_left",
                    team=team,
                    target=member,
                    ping_admins=True,
                )
            team = await bot.db.get_team(team.id)

        try:
            await bot.remove_member(
                team,
                member,
                actor=member,
                how="left the server and dropped out of",
                allow_leader=True,
            )
        except TeamError:
            pass


async def setup(bot) -> None:
    await bot.add_cog(Lifecycle(bot))

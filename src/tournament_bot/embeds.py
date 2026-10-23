"""Embeds for the panels and messages the bot posts."""

from datetime import datetime, timedelta

import discord

from .db import MIN_MEMBERS, Member, Player, Team

FORMING_DEADLINE = timedelta(days=3)
COLOUR = discord.Colour.dark_gold()


def public_panel(teams: list[tuple[Team, int]], signups_open: bool) -> discord.Embed:
    """Only validated teams (3+ members) are listed, by name, without their number."""
    embed = discord.Embed(title="King of the Hill: teams", colour=COLOUR)
    if teams:
        embed.description = "\n".join(
            f"**{team.name}**, led by <@{team.leader_id}> ({count} members)"
            for team, count in teams
        )
    else:
        embed.description = "No teams yet. Be the first to create one."
    status = "open" if signups_open else "closed"
    embed.set_footer(text=f"{len(teams)} teams signed up · sign-ups {status}")
    return embed


def character_line(who: Member | Player) -> str:
    if not who.albion_name:
        return "no character linked"
    guild = f" [{who.albion_guild}]" if who.albion_guild else ""
    return f"{who.albion_name}{guild}"


def manage_panel(team: Team, members: list[Member]) -> discord.Embed:
    embed = discord.Embed(title=team.label, colour=COLOUR)
    embed.add_field(name="Leader", value=f"<@{team.leader_id}>")
    helpers = [m for m in members if m.is_helper]
    embed.add_field(name="Helpers", value=", ".join(f"<@{m.user_id}>" for m in helpers) or "none")
    embed.add_field(name="Status", value=team.status.capitalize())
    roster = "\n".join(f"<@{m.user_id}>: {character_line(m)}" for m in members)
    embed.add_field(name=f"Members ({len(members)})", value=roster[:1024] or "none", inline=False)
    if team.status == "forming":
        deadline = datetime.fromisoformat(team.created_at) + FORMING_DEADLINE
        embed.add_field(
            name="Deadline",
            value=f"Reach {MIN_MEMBERS} members by <t:{int(deadline.timestamp())}:F> "
            f"(<t:{int(deadline.timestamp())}:R>) or the team is deleted.",
            inline=False,
        )
    return embed


def welcome(team: Team) -> str:
    return (
        f"<@{team.leader_id}>, your team **{team.name}** is set up. This channel is only "
        "visible to you. Here's how it works:\n\n"
        "- **Invite member** sends a player a private invite they can accept or reject.\n"
        "- **#team-applications** gets one message per player who applies from the public "
        "panel. You and your helpers accept or deny them there, and get pinged for each one.\n"
        "- **#mass** is where you and your helpers post calls and ping the team role. "
        "Members can only reply in threads.\n"
        "- **#team-chat** is open to the whole team.\n"
        "- More controls (remove member, helpers, role colour, disband) appear on the panel "
        "above as they're added.\n\n"
        f"Your team needs **{MIN_MEMBERS} members within 3 days**, or it is deleted. Until "
        "then it doesn't show on the public panel."
    )


def application(user: discord.abc.User, player: Player | None) -> discord.Embed:
    embed = discord.Embed(title="New application", colour=COLOUR)
    embed.add_field(name="Player", value=user.mention)
    if player:
        embed.add_field(name="Character", value=character_line(player))
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed

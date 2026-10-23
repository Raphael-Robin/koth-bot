"""Creates and removes the Discord roles, category and channels that belong to a team."""

import discord

from .db import Team

CHANNEL_NAMES = {
    "manage_channel_id": "manage-team",
    "applications_channel_id": "team-applications",
    "mass_channel_id": "mass",
    "chat_channel_id": "team-chat",
}

# Discord refuses to create more than 50 categories in one server.
DISCORD_CATEGORY_LIMIT = 50


def _overwrites(
    guild: discord.Guild,
    channel: str,
    team_role: discord.Role,
    staff_role: discord.Role,
    leader: discord.abc.Snowflake,
    admin_role: discord.Role | None,
) -> dict:
    hidden = discord.PermissionOverwrite(view_channel=False)
    see = {"view_channel": True, "read_message_history": True}
    overwrites: dict = {
        guild.default_role: hidden,
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True,
            read_message_history=True,
            mention_everyone=True,
        ),
    }
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(**see, send_messages=True)

    if channel == "manage_channel_id":
        overwrites[team_role] = hidden
        overwrites[staff_role] = hidden
        overwrites[leader] = discord.PermissionOverwrite(**see, send_messages=False)
    elif channel == "applications_channel_id":
        overwrites[team_role] = hidden
        overwrites[staff_role] = discord.PermissionOverwrite(**see, send_messages=False)
    elif channel == "mass_channel_id":
        overwrites[team_role] = discord.PermissionOverwrite(
            **see,
            send_messages=False,
            send_messages_in_threads=True,
            create_public_threads=True,
        )
        # Mention Everyone lets staff ping the team role without making it mentionable by all.
        overwrites[staff_role] = discord.PermissionOverwrite(
            **see, send_messages=True, mention_everyone=True
        )
    elif channel == "chat_channel_id":
        overwrites[team_role] = discord.PermissionOverwrite(**see, send_messages=True)
    return overwrites


async def create_team_space(
    guild: discord.Guild, team: Team, leader: discord.Member, admin_role: discord.Role | None
) -> dict[str, int]:
    """Create roles, category and channels. On failure, delete what was made and re-raise."""
    created: list = []
    try:
        team_role = await guild.create_role(
            name=team.label, mentionable=False, reason="Team created"
        )
        created.append(team_role)
        staff_role = await guild.create_role(
            name=f"{team.label} Staff", mentionable=False, reason="Team created"
        )
        created.append(staff_role)
        await leader.add_roles(team_role, staff_role, reason="Team leader")

        category = await guild.create_category(
            team.label,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
            },
            reason="Team created",
        )
        created.append(category)

        ids = {"role_id": team_role.id, "staff_role_id": staff_role.id, "category_id": category.id}
        for column, name in CHANNEL_NAMES.items():
            channel = await guild.create_text_channel(
                name,
                category=category,
                overwrites=_overwrites(guild, column, team_role, staff_role, leader, admin_role),
                reason="Team created",
            )
            created.append(channel)
            ids[column] = channel.id
        return ids
    except Exception:
        for obj in reversed(created):
            try:
                await obj.delete(reason="Rolling back failed team creation")
            except discord.HTTPException:
                pass
        raise


async def delete_team_space(guild: discord.Guild, team: Team) -> None:
    """Remove every Discord object of a team. Missing objects are skipped."""
    channel_ids = [getattr(team, column) for column in CHANNEL_NAMES]
    for object_id in [*channel_ids, team.category_id]:
        channel = guild.get_channel(object_id) if object_id else None
        if channel:
            try:
                await channel.delete(reason="Team deleted")
            except discord.NotFound:
                pass
    for role_id in (team.role_id, team.staff_role_id):
        role = guild.get_role(role_id) if role_id else None
        if role:
            try:
                await role.delete(reason="Team deleted")
            except discord.NotFound:
                pass

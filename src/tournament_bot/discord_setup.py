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


def _channels(guild: discord.Guild, team: Team) -> dict[str, discord.TextChannel]:
    found = {}
    for column in CHANNEL_NAMES:
        channel = guild.get_channel(getattr(team, column) or 0)
        if isinstance(channel, discord.TextChannel):
            found[column] = channel
    return found


async def archive_team_space(
    guild: discord.Guild, team: Team, admin_role: discord.Role | None
) -> None:
    """Disband: delete both roles and leave the channels read-only for the leader and admins."""
    for role_id in (team.role_id, team.staff_role_id):
        role = guild.get_role(role_id) if role_id else None
        if role:
            try:
                await role.delete(reason="Team disbanded")
            except discord.NotFound:
                pass

    read_only = discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        add_reactions=False,
    )
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, manage_messages=True
        ),
    }
    if admin_role:
        overwrites[admin_role] = read_only
    leader = guild.get_member(team.leader_id)
    if leader:
        overwrites[leader] = read_only
    for channel in _channels(guild, team).values():
        await channel.edit(overwrites=overwrites, reason="Team disbanded")


async def restore_team_space(
    guild: discord.Guild,
    team: Team,
    member_ids: list[int],
    staff_ids: list[int],
    admin_role: discord.Role | None,
) -> dict[str, int]:
    """Recreate the roles of a disbanded team, hand them back and reopen its channels."""
    team_role = await guild.create_role(name=team.label, mentionable=False, reason="Team restored")
    try:
        staff_role = await guild.create_role(
            name=f"{team.label} Staff", mentionable=False, reason="Team restored"
        )
    except discord.HTTPException:
        await team_role.delete(reason="Rolling back failed restore")
        raise
    for user_id in member_ids:
        member = guild.get_member(user_id)
        if member:
            roles = [team_role, staff_role] if user_id in staff_ids else [team_role]
            await member.add_roles(*roles, reason="Team restored")

    leader = guild.get_member(team.leader_id) or discord.Object(team.leader_id, type=discord.Member)
    for column, channel in _channels(guild, team).items():
        await channel.edit(
            overwrites=_overwrites(guild, column, team_role, staff_role, leader, admin_role),
            reason="Team restored",
        )
    return {"role_id": team_role.id, "staff_role_id": staff_role.id}


async def move_leader_access(
    guild: discord.Guild, team: Team, old_leader_id: int, new_leader: discord.Member
) -> None:
    """Hand #manage-team and the staff role from the old leader to the new one."""
    channel = guild.get_channel(team.manage_channel_id or 0)
    if isinstance(channel, discord.TextChannel):
        await channel.set_permissions(
            discord.Object(old_leader_id, type=discord.Member), overwrite=None
        )
        await channel.set_permissions(
            new_leader, view_channel=True, read_message_history=True, send_messages=False
        )
    staff_role = guild.get_role(team.staff_role_id or 0)
    if staff_role:
        await new_leader.add_roles(staff_role, reason="New team leader")
        old = guild.get_member(old_leader_id)
        if old:
            await old.remove_roles(staff_role, reason="No longer team leader")


async def rename_team_space(guild: discord.Guild, team: Team) -> None:
    """Rename roles and category to match team.label."""
    for role_id, name in ((team.role_id, team.label), (team.staff_role_id, f"{team.label} Staff")):
        role = guild.get_role(role_id or 0)
        if role:
            await role.edit(name=name, reason="Team renamed")
    category = guild.get_channel(team.category_id or 0)
    if category:
        await category.edit(name=team.label, reason="Team renamed")

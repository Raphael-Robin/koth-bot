import pytest

from tournament_bot.db import Database, TeamError


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def test_create_team_adds_leader_and_numbers_increase(db):
    first = await db.create_team("Wolves", 1)
    second = await db.create_team("Bears", 2)
    assert (first.number, second.number) == (1, 2)
    assert first.status == "forming"
    assert [m.user_id for m in await db.list_members(first.id)] == [1]


async def test_team_numbers_are_never_reused(db):
    first = await db.create_team("Wolves", 1)
    await db.hard_delete_team(first.id)
    second = await db.create_team("Bears", 2)
    assert second.number == 2


async def test_duplicate_names_are_refused_ignoring_case(db):
    await db.create_team("Wolves", 1)
    with pytest.raises(TeamError):
        await db.create_team("wolves", 2)


async def test_player_cannot_be_in_two_teams(db):
    wolves = await db.create_team("Wolves", 1)
    await db.create_team("Bears", 2)
    with pytest.raises(TeamError):
        await db.create_team("Foxes", 1)
    await db.join_team(wolves.id, 3)
    bears = (await db.list_teams())[1]
    with pytest.raises(TeamError):
        await db.join_team(bears.id, 3)


async def test_joining_leaves_a_disbanded_team(db):
    wolves = await db.create_team("Wolves", 1)
    await db.join_team(wolves.id, 3)
    await db.conn.execute("UPDATE teams SET status = 'disbanded' WHERE id = ?", (wolves.id,))
    bears = await db.create_team("Bears", 2)
    result = await db.join_team(bears.id, 3)
    assert result.left_disbanded_team == wolves.id
    assert (await db.current_team_of(3)).id == bears.id


async def test_team_becomes_active_at_three_members(db):
    team = await db.create_team("Wolves", 1)
    assert not (await db.join_team(team.id, 2)).became_active
    assert (await db.join_team(team.id, 3)).became_active
    assert (await db.get_team(team.id)).status == "active"


async def test_new_application_replaces_the_previous_one(db):
    wolves = await db.create_team("Wolves", 1)
    bears = await db.create_team("Bears", 2)
    first, replaced = await db.create_application(wolves.id, 9)
    assert replaced is None
    second, replaced = await db.create_application(bears.id, 9)
    assert replaced.id == first.id
    assert await db.get_application(first.id) is None
    assert second.status == "pending"


async def test_application_can_only_be_decided_once(db):
    team = await db.create_team("Wolves", 1)
    app, _ = await db.create_application(team.id, 9)
    assert await db.decide_application(app.id, "accepted", 1)
    assert not await db.decide_application(app.id, "denied", 1)


async def test_joining_clears_pending_applications_and_invites(db):
    wolves = await db.create_team("Wolves", 1)
    bears = await db.create_team("Bears", 2)
    app, _ = await db.create_application(wolves.id, 9)
    invite = await db.create_invite(wolves.id, 9, 1)
    result = await db.join_team(bears.id, 9)
    assert [a.id for a in result.removed_applications] == [app.id]
    assert [i.id for i in result.expired_invites] == [invite.id]
    assert (await db.get_invite(invite.id)).status == "expired"


async def test_staff_is_leader_or_helper(db):
    team = await db.create_team("Wolves", 1)
    await db.join_team(team.id, 2)
    await db.join_team(team.id, 3)
    await db.conn.execute("UPDATE members SET is_helper = 1 WHERE user_id = 2")
    assert await db.is_staff(team.id, 1)
    assert await db.is_staff(team.id, 2)
    assert not await db.is_staff(team.id, 3)


async def test_player_character_is_upserted(db):
    await db.set_player_character(9, "abc", "Typo", None)
    await db.set_player_character(9, "def", "Fixed", "Guild")
    player = await db.get_player(9)
    assert (player.albion_id, player.albion_name, player.albion_guild) == ("def", "Fixed", "Guild")


async def _full_team(db, name="Wolves", leader=1, others=(2, 3)):
    team = await db.create_team(name, leader)
    for user_id in others:
        await db.join_team(team.id, user_id)
    return await db.get_team(team.id)


async def test_leader_cannot_leave_but_members_can(db):
    team = await _full_team(db)
    with pytest.raises(TeamError):
        await db.leave_team(team.id, 1)
    await db.leave_team(team.id, 2)
    assert await db.member_count(team.id) == 2
    # Dropping under 3 after validation does not un-validate the team.
    assert (await db.get_team(team.id)).status == "active"


async def test_left_player_can_rejoin(db):
    team = await _full_team(db)
    await db.leave_team(team.id, 2)
    await db.join_team(team.id, 2)
    assert await db.member_count(team.id) == 3


async def test_disband_clears_pending_and_keeps_members(db):
    team = await _full_team(db)
    app, _ = await db.create_application(team.id, 9)
    invite = await db.create_invite(team.id, 8, 1)
    apps, invites = await db.disband_team(team.id)
    assert [a.id for a in apps] == [app.id]
    assert [i.id for i in invites] == [invite.id]
    assert await db.get_application(app.id) is None
    disbanded = await db.get_team(team.id)
    assert disbanded.status == "disbanded" and disbanded.role_id is None
    assert await db.member_count(team.id) == 3
    assert (await db.disbanded_team_led_by(1)).id == team.id


async def test_restore_brings_back_remaining_members(db):
    team = await _full_team(db)
    await db.disband_team(team.id)
    other = await db.create_team("Bears", 7)
    await db.join_team(other.id, 3)  # player 3 moves on and leaves the disbanded team
    restored = await db.restore_team(team.id)
    assert restored.status == "forming"
    assert restored.warned_at is None
    assert sorted(m.user_id for m in await db.list_members(team.id)) == [1, 2]


async def test_restore_with_enough_members_is_active(db):
    team = await _full_team(db)
    await db.disband_team(team.id)
    assert (await db.restore_team(team.id)).status == "active"


async def test_restore_refused_when_name_reused(db):
    team = await _full_team(db)
    await db.disband_team(team.id)
    await db.create_team("wolves", 7)
    with pytest.raises(TeamError):
        await db.restore_team(team.id)


async def test_transfer_leader_requires_membership(db):
    team = await _full_team(db)
    await db.set_helper(team.id, 2, True)
    with pytest.raises(TeamError):
        await db.transfer_leader(team.id, 99)
    await db.transfer_leader(team.id, 2)
    assert (await db.get_team(team.id)).leader_id == 2
    assert not any(m.is_helper for m in await db.list_members(team.id))
    assert await db.is_staff(team.id, 2)
    assert not await db.is_staff(team.id, 1)


async def test_old_invites_expire(db):
    team = await db.create_team("Wolves", 1)
    invite = await db.create_invite(team.id, 9, 1)
    assert await db.expire_old_invites("2000-01-01T00:00:00+00:00") == []
    expired = await db.expire_old_invites("2999-01-01T00:00:00+00:00")
    assert [i.id for i in expired] == [invite.id]

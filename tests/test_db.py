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

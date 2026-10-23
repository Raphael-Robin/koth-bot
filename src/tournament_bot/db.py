"""SQLite storage. Every rule that must survive a restart lives here."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

MIN_MEMBERS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    albion_id TEXT NOT NULL,
    albion_name TEXT NOT NULL,
    albion_guild TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('forming', 'active', 'disbanded')),
    leader_id INTEGER NOT NULL,
    role_id INTEGER,
    staff_role_id INTEGER,
    category_id INTEGER,
    manage_channel_id INTEGER,
    applications_channel_id INTEGER,
    mass_channel_id INTEGER,
    chat_channel_id INTEGER,
    panel_message_id INTEGER,
    created_at TEXT NOT NULL,
    disbanded_at TEXT,
    warned_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS teams_live_name
    ON teams (lower(name)) WHERE status != 'disbanded';

-- left_at IS NULL means current. A disbanded team keeps its open memberships so it can be
-- restored, which is why the one-team index covers disbanded teams too.
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    is_helper INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    left_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS members_one_team
    ON members (user_id) WHERE left_at IS NULL;

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'denied')),
    channel_id INTEGER,
    message_id INTEGER,
    created_at TEXT NOT NULL,
    decided_by INTEGER,
    decided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS applications_one_pending
    ON applications (user_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    invited_by INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'expired')),
    channel_id INTEGER,
    message_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_id INTEGER,
    action TEXT NOT NULL,
    team_id INTEGER,
    target_id INTEGER,
    details TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TeamError(Exception):
    """A rule was broken; the message is safe to show to the player."""


@dataclass
class Team:
    id: int
    number: int
    name: str
    status: str
    leader_id: int
    role_id: int | None
    staff_role_id: int | None
    category_id: int | None
    manage_channel_id: int | None
    applications_channel_id: int | None
    mass_channel_id: int | None
    chat_channel_id: int | None
    panel_message_id: int | None
    created_at: str
    disbanded_at: str | None
    warned_at: str | None

    @property
    def label(self) -> str:
        return f"Team {self.number} - {self.name}"


@dataclass
class Member:
    user_id: int
    is_helper: bool
    joined_at: str
    albion_id: str | None
    albion_name: str | None
    albion_guild: str | None


@dataclass
class Player:
    user_id: int
    albion_id: str
    albion_name: str
    albion_guild: str | None


@dataclass
class Application:
    id: int
    team_id: int
    user_id: int
    status: str
    channel_id: int | None
    message_id: int | None


@dataclass
class Invite:
    id: int
    team_id: int
    user_id: int
    invited_by: int
    status: str
    channel_id: int | None
    message_id: int | None


@dataclass
class JoinResult:
    """What else changed when a player joined, so the caller can update Discord to match."""

    left_disbanded_team: int | None = None
    removed_applications: list[Application] = field(default_factory=list)
    expired_invites: list[Invite] = field(default_factory=list)
    became_active: bool = False


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        # One connection is shared by every coroutine, so writes take this lock to keep
        # transactions from interleaving at an await.
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.executescript(SCHEMA)

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    @asynccontextmanager
    async def transaction(self):
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")

    async def _one(self, sql: str, params=()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params=()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    # Settings

    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    async def signups_open(self) -> bool:
        return await self.get_setting("signups_open") != "0"

    async def rosters_locked(self) -> bool:
        return await self.get_setting("rosters_locked") == "1"

    # Players

    async def set_player_character(
        self, user_id: int, albion_id: str, albion_name: str, albion_guild: str | None
    ) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO players (user_id, albion_id, albion_name, albion_guild, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
                "albion_id = excluded.albion_id, albion_name = excluded.albion_name, "
                "albion_guild = excluded.albion_guild, updated_at = excluded.updated_at",
                (user_id, albion_id, albion_name, albion_guild, now()),
            )

    async def get_player(self, user_id: int) -> Player | None:
        row = await self._one(
            "SELECT user_id, albion_id, albion_name, albion_guild FROM players WHERE user_id = ?",
            (user_id,),
        )
        return Player(**dict(row)) if row else None

    # Teams

    async def get_team(self, team_id: int) -> Team | None:
        row = await self._one("SELECT * FROM teams WHERE id = ?", (team_id,))
        return Team(**dict(row)) if row else None

    async def list_teams(self, statuses: tuple[str, ...] = ("forming", "active")) -> list[Team]:
        marks = ",".join("?" * len(statuses))
        rows = await self._all(
            f"SELECT * FROM teams WHERE status IN ({marks}) ORDER BY number", statuses
        )
        return [Team(**dict(r)) for r in rows]

    async def current_team_of(self, user_id: int) -> Team | None:
        """The team the player currently belongs to, disbanded teams included."""
        row = await self._one(
            "SELECT t.* FROM teams t JOIN members m ON m.team_id = t.id "
            "WHERE m.user_id = ? AND m.left_at IS NULL",
            (user_id,),
        )
        return Team(**dict(row)) if row else None

    async def name_taken(self, name: str) -> bool:
        row = await self._one(
            "SELECT 1 FROM teams WHERE lower(name) = lower(?) AND status != 'disbanded'", (name,)
        )
        return row is not None

    async def create_team(self, name: str, leader_id: int) -> Team:
        """Insert the team and its leader. Discord IDs are filled in afterwards."""
        async with self.transaction() as conn:
            existing = await self._one(
                "SELECT t.status FROM teams t JOIN members m ON m.team_id = t.id "
                "WHERE m.user_id = ? AND m.left_at IS NULL",
                (leader_id,),
            )
            if existing and existing["status"] != "disbanded":
                raise TeamError("You are already in a team.")
            if await self.name_taken(name):
                raise TeamError(f"A team called **{name}** already exists.")
            # Team numbers are never reused, so keep the counter in settings.
            row = await self._one("SELECT value FROM settings WHERE key = 'next_team_number'")
            number = int(row["value"]) if row else 1
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ('next_team_number', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(number + 1),),
            )
            cur = await conn.execute(
                "INSERT INTO teams (number, name, status, leader_id, created_at) "
                "VALUES (?, ?, 'forming', ?, ?)",
                (number, name, leader_id, now()),
            )
            team_id = cur.lastrowid
            await self._join(conn, team_id, leader_id, JoinResult())
        return await self.get_team(team_id)

    async def set_team_discord_ids(self, team_id: int, **ids: int | None) -> None:
        allowed = {
            "role_id",
            "staff_role_id",
            "category_id",
            "manage_channel_id",
            "applications_channel_id",
            "mass_channel_id",
            "chat_channel_id",
            "panel_message_id",
        }
        if not ids or not set(ids) <= allowed:
            raise ValueError(f"Unknown team columns: {set(ids) - allowed}")
        assignments = ", ".join(f"{col} = ?" for col in ids)
        async with self.transaction() as conn:
            await conn.execute(
                f"UPDATE teams SET {assignments} WHERE id = ?", (*ids.values(), team_id)
            )

    async def hard_delete_team(self, team_id: int) -> None:
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))

    # Members

    async def list_members(self, team_id: int) -> list[Member]:
        rows = await self._all(
            "SELECT m.user_id, m.is_helper, m.joined_at, "
            "p.albion_id, p.albion_name, p.albion_guild "
            "FROM members m LEFT JOIN players p ON p.user_id = m.user_id "
            "WHERE m.team_id = ? AND m.left_at IS NULL ORDER BY m.joined_at",
            (team_id,),
        )
        return [Member(**{**dict(r), "is_helper": bool(r["is_helper"])}) for r in rows]

    async def member_count(self, team_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM members WHERE team_id = ? AND left_at IS NULL", (team_id,)
        )
        return row["n"]

    async def is_staff(self, team_id: int, user_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM teams t LEFT JOIN members m ON m.team_id = t.id "
            "AND m.user_id = ? AND m.left_at IS NULL AND m.is_helper = 1 "
            "WHERE t.id = ? AND (t.leader_id = ? OR m.id IS NOT NULL)",
            (user_id, team_id, user_id),
        )
        return row is not None

    async def _join(self, conn, team_id: int, user_id: int, result: JoinResult) -> JoinResult:
        current = await self._one(
            "SELECT m.id, t.id AS team_id, t.status FROM members m "
            "JOIN teams t ON t.id = m.team_id WHERE m.user_id = ? AND m.left_at IS NULL",
            (user_id,),
        )
        if current:
            if current["team_id"] == team_id:
                raise TeamError("That player is already in this team.")
            if current["status"] != "disbanded":
                raise TeamError("That player is already in another team.")
            await conn.execute(
                "UPDATE members SET left_at = ? WHERE id = ?", (now(), current["id"])
            )
            result.left_disbanded_team = current["team_id"]
        await conn.execute(
            "INSERT INTO members (team_id, user_id, joined_at) VALUES (?, ?, ?)",
            (team_id, user_id, now()),
        )
        return result

    async def join_team(self, team_id: int, user_id: int) -> JoinResult:
        """Add a player to a team and clear their other pending applications and invites."""
        result = JoinResult()
        async with self.transaction() as conn:
            team = await self._one("SELECT status FROM teams WHERE id = ?", (team_id,))
            if not team or team["status"] == "disbanded":
                raise TeamError("That team no longer exists.")
            await self._join(conn, team_id, user_id, result)

            rows = await self._all(
                "SELECT id, team_id, user_id, status, channel_id, message_id FROM applications "
                "WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            )
            result.removed_applications = [Application(**dict(r)) for r in rows]
            await conn.execute(
                "DELETE FROM applications WHERE user_id = ? AND status = 'pending'", (user_id,)
            )

            rows = await self._all(
                "SELECT id, team_id, user_id, invited_by, status, channel_id, message_id "
                "FROM invites WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            )
            result.expired_invites = [Invite(**dict(r)) for r in rows]
            await conn.execute(
                "UPDATE invites SET status = 'expired' WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            )

            count = await self._one(
                "SELECT COUNT(*) AS n FROM members WHERE team_id = ? AND left_at IS NULL",
                (team_id,),
            )
            if team["status"] == "forming" and count["n"] >= MIN_MEMBERS:
                await conn.execute("UPDATE teams SET status = 'active' WHERE id = ?", (team_id,))
                result.became_active = True
        return result

    # Applications

    async def create_application(
        self, team_id: int, user_id: int
    ) -> tuple[Application, Application | None]:
        """Create a pending application, replacing the player's previous one if any.

        Returns the new application and the one it replaced.
        """
        async with self.transaction() as conn:
            row = await self._one(
                "SELECT id, team_id, user_id, status, channel_id, message_id FROM applications "
                "WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            )
            replaced = Application(**dict(row)) if row else None
            if replaced:
                await conn.execute("DELETE FROM applications WHERE id = ?", (replaced.id,))
            cur = await conn.execute(
                "INSERT INTO applications (team_id, user_id, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (team_id, user_id, now()),
            )
            app_id = cur.lastrowid
        return await self.get_application(app_id), replaced

    async def get_application(self, app_id: int) -> Application | None:
        row = await self._one(
            "SELECT id, team_id, user_id, status, channel_id, message_id FROM applications "
            "WHERE id = ?",
            (app_id,),
        )
        return Application(**dict(row)) if row else None

    async def set_application_message(self, app_id: int, channel_id: int, message_id: int) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE applications SET channel_id = ?, message_id = ? WHERE id = ?",
                (channel_id, message_id, app_id),
            )

    async def decide_application(self, app_id: int, status: str, decided_by: int) -> bool:
        """Mark a pending application accepted or denied. False if someone already did."""
        async with self.transaction() as conn:
            cur = await conn.execute(
                "UPDATE applications SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, decided_by, now(), app_id),
            )
            return cur.rowcount == 1

    async def reopen_application(self, app_id: int) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE applications SET status = 'pending', decided_by = NULL, decided_at = NULL "
                "WHERE id = ?",
                (app_id,),
            )

    # Invites

    async def create_invite(self, team_id: int, user_id: int, invited_by: int) -> Invite:
        async with self.transaction() as conn:
            dup = await self._one(
                "SELECT 1 FROM invites WHERE team_id = ? AND user_id = ? AND status = 'pending'",
                (team_id, user_id),
            )
            if dup:
                raise TeamError("That player already has a pending invite from your team.")
            cur = await conn.execute(
                "INSERT INTO invites (team_id, user_id, invited_by, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (team_id, user_id, invited_by, now()),
            )
            invite_id = cur.lastrowid
        return await self.get_invite(invite_id)

    async def get_invite(self, invite_id: int) -> Invite | None:
        row = await self._one(
            "SELECT id, team_id, user_id, invited_by, status, channel_id, message_id "
            "FROM invites WHERE id = ?",
            (invite_id,),
        )
        return Invite(**dict(row)) if row else None

    async def set_invite_message(self, invite_id: int, channel_id: int, message_id: int) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE invites SET channel_id = ?, message_id = ? WHERE id = ?",
                (channel_id, message_id, invite_id),
            )

    async def answer_invite(self, invite_id: int, status: str, *, was: str = "pending") -> bool:
        """Move an invite from one status to another. False if it was no longer `was`."""
        async with self.transaction() as conn:
            cur = await conn.execute(
                "UPDATE invites SET status = ? WHERE id = ? AND status = ?",
                (status, invite_id, was),
            )
            return cur.rowcount == 1

    # Audit log

    async def audit(
        self,
        action: str,
        *,
        actor_id: int | None = None,
        team_id: int | None = None,
        target_id: int | None = None,
        details: str | None = None,
    ) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO audit_log (at, actor_id, action, team_id, target_id, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now(), actor_id, action, team_id, target_id, details),
            )

# koth-bot

A Discord bot that runs team sign-ups for an Albion Online King of the Hill tournament.

Players create teams or apply to them from a public panel. Each team gets its own roles and
category, a leader-only management panel, an applications channel and a mass channel. Every
player links their Albion character (Europe server) through the game-info API.

## Running it

1. Create an application at <https://discord.com/developers/applications>, add a bot, and turn on
   the **Server Members Intent**.
2. Invite it with the `bot` and `applications.commands` scopes and the **Manage Roles**,
   **Manage Channels**, **Send Messages**, **Embed Links**, **Manage Messages** and
   **Mention Everyone** permissions.
3. In the server settings, drag the bot's role above every role it will hand out.
4. Copy `.env.example` to `.env` and fill it in.
5. Start it:

   ```sh
   docker compose up -d --build
   ```

   The SQLite database lives in `./data`, so it survives rebuilds and restarts.
6. In the sign-up channel, run `/setup panel`.

## Admin commands

Admin commands need the role set in `ADMIN_ROLE_ID` (or Administrator) and skip the sign-up,
roster-lock and one-team checks.

| Command | What it does |
| --- | --- |
| `/setup panel` | Posts the public panel in the current channel |
| `/signups open` / `close` | Allows or stops creating and applying to teams |
| `/rosters lock` / `unlock` | Freezes or unfreezes every team's members |
| `/team info <team>` | Shows a team's roster |
| `/team disband` / `restore` / `delete <team>` | Soft-deletes, restores or hard-deletes a team |
| `/team rename <team> <name>` | Renames the team, its roles and category |
| `/team set-leader <team> <member>` | Hands leadership to a member |
| `/team add <team> <member>` | Puts a player in a team, moving them out of their old one |
| `/team remove <member>` | Takes a player out of their team |
| `/team extend <team> <hours>` | Gives a forming team more time to reach 3 members |
| `/admin-character set <member> <name>` | Links a character for another player |
| `/export` | Downloads every team and member as CSV |
| `/resync` | Fixes team roles that drifted and redraws every panel |

Players have `/character change` to relink their own character.

Before the tournament, run through [the test-server walkthrough](docs/test-plan.md).

## Development

```sh
uv sync
uv run python -m pytest
uv run ruff check src tests
uv run tournament-bot   # reads .env from the current directory
```

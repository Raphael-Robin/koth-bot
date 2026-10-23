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

## Development

```sh
uv sync
uv run python -m pytest
uv run ruff check src tests
uv run tournament-bot   # reads .env from the current directory
```

# koth-bot

A Discord bot that runs team sign-ups for an Albion Online King of the Hill tournament.

Players create teams or apply to them from a public panel. Each team gets its own roles and
category, a leader-only management panel, an applications channel and a mass channel. Every
player links their Albion character (Europe server) through the game-info API.

## Running it

The bot runs as two separate copies at once: **prod** for the tournament server and **test**
for a test server. Each copy is its own Discord bot with its own database, so testing never
touches tournament data.

1. Create **two** applications at <https://discord.com/developers/applications> (for example
   "KotH Bot" and "KotH Bot Test"). For each one, add a bot and turn on the
   **Server Members Intent**.
2. Invite each bot to its own server only, with the `bot` and `applications.commands` scopes and
   the **Manage Roles**, **Manage Channels**, **Send Messages**, **Embed Links**,
   **Manage Messages** and **Mention Everyone** permissions. A bot leaves any server that isn't
   its own.
3. In each server's settings, drag the bot's role above every role it will hand out.
4. Copy `.env.example` to `.env.prod` and `.env.test`, and fill each in with that bot's token and
   that server's IDs.
5. Start both:

   ```sh
   docker compose up -d --build
   ```

   Or one at a time with `docker compose up -d bot-test`. The databases live in `./data/prod`
   and `./data/test`, so they survive rebuilds and restarts.
6. In each server's sign-up channel, run `/setup panel`. The test server's panel is labelled
   **[TEST SERVER]**.

## Development

```sh
uv sync
uv run python -m pytest
uv run ruff check src tests
BOT_ENV=test uv run tournament-bot   # reads .env from the current directory
```

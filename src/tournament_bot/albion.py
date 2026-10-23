"""Client for the Albion Online game-info API, used to link players to their characters."""

from dataclasses import dataclass

import aiohttp

MAX_RESULTS = 25  # Discord select menus hold at most 25 options


class AlbionAPIError(Exception):
    pass


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    guild: str | None


def parse_search(payload: dict, query: str) -> list[Character]:
    """Turn a /search response into characters, exact name matches first."""
    characters = [
        Character(id=p["Id"], name=p["Name"], guild=p.get("GuildName") or None)
        for p in payload.get("players", [])
        if p.get("Id") and p.get("Name")
    ]
    wanted = query.strip().lower()
    characters.sort(key=lambda c: (c.name.lower() != wanted, c.name.lower()))
    return characters[:MAX_RESULTS]


class AlbionClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def search(self, name: str) -> list[Character]:
        assert self._session, "AlbionClient.start() was not called"
        try:
            async with self._session.get(f"{self.base_url}/search", params={"q": name}) as resp:
                if resp.status != 200:
                    raise AlbionAPIError(f"Albion API returned HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AlbionAPIError(str(exc)) from exc
        return parse_search(payload or {}, name)

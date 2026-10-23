import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    admin_role_id: int
    log_channel_id: int
    db_path: str
    albion_api_base: str
    env_name: str

    @property
    def is_test(self) -> bool:
        return self.env_name != "prod"

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"Missing required environment variable {name}")
            return value

        return cls(
            token=required("DISCORD_TOKEN"),
            guild_id=int(required("GUILD_ID")),
            admin_role_id=int(required("ADMIN_ROLE_ID")),
            log_channel_id=int(required("LOG_CHANNEL_ID")),
            db_path=os.getenv("DB_PATH", "data/bot.db"),
            albion_api_base=os.getenv(
                "ALBION_API_BASE", "https://gameinfo-ams.albiononline.com/api/gameinfo"
            ),
            env_name=os.getenv("BOT_ENV", "prod").lower(),
        )

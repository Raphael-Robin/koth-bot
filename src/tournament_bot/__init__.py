import logging


def main() -> None:
    from .bot import TournamentBot
    from .config import Config

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = Config.from_env()
    TournamentBot(config).run(config.token, log_handler=None)

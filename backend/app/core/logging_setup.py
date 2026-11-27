"""One logging format for every process (API, workers, MCP servers), at the level LOG_LEVEL sets (app/core/config.py)."""
import logging

from app.core.config import get_settings

FORMAT = "%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
DATEFMT = "%H:%M:%S"


def configure_logging() -> None:
    """Configure the root logger once per process. Safe to call more than once."""
    level = getattr(logging, get_settings().log_level, logging.INFO)
    logging.basicConfig(level=level, format=FORMAT, datefmt=DATEFMT)
    logging.getLogger().setLevel(level)

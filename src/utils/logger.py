import logging
import sys
from logging.handlers import RotatingFileHandler, SysLogHandler

from src.config import settings

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class LevelFilter(logging.Filter):
    def __init__(self, min_level, max_level):
        super().__init__()
        self.min_level = min_level
        self.max_level = max_level

    def filter(self, record):
        return self.min_level <= record.levelno <= self.max_level


def setup_logger():
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger()

    # Clear existing handlers to prevent duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.setLevel(LEVELS.get(settings.LOG_LEVEL, logging.INFO))

    # These emit request and response bodies at DEBUG, including credentials.
    for noisy in ("litellm", "LiteLLM", "httpx", "httpcore", "urllib3", "sqlalchemy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log_driver = settings.LOG_DRIVER

    if log_driver == "file":
        handler = RotatingFileHandler(
            settings.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif log_driver == "syslog":
        handler = SysLogHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif log_driver == "stderr":
        # Everything to stderr, for a process whose stdout is a protocol.
        # A stdio MCP server has a client parsing every line of stdout as
        # JSON-RPC, so one log line there loses the message it interrupted.
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif log_driver == "console":
        # Handler for stdout (INFO and DEBUG)
        stdout_handler = logging.StreamHandler(stream=sys.stdout)
        stdout_handler.setFormatter(formatter)
        stdout_handler.addFilter(LevelFilter(logging.DEBUG, logging.INFO))
        logger.addHandler(stdout_handler)

        # Handler for stderr (WARNING and above)
        stderr_handler = logging.StreamHandler(stream=sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.addFilter(LevelFilter(logging.WARNING, logging.CRITICAL))
        logger.addHandler(stderr_handler)
    else:
        raise ValueError(
            f"Invalid LOG_DRIVER: {log_driver}. "
            "Must be one of ['console', 'stderr', 'file', 'syslog']"
        )


# Define the logger at the module level so it can be imported
logger = logging.getLogger()

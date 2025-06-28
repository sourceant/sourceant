import logging
import sys
from logging.handlers import RotatingFileHandler, SysLogHandler

from src.config import settings


class LevelFilter(logging.Filter):
    def __init__(self, min_level, max_level):
        super().__init__()
        self.min_level = min_level
        self.max_level = max_level

    def filter(self, record):
        return self.min_level <= record.levelno <= self.max_level


formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    log_driver = settings.LOG_DRIVER
    if log_driver == "file":
        handler = RotatingFileHandler(
            "sourceant.log", maxBytes=10 * 1024 * 1024, backupCount=5
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif log_driver == "syslog":
        handler = SysLogHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif log_driver == "console":
        # Handler for stdout (INFO and below)
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
            f"Invalid LOG_DRIVER: {log_driver}. Must be one of ['console', 'file', 'syslog']"
        )

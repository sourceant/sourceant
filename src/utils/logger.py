import logging
from logging.handlers import RotatingFileHandler

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

handler = RotatingFileHandler("sourceant.log", maxBytes=10 * 1024 * 1024, backupCount=5)
handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

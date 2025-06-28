import redis
from redislite import Redis as RedisLite

from src.config.settings import QUEUE_MODE, REDIS_HOST, REDIS_PORT, REDISLITE_DB_PATH
from src.utils.logger import logger, setup_logger

# Set up logging for the worker
setup_logger()

# Configure the connection based on the queue mode
if QUEUE_MODE == "redis":
    logger.info("Worker using Redis for event queue.")
    REDIS_CONNECTION = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
elif QUEUE_MODE == "redislite":
    logger.info("Worker using RedisLite for event queue.")
    # This will connect to the same file-based Redis instance as the main app
    REDIS_CONNECTION = RedisLite(REDISLITE_DB_PATH)
else:
    raise ValueError(f"Invalid QUEUE_MODE for worker: {QUEUE_MODE}")

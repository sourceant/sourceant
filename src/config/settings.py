import os
from dotenv import load_dotenv

load_dotenv()

STATELESS_MODE = os.getenv("STATELESS_MODE", "false").lower() == "true"
QUEUE_MODE = os.getenv("QUEUE_MODE", "redis")
VALID_QUEUE_MODES = ["redis", "request", "redislite"]
if QUEUE_MODE not in VALID_QUEUE_MODES:
    raise ValueError(
        f"Invalid QUEUE_MODE: {QUEUE_MODE}. Must be one of {VALID_QUEUE_MODES}"
    )

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
LLM = os.getenv("LLM", "gemini")
LOG_DRIVER: str = os.getenv("LOG_DRIVER", "console")
LOG_FILE: str = os.getenv("LOG_FILE", "sourceant.log")
GITHUB_SECRET = os.getenv("GITHUB_SECRET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if GITHUB_SECRET is None:
    raise ValueError("GITHUB_SECRET environment variable is not set.")

import os
from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "production").lower()
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
STATELESS_MODE = os.getenv("STATELESS_MODE", "false").lower() == "true"
QUEUE_MODE = os.getenv("QUEUE_MODE", "redis")
VALID_QUEUE_MODES = ["redis", "request", "redislite"]
if QUEUE_MODE not in VALID_QUEUE_MODES:
    raise ValueError(
        f"Invalid QUEUE_MODE: {QUEUE_MODE}. Must be one of {VALID_QUEUE_MODES}"
    )

DATABASE_URL = os.getenv("DATABASE_URL")
if not STATELESS_MODE and DATABASE_URL is None:
    raise ValueError(
        "DATABASE_URL environment variable must be set when not in STATELESS_MODE."
    )

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
# 128K tokens - a conservative default compatible with most LLM providers
DEFAULT_TOKEN_LIMIT = 131072

LLM_MODEL = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")
LLM_TOKEN_LIMIT = int(os.getenv("LLM_TOKEN_LIMIT", DEFAULT_TOKEN_LIMIT))
LOG_DRIVER: str = os.getenv("LOG_DRIVER", "console")
LOG_FILE: str = os.getenv("LOG_FILE", "sourceant.log")
GITHUB_SECRET = os.getenv("GITHUB_SECRET")
REVIEW_DRAFT_PRS = os.getenv("REVIEW_DRAFT_PRS", "false").lower() == "true"
LLM_UPLOADS_ENABLED = os.getenv("LLM_UPLOADS_ENABLED", "true").lower() == "true"
# VADER compound score: -1.0 (negative) to +1.0 (positive).
# 0.3 is above VADER's default positive cutoff (0.05) to avoid
# filtering mixed comments that contain actionable feedback.
POSITIVE_SENTIMENT_THRESHOLD = float(os.getenv("POSITIVE_SENTIMENT_THRESHOLD", "0.3"))
REVIEW_MISSING_EXISTING_CODE_POLICY = os.getenv(
    "REVIEW_MISSING_EXISTING_CODE_POLICY", "drop"
).lower()

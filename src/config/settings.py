import os
from dotenv import load_dotenv

load_dotenv()

STATELESS_MODE = os.getenv("STATELESS_MODE", "false").lower() == "true"
QUEUE_MODE = os.getenv("QUEUE_MODE", "redis")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
LLM = os.getenv("LLM", "gemini")
GITHUB_SECRET = os.getenv("GITHUB_SECRET")

if GITHUB_SECRET is None:
    raise ValueError("GITHUB_SECRET environment variable is not set.")

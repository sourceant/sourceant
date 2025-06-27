import os

STATELESS_MODE = os.getenv("STATELESS_MODE", "false").lower() == "true"

from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SA_API_KEY")
API_KEY_NAME = "X-SourceAnt-API-KEY"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)


async def get_api_key(api_key_header: str = Security(api_key_header)):
    if not API_KEY:
        # This case is for when the server itself is not configured with an API key.
        # It's a server-side error, not a client error.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API Key not configured on server.",
        )

    if api_key_header == API_KEY:
        return api_key_header
    else:
        # This case is for when the client provides an invalid key.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )

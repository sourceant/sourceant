from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth import get_current_user
from src.config.db import get_engine
from src.core.mcp.revocations import revoke

router = APIRouter()


class RevocationInput(BaseModel):
    token_id: str = Field(min_length=1, max_length=64)
    expires_at: datetime


@router.post("/revocations", status_code=204)
def revoke_token(body: RevocationInput, user: dict = Depends(get_current_user)):
    """Withdraw an MCP token before it expires. Only the gateway, which issued it, asks."""
    if user.get("scope", {}).get("mcp_revocation") is not True:
        raise HTTPException(403, "Only the issuer can withdraw a token")
    engine = get_engine()
    if engine is None:
        raise HTTPException(503, "No database to record the withdrawal in")
    revoke(engine, body.token_id, body.expires_at)

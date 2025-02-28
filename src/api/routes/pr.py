from fastapi import APIRouter, HTTPException, Request, Header, Depends, Body
from src.controllers.repository_event_controller import RepositoryEventController
from typing import Optional
from pydantic import BaseModel, ConfigDict
import os
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

GITHUB_SECRET = os.getenv("GITHUB_SECRET")

if GITHUB_SECRET is None:
    raise ValueError("GITHUB_SECRET environment variable is not set.")


class GitHubWebhookPayload(BaseModel):
    action: Optional[str] = None
    pull_request: Optional[dict] = None
    issue: Optional[dict] = None
    repository: dict
    sender: dict

    model_config = ConfigDict(extra="allow")

    class ConfigDict:
        json_schema_extra = {
            "example": {
                "action": "opened",
                "pull_request": {
                    "url": "https://api.github.com/sourceant/sourceant/repo/pulls/1",
                    "title": "Example PR Title",
                    "number": 1,
                },
                "repository": {"full_name": "sourceant/soureant"},
                "sender": {"login": "nfebe"},
            }
        }


def verify_signature(payload: str, signature: str, secret: str) -> bool:
    if signature is None:
        return True
    hash_payload = hmac.new(secret.encode(), payload.encode(), hashlib.sha256)
    expected_signature = f"sha256={hash_payload.hexdigest()}"
    return hmac.compare_digest(expected_signature, signature)


def get_event(event: str = Header(None, alias="X-GitHub-Event")):
    return event


def get_provider_from_headers(headers: dict) -> Optional[str]:
    """
    Determines the provider based on the request headers (case-insensitive).
    """
    for header_name in headers:
        if header_name.lower().startswith("x-github-"):
            return "GitHub"
    return None


@router.post("/github-webhook")
async def github_webhook(
    request: Request,
    signature: str = Header(None, alias="X-Hub-Signature-256"),
    event: str = Depends(get_event),
    payload: GitHubWebhookPayload = Body(...),
):
    payload_data = await request.body()
    if not verify_signature(payload_data.decode(), signature, GITHUB_SECRET):
        raise HTTPException(status_code=400, detail="Invalid GitHub signature")

    # Handle both pull request and issue events
    url = (
        payload.pull_request["url"]
        if payload.pull_request
        else payload.issue["url"] if payload.issue else None
    )
    title = (
        payload.pull_request["title"]
        if payload.pull_request
        else payload.issue["title"] if payload.issue else None
    )

    number = payload.pull_request["number"] if payload.pull_request else None

    provider = get_provider_from_headers(request.headers)

    return RepositoryEventController.create(
        action=payload.action,
        type=event,
        url=url,
        title=title,
        repository_full_name=payload.repository["full_name"],
        number=number,
        payload=payload.model_dump(),
        provider=provider,
    )

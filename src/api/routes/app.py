from fastapi import APIRouter, Depends
from typing import List
from src.api.routes.health import identity
from src.auth import get_current_user
from src.models.repository_event import RepositoryEvent
from src.controllers.repository_event_controller import RepositoryEventController

router = APIRouter()


@router.get("/")
async def index():
    return {
        **identity(),
        "documentation": "https://sourceant.ai/docs",
        "openapi": "/docs",
        "health": "/health",
    }


@router.get("/repository-events", response_model=List[RepositoryEvent])
async def get_repository_events(user: dict = Depends(get_current_user)):
    return RepositoryEventController.index()

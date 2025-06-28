from fastapi import APIRouter, Depends
from typing import List
from src.models.repository_event import RepositoryEvent
from src.controllers.repository_event_controller import RepositoryEventController
from src.api.security import get_api_key

router = APIRouter()


@router.get("/")
async def welcome():
    return {"message": "The 🐜 SourceAnt 🐜  API is live!"}


@router.get(
    "/repository-events",
    response_model=List[RepositoryEvent],
    dependencies=[Depends(get_api_key)],
)
async def get_repository_events():
    return RepositoryEventController.index()

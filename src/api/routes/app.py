from fastapi import APIRouter
from typing import List
from src.models.repository_event import RepositoryEvent
from src.controllers.repository_event_controller import RepositoryEventController

router = APIRouter()


@router.get("/")
async def welcome():
    return {"message": "The 🐜 SourceAnt 🐜  API is live!"}


@router.get("/repository-events", response_model=List[RepositoryEvent])
async def get_repository_events():
    return RepositoryEventController.index()

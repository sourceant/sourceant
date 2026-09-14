from fastapi import APIRouter, Depends, Query

from src.auth import gateway_routing
from src.core.responses import success_response
from src.core.workspace import workspaces_holding

router = APIRouter(dependencies=[Depends(gateway_routing)])


@router.get("/repository")
async def workspaces_for_repository(full_name: str = Query(..., min_length=1)):
    """Which workspaces have connected this repository.

    One exact name at a time, answered with workspace references and nothing
    else, so a caller learns nothing about a repository whose name they do not
    already have.

    More than one is left for the caller to settle. Which of them a delivery
    belongs to is decided by what the provider says, and that is not known here.
    """
    return success_response(data={"workspaces": workspaces_holding(full_name)})

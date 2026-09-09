from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from src.api.routes.topology import get_scope
from src.config.db import get_engine
from src.core.responses import success_response
from src.core.scope import Scope
from src.core.services import service_registry
from src.core.usage.reading import SQLUsageReader
from src.core.usage.models import UsageQuery
from src.core.usage.interfaces import UsageReader

router = APIRouter()


def get_usage_reader():
    try:
        return service_registry.resolve(UsageReader)
    except LookupError:
        engine = get_engine()
        if engine is None:
            raise HTTPException(503, "Usage store is unavailable")
        return SQLUsageReader(engine)


@router.get("")
def usage(
    period: Literal["7d", "30d", "90d"] = "30d",
    repository: str = "",
    organization: str = "",
    scope: Scope = Depends(get_scope),
    reader=Depends(get_usage_reader),
):
    since = datetime.now(timezone.utc) - timedelta(days=int(period[:-1]))
    report = reader.read(
        UsageQuery("workspace", scope.get("workspace"), since, repository, organization)
    )
    return success_response({"since": period, **report})

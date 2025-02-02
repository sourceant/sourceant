from fastapi import FastAPI
from src.api.routes import pr as pr_endpoints
from src.api.routes import app as app_endpoints

app = FastAPI(
    title="🐜 SourceAnt 🐜",
    description="Automated code review tool",
    version="1.0.0",
)

app.include_router(app_endpoints.router, tags=["general"])
app.include_router(pr_endpoints.router, prefix="/api/prs", tags=["pull_requests"])

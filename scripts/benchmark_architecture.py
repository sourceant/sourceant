import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes import code
from src.core.code_index import SQLCodeIndexRepository


def main():
    parser = argparse.ArgumentParser(
        description="Measure architecture reads against an indexed checkout"
    )
    parser.add_argument("repository", type=Path)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error("runs must be between 1 and 100")
    if not code.LOCAL_MODE:
        parser.error("SOURCEANT_LOCAL=true is required")
    with (
        tempfile.TemporaryDirectory() as folder,
        patch.dict(os.environ, {"SOURCEANT_HOME": folder}),
        patch.dict(app.dependency_overrides),
    ):
        index = SQLCodeIndexRepository(
            create_engine(f"sqlite:///{folder}/index.db"), create_schema=True
        )
        app.dependency_overrides[code.get_code_index] = lambda: index
        client = TestClient(app)
        response = client.post(
            "/api/code/repositories",
            json={
                "path": str(args.repository.resolve()),
                "name": "benchmark/repository",
            },
        )
        response.raise_for_status()
        start = perf_counter()
        response = client.post(
            "/api/code/index", json={"repository": "benchmark/repository"}
        )
        response.raise_for_status()
        indexing = perf_counter() - start
        durations = []
        for _ in range(args.runs):
            start = perf_counter()
            response = client.get(
                "/api/code/architecture",
                params={"repository": "benchmark/repository", "depth": 2},
            )
            response.raise_for_status()
            durations.append((perf_counter() - start) * 1000)
        data = response.json()["data"]
        print(
            json.dumps(
                {
                    "index_seconds": round(indexing, 3),
                    "runs": len(durations),
                    "median_ms": round(statistics.median(durations), 2),
                    "max_ms": round(max(durations), 2),
                    "response_bytes": len(response.content),
                    "components": len(data["components"]),
                    "relationships": len(data["relationships"]),
                    "coverage": data["coverage"],
                },
                indent=2,
            )
        )
        app.dependency_overrides.pop(code.get_code_index, None)


if __name__ == "__main__":
    main()

import pytest
import subprocess
from fastapi.testclient import TestClient
from src.api.main import app
import os

_migrated = False


class BaseTestCase:

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Setup environment variables and start the test server."""
        os.environ["APP_ENV"] = "test"
        self.client = TestClient(app)
        self.run_migrations()
        yield

    def run_migrations(self):
        """Run the database migrations for the test database, once."""
        global _migrated
        if _migrated:
            return

        command = ["./sourceant", "db", "upgrade", "head"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            print(f"Error running migrations: {result.stderr.decode()}")
            raise Exception("Migration failed")
        _migrated = True

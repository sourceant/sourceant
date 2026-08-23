import pytest
import subprocess
from fastapi.testclient import TestClient
from src.api.main import app
import os


class BaseTestCase:

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Setup environment variables and start the test server."""
        try:
            os.environ["APP_ENV"] = "test"
            os.environ["DATABASE_URL"] = "sqlite:///./sourceant.db"

            self.client = TestClient(app)
            self.run_migrations()
            yield
        finally:
            pass

    def run_migrations(self):
        """Run the database migrations for the test database."""
        command = ["./sourceant", "db", "upgrade", "head"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            print(f"Error running migrations: {result.stderr.decode()}")
            raise Exception("Migration failed")
        else:
            print(f"Migrations ran successfully: {result.stdout.decode()}")

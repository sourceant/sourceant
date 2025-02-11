import pytest
import subprocess
from fastapi.testclient import TestClient
from multiprocessing import Process
from pathlib import Path
from src.api.main import app


class BaseTestCase:
    client = None
    client_base_url = "http://localhost:8001"

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Setup environment variables and start the test server."""
        env_file = Path(".env")
        backup_env = env_file.read_text() if env_file.exists() else None

        # Overwriting the .env file with these test environment variables only works locally
        # On github actions, we need to set these environment variables in the workflow file (where we use a postgres database container)
        # So do not be stunned if github action logs show a DATABASE_URL with a postgres connection string instead of what you see here
        try:
            env_file.write_text(
                "APP_ENV=test\nDATABASE_URL=sqlite:///./test.db\nGITHUB_SECRET=SECRET\n"
            )

            self.start_app_on_test_port()
            self.client = TestClient(app)
            self.client.base_url = self.client_base_url
            self.run_migrations()
            yield
        finally:
            if backup_env is not None:
                env_file.write_text(backup_env)
            else:
                env_file.unlink(missing_ok=True)

            self.stop_app()

    def start_app_on_test_port(self):
        """Starts the FastAPI app on a custom port in a separate process."""

        def run_uvicorn():
            subprocess.run(
                [
                    "uvicorn",
                    "src.api.main:app",
                    "--reload",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8001",
                ]
            )

        self.process = Process(target=run_uvicorn)
        self.process.start()

    def stop_app(self):
        """Stops the app after the test session ends."""
        if hasattr(self, "process") and self.process.is_alive():
            self.process.terminate()
            self.process.join()

    def run_migrations(self):
        """Run the database migrations for the test database."""
        command = ["./sourceant", "db", "upgrade", "head"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            print(f"Error running migrations: {result.stderr.decode()}")
            raise Exception("Migration failed")
        else:
            print(f"Migrations ran successfully: {result.stdout.decode()}")

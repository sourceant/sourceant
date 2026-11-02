from src.core.memory import EventRecorder, InMemoryMemory
from src.core.services import service_registry
from src.tests.base_test import BaseTestCase


class TestKnowledgeMemory(BaseTestCase):
    def test_http_knowledge_writes_and_removal_record_events(
        self, monkeypatch, tmp_path
    ):
        from src.core.knowledge import InMemoryKnowledgeRepository, KnowledgeRepository
        from src.cli.local_index import add_repository

        monkeypatch.setattr(service_registry, "_registrations", {})
        memory = InMemoryMemory()
        service_registry.register(EventRecorder, memory, "test")
        service_registry.register(
            KnowledgeRepository, InMemoryKnowledgeRepository(), "test"
        )
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path))
        repository = tmp_path / "checkout"
        repository.mkdir()
        add_repository(str(repository), name="acme/shop")
        body = {
            "repository": "acme/shop",
            "id": "retry",
            "kind": "decision",
            "status": "accepted",
            "summary": "Retry three times",
        }
        assert self.client.put("/api/knowledge", json=body).status_code == 200
        body["summary"] = "Retry twice"
        assert self.client.put("/api/knowledge", json=body).status_code == 200
        body["status"] = "retired"
        assert self.client.put("/api/knowledge", json=body).status_code == 200
        assert (
            self.client.delete(
                "/api/knowledge", params={"repository": "acme/shop", "id": "retry"}
            ).status_code
            == 200
        )
        events = list(memory.events.values())
        assert [event["type"] for event in events] == [
            "knowledge.captured",
            "knowledge.updated",
            "knowledge.status_changed",
            "knowledge.removed",
        ]
        assert events[-1]["attributes"]["knowledge.summary_before"] == "Retry twice"
        assert events[-1]["attributes"]["knowledge.status_after"] is None
        assert memory.entities[events[0]["participants"][0]["actor"]]["name"] == "local"

    def test_requirements_record_the_authenticated_actor(self, monkeypatch, tmp_path):
        import time
        import jwt
        from sqlalchemy import create_engine
        from src.api.main import app
        from src.api.routes import requirements
        from src.core.knowledge import InMemoryKnowledgeRepository
        from src.core.requirements import (
            KnowledgeBackedRequirements,
            SQLRequirementsRepository,
        )

        monkeypatch.setattr(service_registry, "_registrations", {})
        memory = InMemoryMemory()
        service_registry.register(EventRecorder, memory, "test")
        monkeypatch.setenv("JWT_SECRET", "memory-requirements-test-signing-secret")
        token = jwt.encode(
            {
                "sub": "engineer",
                "scope": {"workspace_id": "workspace"},
                "exp": int(time.time()) + 60,
            },
            "memory-requirements-test-signing-secret",
            algorithm="HS256",
        )
        engine = create_engine(f"sqlite:///{tmp_path / 'requirements.db'}")
        store = KnowledgeBackedRequirements(
            SQLRequirementsRepository(engine, create_schema=True),
            InMemoryKnowledgeRepository(),
        )
        app.dependency_overrides[requirements.get_requirements] = lambda: store
        try:
            headers = {"Authorization": f"Bearer {token}"}
            response = self.client.put(
                "/api/requirements",
                headers=headers,
                json={
                    "id": "retry",
                    "type": "functional",
                    "status": "accepted",
                    "summary": "Stop after three retries",
                },
            )
            assert response.status_code == 200, response.text
            response = self.client.delete("/api/requirements/retry", headers=headers)
            assert response.status_code == 200, response.text
        finally:
            app.dependency_overrides.pop(requirements.get_requirements, None)
            engine.dispose()
        events = list(memory.events.values())
        assert [event["type"] for event in events] == [
            "knowledge.captured",
            "knowledge.removed",
        ]
        for event in events:
            assert (
                memory.entities[event["participants"][0]["actor"]]["name"] == "engineer"
            )

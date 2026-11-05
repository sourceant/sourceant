from src.core.memory import Memory, InMemoryMemory
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
        service_registry.register(Memory, memory, "test")
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
        response = self.client.put("/api/knowledge", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["type"] == "decision"
        assert response.json()["data"]["kind"] == "decision"
        response = self.client.get("/api/knowledge", params={"repository": "acme/shop"})
        assert response.status_code == 200, response.text
        item = response.json()["data"]["items"][0]
        assert item["type"] == item["kind"] == "decision"
        body["type"] = "constraint"
        response = self.client.put("/api/knowledge", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["type"] == "constraint"
        assert response.json()["data"]["kind"] == "constraint"
        body.pop("kind")

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
        events = list(memory._events.values())
        assert [event["type"] for event in events] == [
            "knowledge.captured",
            "knowledge.updated",
            "knowledge.updated",
            "knowledge.status_changed",
            "knowledge.removed",
        ]
        assert events[-1]["attributes"]["knowledge.summary_before"] == "Retry twice"
        assert events[-1]["attributes"]["knowledge.status_after"] is None
        assert memory.entities[events[0]["participants"][0]["actor"]]["name"] == "local"

    def test_repeated_http_write_records_after_engine_failure(
        self, monkeypatch, tmp_path
    ):
        from src.core.knowledge import KnowledgeRepository, InMemoryKnowledgeRepository
        from src.cli.local_index import add_repository

        monkeypatch.setattr(service_registry, "_registrations", {})
        memory = InMemoryMemory()
        service_registry.register(Memory, memory, "test")
        service_registry.register(
            KnowledgeRepository, InMemoryKnowledgeRepository(), "test"
        )
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path))
        repository = tmp_path / "checkout"
        repository.mkdir()
        add_repository(str(repository), name="acme/shop")
        write = memory._write_records
        attempts = []

        def fail_once(events, entities):
            attempts.append(events)
            if len(attempts) == 1:
                raise ConnectionError("Engine unavailable")
            write(events, entities)

        monkeypatch.setattr(memory, "_write_records", fail_once)
        body = {
            "repository": "acme/shop",
            "id": "retry",
            "type": "decision",
            "status": "accepted",
            "summary": "Retry three times",
        }
        response = self.client.put("/api/knowledge", json=body)
        assert response.status_code == 200, response.text
        assert memory._events == {}
        response = self.client.put("/api/knowledge", json=body)
        assert response.status_code == 200, response.text
        assert len(attempts) == 2
        (event,) = memory._events.values()
        assert event["type"] == "knowledge.updated"
        assert event["content"][0]["text"] == body["summary"]

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
        service_registry.register(Memory, memory, "test")
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
        events = list(memory._events.values())
        assert [event["type"] for event in events] == [
            "knowledge.captured",
            "knowledge.removed",
        ]
        for event in events:
            assert (
                memory.entities[event["participants"][0]["actor"]]["name"] == "engineer"
            )

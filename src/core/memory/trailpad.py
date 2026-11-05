from __future__ import annotations

from .engine import Entity, EventRecord, _ScopeMemory, binding
from threading import RLock
from urllib.parse import quote
import httpx
from src.core.scope import Scope
from .bindings import SourceBindings
from .models import Source


class _EngineRecords:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        person: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"Authorization": f"Bearer {key}"}
        if person:
            headers["Memory-Actor"] = person
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/",
            headers=headers,
            transport=transport,
            timeout=5,
        )
        self._buckets: dict[tuple[str, str, str], str] = {}
        self._sources_by_bucket = {}
        self._source_bindings = SourceBindings()
        self._lock = RLock()

    def _request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def bucket(self, scope: Scope) -> str:
        return self.bind(*binding(scope))

    def buckets(self, scope: Scope) -> list[str]:
        return repository_buckets(scope, self)

    def bind(self, system: str, installation: str, container: str) -> str:
        key = (system, installation, container)
        with self._lock:
            if key not in self._buckets:
                response = self._request(
                    "PUT",
                    "v1/buckets",
                    json={
                        "name": container,
                        "binding": dict(
                            zip(("system", "installation", "container"), key)
                        ),
                    },
                )
                self._buckets[key] = response["id"]
            return self._buckets[key]

    def _write_records(self, events: list[EventRecord], entities: list[Entity]) -> None:
        self._request(
            "POST",
            "v1/records",
            json={
                "events": [event.to_dict() for event in events],
                "entities": [entity.to_dict() for entity in entities],
            },
        )

    def list(
        self, scope: Scope, since=None, until=None, after=None, *, buckets=None
    ) -> dict:
        params = {
            "bucket": buckets if buckets is not None else self.buckets(scope),
            "from": since,
            "to": until,
            "after": after,
        }
        return self._request(
            "GET",
            "v1/events",
            params={key: value for key, value in params.items() if value is not None},
        )

    def _recall(self, query: dict) -> dict:
        return self._request("POST", "v1/recall", json=query)

    def event(self, id: str) -> EventRecord:
        return EventRecord.from_dict(
            self._request("GET", "v1/events/" + quote(id, safe=""))
        )

    def entity(self, id: str) -> Entity:
        return Entity.from_dict(
            self._request("GET", "v1/entities/" + quote(id, safe=""))
        )

    def use(self, ids: list[str]) -> None:
        self._request("POST", "v1/events/use", json={"events": ids})

    def close(self) -> None:
        self.client.close()


class TrailpadMemory(_ScopeMemory, _EngineRecords):
    pass


def repository_buckets(scope: Scope, binder) -> list[str]:
    buckets = [binder.bucket(scope)]
    binder._sources_by_bucket[buckets[0]] = Source.for_scope(scope)
    for source in binder._source_bindings.sources(scope):
        bucket = binder.bind(source.system, source.installation, source.container)
        binder._sources_by_bucket[bucket] = source
        if bucket not in buckets:
            buckets.append(bucket)
    return buckets

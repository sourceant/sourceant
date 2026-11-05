from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import RLock

from sqlalchemy import Column, MetaData, String, Table, insert, select
from sqlalchemy.exc import IntegrityError

from src.core.scope import Scope
from .models import Source

metadata = MetaData()
source_bindings = Table(
    "memory_source_bindings",
    metadata,
    *(
        Column(name, String(255), primary_key=True)
        for name in ("workspace", "repository", "system", "installation", "container")
    ),
)

SourceResolver = Callable[[Scope], Iterable[Source]]
_resolvers: list[SourceResolver] = []
_resolver_lock = RLock()


def register_source_resolver(resolver: SourceResolver) -> None:
    with _resolver_lock:
        if resolver not in _resolvers:
            _resolvers.append(resolver)


class SourceBindings:
    def __init__(self, *, persist: bool = True):
        self.persist = persist
        self._sources: dict[tuple[str, str], dict[tuple[str, str, str], Source]] = {}
        self._lock = RLock()

    def _engine(self):
        if not self.persist:
            return None
        from src.config.db import get_engine

        return get_engine()

    def remember(self, scope: Scope, sources: list[Source]) -> None:
        workspace, repository = scope.get("workspace", ""), scope.get("repository", "")
        unique = {
            (source.system, source.installation, source.container): source
            for source in sources
        }
        engine = self._engine()
        if engine is None:
            with self._lock:
                self._sources.setdefault((workspace, repository), {}).update(unique)
            return
        with engine.begin() as connection:
            for system, installation, container in unique:
                values = dict(
                    workspace=workspace,
                    repository=repository,
                    system=system,
                    installation=installation,
                    container=container,
                )
                try:
                    with connection.begin_nested():
                        connection.execute(insert(source_bindings).values(**values))
                except IntegrityError:
                    existing = select(source_bindings).filter_by(**values)
                    if connection.execute(existing).first() is None:
                        raise

    def sources(self, scope: Scope) -> list[Source]:
        workspace, repository = scope.get("workspace", ""), scope.get("repository", "")
        engine = self._engine()
        if engine is None:
            with self._lock:
                sources = [
                    source
                    for (
                        saved_workspace,
                        saved_repository,
                    ), items in self._sources.items()
                    if saved_repository == repository
                    and (not workspace or saved_workspace == workspace)
                    for source in items.values()
                ]
        else:
            query = select(source_bindings).where(
                source_bindings.c.repository == repository
            )
            if workspace:
                query = query.where(source_bindings.c.workspace == workspace)
            with engine.connect() as connection:
                sources = [
                    Source(
                        system=row.system,
                        installation=row.installation,
                        container=row.container,
                    )
                    for row in connection.execute(query)
                ]
        with _resolver_lock:
            resolvers = tuple(_resolvers)
        for resolver in resolvers:
            sources.extend(resolver(scope))
        application = Source.for_scope(scope)
        return [
            source
            for source in sources
            if source.system != application.system
            or (source.installation, source.container)
            == (application.installation, application.container)
        ]

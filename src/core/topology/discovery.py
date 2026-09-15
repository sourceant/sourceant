"""Connection discovery: asked for in a request, done in a worker.

Reading a repository's code for the joints a manifest cannot declare asks a
model several times over, and a system holds many repositories. Done in the
request that asked for it, a whole discovery is bounded by how long one HTTP
request may last, and the system large enough to be worth mapping is exactly
the one that runs out of it. Worse, the failure looks like an answer: a
discovery that died part way through comes back as nothing found.

So a discovery is a batch. One job reads what every repository declares, which
has to see them together because a declared name is only a dependency once
another repository is found publishing it. One job per repository does the
reading that has no such need. Each records what it found as it finishes, so a
discovery interrupted anywhere keeps everything it has already paid for, and
asking again only does what is still outstanding.

Nothing here carries the asking user's credentials. What may be read is settled
while the user is present, and the worker mints the installation token for the
one repository its job names.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Mapping, Optional, Sequence

from src.config.settings import whole_number
from src.core.jobs.models import BACKGROUND, BY_WORKSPACE, Job, JobOutcome, JobRequest
from src.core.scope import Scope
from src.core.services import ServiceRegistry, service_registry
from src.core.topology.reading import head_revision
from src.utils.logger import logger

MANIFESTS = "topology.manifests"
READING = "topology.reading"

#: What a discovery is called, so a batch reads as one in the job feed.
NAME = "connection discovery"

#: How long one repository's reading may take. Four rounds with a model, each
#: carrying the files the round before it asked for, outlasts the queue's own
#: default of 300 seconds on a repository of any size.
READING_TIMEOUT = whole_number("TOPOLOGY_READING_TIMEOUT", 900)

MANIFESTS_TIMEOUT = whole_number("TOPOLOGY_MANIFESTS_TIMEOUT", 300)

#: How many repositories one discovery reads with a model. Each costs up to
#: MAX_ROUNDS model calls, so this is the ceiling on what one asking spends.
MOST_READ = whole_number("TOPOLOGY_DISCOVERY_READS", 25)

#: How many entities a single reading may be offered to join to.
MOST_TARGETS = 50


def _request(
    kind: str,
    payload: Mapping[str, Any],
    *,
    workspace: str,
    seconds: int,
    batch_id: Optional[int] = None,
) -> JobRequest:
    return JobRequest(
        lane=BACKGROUND,
        kind=kind,
        payload=dict(payload),
        deadline_seconds=seconds,
        # A second attempt pays for the whole reading again, so a job that
        # fails is reported rather than repeated.
        max_attempts=1,
        batch_id=batch_id,
    ).for_(workspace, BY_WORKSPACE)


def manifests_job(
    assets: Sequence[Mapping[str, str]],
    *,
    workspace: str,
    system_id: str,
    persist: bool = True,
    batch_id: Optional[int] = None,
) -> JobRequest:
    """The pass that reads what every repository declares, all at once."""
    return _request(
        MANIFESTS,
        {
            "assets": [
                {"entity_id": one["entity_id"], "repository": one["repository"]}
                for one in assets
            ],
            "workspace": workspace,
            "system_id": system_id,
            "persist": persist,
        },
        workspace=workspace,
        seconds=MANIFESTS_TIMEOUT,
        batch_id=batch_id,
    )


def reading_job(
    asset: Mapping[str, str],
    *,
    workspace: str,
    system_id: str,
    targets: Sequence[str],
    about: str,
    revision: str = "",
    persist: bool = True,
    batch_id: Optional[int] = None,
) -> JobRequest:
    """The pass that reads one repository's code for what it declares nowhere.

    `targets` and `about` are settled here rather than in the worker. What a
    reading may name is a question about what the asking user can see, and the
    user is here.
    """
    return _request(
        READING,
        {
            "entity_id": asset["entity_id"],
            "repository": asset["repository"],
            "targets": list(targets)[:MOST_TARGETS],
            "about": about,
            "revision": revision,
            "workspace": workspace,
            "system_id": system_id,
            "persist": persist,
        },
        workspace=workspace,
        seconds=READING_TIMEOUT,
        batch_id=batch_id,
    )


#: Where a finished reading is remembered, and for how long. The key names
#: the revision, so expiry only bounds how long one is trusted.
REMEMBERED = "topology.reading"
REMEMBERED_FOR = whole_number("TOPOLOGY_READING_KEPT_SECONDS", 30 * 24 * 60 * 60)


def reading_key(repository: str, revision: str, targets: Sequence[str]) -> str:
    """One name for one reading of one state of one repository.

    The targets are in it because a reading is asked what this repository joins
    to out of a fixed list, and a longer list is a different question.
    """
    from src.core.cache import keyed

    return keyed(repository, revision, *sorted(targets))


def already_read(repository: str, revision: str, targets: Sequence[str]) -> bool:
    """Whether this exact reading has been done and kept.

    A revision nobody could establish is never reused: an unknown state of a
    repository is not evidence that it is the state that was read.
    """
    if not revision:
        return False
    from src.core.cache import cache

    return bool(cache().get(REMEMBERED, reading_key(repository, revision, targets)))


def remember_read(repository: str, revision: str, targets: Sequence[str]) -> None:
    """Keep that this reading was done, so asking again does not pay twice."""
    if not revision:
        return
    from src.core.cache import cache

    cache().set(
        REMEMBERED,
        reading_key(repository, revision, targets),
        "read",
        ttl=REMEMBERED_FOR,
    )


def _scope(job: Job) -> Optional[Scope]:
    """Which workspace this job writes into, or None where it names none.

    A scope refuses to hold an empty value, so a job with no workspace has
    nowhere to put what it reads and is a failure rather than an exception.
    """
    workspace = str(job.payload.get("workspace") or "")
    return Scope.from_mapping({"workspace": workspace}) if workspace else None


def _token(repository: str) -> Optional[str]:
    """A token for the one repository this job names.

    Minted here rather than carried. A user token in a job payload is a
    credential written to the database, readable long after the person who
    granted it stopped meaning to, and good for far more than the repository
    the job is about.
    """
    owner, _, name = repository.partition("/")
    if not owner or not name:
        return None
    try:
        from src.integrations.github.github import GitHub

        return GitHub().get_installation_access_token(owner, name)
    except Exception as error:  # noqa: BLE001 - one repository, not the discovery
        logger.warning("No token to read %s with: %s", repository, error)
        return None


def _kept(repository, scope, proposals, persist: bool) -> int:
    if not persist:
        return 0
    kept = 0
    for proposal in proposals:
        try:
            repository.put_relationship(scope, proposal)
            kept += 1
        except ValueError as error:
            logger.warning("Could not record a proposed relationship: %s", error)
    return kept


class Manifests:
    """Proposes what the repositories of a system declare about each other."""

    kind = MANIFESTS

    def __init__(self, services: ServiceRegistry = service_registry) -> None:
        self._services = services

    def run(self, job: Job) -> JobOutcome:
        from src.core.topology.inference import infer_dependencies
        from src.core.topology.manifests import read_manifests
        from src.core.topology.store import topology_repository

        assets = list(job.payload.get("assets") or ())
        if not assets:
            return JobOutcome.ok()
        scope = _scope(job)
        if scope is None:
            return JobOutcome.failed("this job names no workspace to write into")

        tokens = {
            asset["repository"]: _token(asset["repository"])
            for asset in assets
            if asset.get("repository")
        }
        readable = [one for one in assets if tokens.get(one.get("repository"))]
        if not readable:
            return JobOutcome.failed("nothing here could be read with an app token")

        # Grouped by token so each group is still read concurrently. An
        # installation is per repository, and a system may hold repositories
        # from more than one.
        grouped: dict[str, list] = {}
        for asset in readable:
            grouped.setdefault(tokens[asset["repository"]], []).append(asset)

        async def everything():
            read = await asyncio.gather(
                *[read_manifests(group, token) for token, group in grouped.items()]
            )
            return [one for group in read for one in group]

        found = asyncio.run(everything())

        system_id = job.payload.get("system_id")
        held = {asset["entity_id"] for asset in readable}
        proposals = tuple(
            _with(proposal, system_id)
            for proposal in infer_dependencies(found)
            if proposal.source_id in held and proposal.target_id in held
        )
        _kept(
            topology_repository(self._services),
            scope,
            proposals,
            bool(job.payload.get("persist", True)),
        )
        logger.info(
            "Read %d manifests across %d repositories and proposed %d connections",
            len(found),
            len(readable),
            len(proposals),
        )
        return JobOutcome.ok()


def _with(proposal, system_id):
    from dataclasses import replace

    return replace(
        proposal,
        properties={
            **proposal.properties,
            "system_id": system_id,
            "provenance": {"evidence": [asdict(item) for item in proposal.evidence]},
        },
    )


class Readings:
    """Reads one repository's code for the connections it declares nowhere."""

    kind = READING

    def __init__(self, services: ServiceRegistry = service_registry) -> None:
        self._services = services

    def run(self, job: Job) -> JobOutcome:
        from src.core.model import provider_for
        from src.core.search import SearchQuery, Searcher
        from src.core.settings.configuration import Configuration
        from src.core.topology.proposing import WhatItReads
        from src.core.topology.reading import contents_reader
        from src.core.topology.store import topology_repository

        entity_id = str(job.payload.get("entity_id") or "")
        name = str(job.payload.get("repository") or "")
        targets = tuple(job.payload.get("targets") or ())
        if not entity_id or not name or not targets:
            return JobOutcome.failed("this job names nothing to read")

        workspace = str(job.payload.get("workspace") or "")
        revision = str(job.payload.get("revision") or "")
        scope = _scope(job)
        if scope is None:
            return JobOutcome.failed("this job names no workspace to write into")
        store = topology_repository(self._services)

        token = _token(name)
        if not token:
            return JobOutcome.failed(f"no token to read {name} with")

        # Attributed to the workspace as well as the repository, so what a
        # discovery costs is answerable to whoever asked for it.
        provider = provider_for(
            Configuration(repository=name, workspace=workspace or None)
        )
        if provider is None:
            return JobOutcome.failed("no model is configured to read with")

        def search(terms):
            try:
                searcher = self._services.resolve(Searcher)
            except LookupError:
                return ()
            result = searcher.search(
                SearchQuery(Scope.from_mapping({"repository": name}), tuple(terms))
            )
            return [
                {
                    "path": match.path,
                    "start_line": match.start_line,
                    "end_line": match.end_line,
                    "text": match.text,
                }
                for match in result.matches
            ]

        def corroborates(source_id: str, target_id: str) -> bool:
            edges = store.get_relationships(scope, frozenset({source_id, target_id}))
            return any(
                {edge.source_id, edge.target_id} == {source_id, target_id}
                and not edge.stale
                and (edge.properties.get("provenance") or {}).get("read_from")
                == "code_index"
                for edge in edges
            )

        reads = WhatItReads(
            contents_reader(name, token, revision), search, corroborates
        )
        proposals = reads.propose(
            provider,
            entity_id=entity_id,
            repository=name,
            targets=targets,
            about=str(job.payload.get("about") or ""),
            revision=revision,
        )
        _kept(store, scope, proposals, bool(job.payload.get("persist", True)))
        if reads.refused:
            # A reading that never ran and one that found nothing look the same
            # in what was written down, and only the second is an answer.
            return JobOutcome.failed(reads.refused)
        remember_read(name, revision, targets)
        logger.info("Read %s and proposed %d connections", name, len(proposals))
        return JobOutcome.ok()


def discover(
    assets: Sequence[Mapping[str, str]],
    *,
    workspace: str,
    system_id: str,
    targets: Sequence[str],
    about: Mapping[str, str],
    persist: bool = True,
    refresh: bool = False,
    services: ServiceRegistry = service_registry,
) -> dict:
    """Ask for a discovery, and answer with what was asked for.

    Everything expensive is decided here, where the user is: which repositories
    may be read, which of them have not changed since the last reading, and how
    many of the rest one asking may pay for.
    """
    from src.core.jobs import enqueue, job_store

    store = job_store(services)
    readable = [one for one in assets if one.get("entity_id") and one.get("repository")]

    # Where each repository stands, so a reading already done at that state
    # is not paid for again. One cheap call each, against several model calls.
    revisions: dict[str, str] = {}
    outstanding: list[Mapping[str, str]] = []
    reused: list[str] = []
    for asset in readable:
        name = asset["repository"]
        token = _token(name)
        revision = head_revision(name, token) if token else ""
        revisions[name] = revision
        joins = [one for one in targets if one != asset["entity_id"]]
        if not refresh and already_read(name, revision, joins):
            reused.append(name)
            continue
        outstanding.append(asset)

    over = max(0, len(outstanding) - MOST_READ)
    outstanding = outstanding[:MOST_READ]

    batch = store.open(NAME, 1 + len(outstanding), tenant=workspace)
    enqueue(
        manifests_job(
            readable,
            workspace=workspace,
            system_id=system_id,
            persist=persist,
            batch_id=batch.id,
        ),
        services=services,
    )
    for asset in outstanding:
        joins = [one for one in targets if one != asset["entity_id"]]
        enqueue(
            reading_job(
                asset,
                workspace=workspace,
                system_id=system_id,
                targets=joins,
                about="\n".join(
                    f"- {one}: {about.get(one) or one}" for one in joins[:MOST_TARGETS]
                ),
                revision=revisions.get(asset["repository"], ""),
                persist=persist,
                batch_id=batch.id,
            ),
            services=services,
        )

    return {
        "batch_id": batch.id,
        "total": batch.total,
        "reading": [one["repository"] for one in outstanding],
        "reused": reused,
        "not_read": over,
    }

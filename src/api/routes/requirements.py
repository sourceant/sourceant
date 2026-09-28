from dataclasses import asdict
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import Session, select

from src.auth import get_current_user
from src.api.routes.knowledge import get_knowledge
from src.api.routes.topology import get_scope
from src.config.db import get_engine
from src.core.requirements import (
    CoverageQuery,
    GitHubIssueRequirements,
    KnowledgeBackedRequirements,
    Requirement,
    RequirementLink,
    RequirementQuery,
    RequirementsRepository,
    SQLRequirementsRepository,
)
from src.core.grouping import groups as grouping
from src.core.requirements.grouping import TYPE as REQUIREMENT
from src.core.responses import success_response
from src.core.scope import Scope
from src.core.services import service_registry
from src.core.workspace import repositories_of
from src.models.repository import Repository

from src.core.requirements.assurance import (
    AcceptanceCriterion,
    EvidenceRecord,
    CriterionAssessment,
)
from src.core.requirements.assurance_sql import SQLAssuranceRepository
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

router = APIRouter()


@lru_cache(maxsize=1)
def sql_requirements(engine):
    return SQLRequirementsRepository(engine)


def get_requirements():
    try:
        return service_registry.resolve(RequirementsRepository)
    except LookupError:
        engine = get_engine()
        if engine is None:
            raise HTTPException(503, "Requirements store is unavailable")
        return KnowledgeBackedRequirements(sql_requirements(engine), get_knowledge())


def connected_names(user: dict) -> list[str]:
    engine = get_engine()
    if engine is None:
        return []
    workspace = get_scope(user).get("workspace")
    with Session(engine) as session:
        ids = repositories_of(session, workspace)
        claimed = (user.get("scope") or {}).get("repository_ids", [])
        ids = set(ids) | {int(value) for value in claimed if str(value).isdigit()}
        return (
            sorted(
                session.exec(
                    select(Repository.full_name).where(Repository.id.in_(ids))
                ).all()
            )
            if ids
            else []
        )


def request_scopes(user: dict, repo: str = "") -> list[Scope]:
    scope = get_scope(user)
    names = connected_names(user)
    if repo:
        return [scope.extend({"repository": repo})] if repo in names else []
    return [scope, *(scope.extend({"repository": name}) for name in names)]


def write_scope(user: dict, repo: str) -> Scope:
    if not repo:
        return get_scope(user)
    scopes = request_scopes(user, repo)
    if not scopes:
        raise HTTPException(403, "Repository is outside this workspace")
    return scopes[0]


class RequirementPayload(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def public_types(cls, values):
        if isinstance(values, dict) and {"kind", "kinds", "target_kind"}.intersection(
            values
        ):
            raise ValueError("Use type, types, and target_type for requirements")
        return values


class RequirementInput(RequirementPayload):

    @field_validator("properties", mode="before")
    @classmethod
    def properties_map(cls, value):
        return {} if value is None else value

    @field_validator("external_ref", "priority", "repo", mode="before")
    @classmethod
    def optional_text(cls, value):
        return "" if value is None else value

    id: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    external_ref: str = Field(default="", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)
    priority: str = Field(default="", max_length=255)
    repo: str = ""


class LinkInput(RequirementPayload):

    @field_validator("properties", mode="before")
    @classmethod
    def properties_map(cls, value):
        return {} if value is None else value

    id: str = Field(min_length=1, max_length=255)
    requirement_id: str = Field(min_length=1, max_length=255)
    target_type: str
    target_id: str = Field(min_length=1, max_length=500)
    relation: str = Field(default="", max_length=64)
    properties: dict[str, Any] = Field(default_factory=dict)
    repo: str = ""


class ImportInput(BaseModel):
    repo: str = Field(pattern=r"^[^/]+/[^/]+$")


class ScenarioInput(BaseModel):
    source: str = Field(min_length=1, max_length=100_000)


@router.post("/scenarios/parse")
def parse_scenarios(payload: ScenarioInput, user: dict = Depends(get_current_user)):
    from src.core.requirements.scenarios import parse_scenarios as parse

    get_scope(user)
    try:
        return success_response(parse(payload.source))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("")
def search(
    repo: str = "",
    types: list[str] = Query([], max_length=100),
    statuses: list[str] = Query([], max_length=100),
    priorities: list[str] = Query([], max_length=100),
    ids: list[str] = Query([], max_length=100),
    groups: list[str] = Query([], max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    filing = grouping()
    if groups and filing is None:
        raise HTTPException(503, "Grouping is unavailable")
    held = _held_by(filing, user, frozenset(groups)) if groups else None
    found = []
    total = 0
    remaining_offset = offset
    for scope in request_scopes(user, repo):
        wanted = frozenset(ids)
        if held is not None:
            wanted = held.get(scope, frozenset())
            if not wanted:
                continue
            if ids:
                wanted = wanted & frozenset(ids)
                if not wanted:
                    continue
        result = store.search(
            RequirementQuery(
                scope=scope,
                types=frozenset(types),
                statuses=frozenset(statuses),
                priorities=frozenset(priorities),
                ids=wanted,
                limit=limit,
                offset=remaining_offset,
            )
        )
        total += result.total
        remaining_offset = max(0, remaining_offset - result.total)
        filed = (
            filing.filed_under(
                get_scope(user),
                REQUIREMENT,
                frozenset(item.id for item in result.items),
                scope,
            )
            if filing is not None
            else {}
        )
        found.extend(
            {
                **asdict(item),
                "repo": scope.get("repository", ""),
                "group_ids": list(filed.get(item.id, ())),
            }
            for item in result.items
        )
    return {"data": found[:limit], "total": total, "has_more": offset + limit < total}


def _held_by(filing, user: dict, wanted: frozenset[str]):
    """Which requirements those groups hold, by the scope each is filed in."""
    if filing is None:
        return {}
    held: dict[Scope, set[str]] = {}
    for member in filing.members(get_scope(user), wanted):
        if member.member_type != REQUIREMENT:
            continue
        held.setdefault(member.member_scope, set()).add(member.member_id)
    return {scope: frozenset(ids) for scope, ids in held.items()}


@router.get("/coverage")
def coverage(
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    reports = [
        (scope, store.coverage(CoverageQuery(scope=scope)))
        for scope in request_scopes(user, repo)
    ]
    return success_response(
        {
            "items": [
                {**asdict(item), "repo": scope.get("repository", "")}
                for scope, report in reports
                for item in report.items
            ],
            "truncated": any(report.truncated for _, report in reports),
        }
    )


@router.get("/links")
def links(
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    return success_response(
        [
            {**asdict(link), "repo": scope.get("repository", "")}
            for scope in request_scopes(user, repo)
            for link in store.get_links(scope, frozenset())
        ]
    )


@router.put("")
def put(
    payload: RequirementInput,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    scope = write_scope(user, payload.repo or repo)
    try:
        item = Requirement(**payload.model_dump(exclude={"repo"}))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    store.put(scope, item)
    return success_response(asdict(item))


@router.put("/links")
def put_link(
    payload: LinkInput,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    scope = write_scope(user, payload.repo or repo)
    try:
        link = RequirementLink(**payload.model_dump(exclude={"repo"}))
        if link.target_type == "artifact":
            from src.api.routes.artifacts import get_artifacts, key_from_reference

            if (
                get_artifacts().get(key_from_reference(get_scope(user), link.target_id))
                is None
            ):
                raise HTTPException(404, "Artifact not found")
        store.put_link(scope, link)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return success_response(asdict(link))


@router.delete("/links/{identifier:path}")
def remove_link(
    identifier: str,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    store.remove_link(write_scope(user, repo), identifier)
    return success_response({"deleted": True})


@router.post("/import")
async def import_issues(
    payload: ImportInput,
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    scope = write_scope(user, payload.repo)
    token = user.get("github_token")
    if not token:
        raise HTTPException(400, "No GitHub token available")
    issues = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for label in ("requirement", "acceptance-criteria"):
            page = 1
            while True:
                response = await client.get(
                    f"https://api.github.com/repos/{payload.repo}/issues",
                    params={
                        "labels": label,
                        "state": "all",
                        "per_page": 100,
                        "page": page,
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if response.status_code != 200:
                    raise HTTPException(502, "Could not read GitHub issues")
                batch = response.json()
                for issue in batch:
                    if "pull_request" not in issue:
                        issues[issue["number"]] = issue
                if len(batch) < 100:
                    break
                page += 1
    imported = GitHubIssueRequirements(lambda repository, labels: issues.values()).sync(
        scope
    )
    for item in imported:
        store.put(scope, item)
    return success_response({"imported": len(imported)})


@router.delete("/{identifier:path}")
def remove(
    identifier: str,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_requirements),
):
    store.remove(write_scope(user, repo), identifier)
    return success_response({"deleted": True})


def get_assurance():
    engine = get_engine()
    if engine is None or not inspect(engine).has_table("requirement_assessments"):
        raise HTTPException(
            503, "Acceptance and evidence storage requires the requirements migration"
        )
    return SQLAssuranceRepository(engine)


class CriterionInput(BaseModel):
    criterion: AcceptanceCriterion
    expected_revision: str = ""


def assurance_write(action):
    try:
        return success_response(action())
    except IntegrityError:
        raise HTTPException(
            409, "A record with this identity or a newer revision already exists"
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.put("/criteria")
def put_criterion(
    payload: CriterionInput,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(
        lambda: store.put_criterion(
            scope, payload.criterion, payload.expected_revision, str(user["user_id"])
        )
    )


@router.post("/evidence")
def record_evidence(
    payload: EvidenceRecord,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(
        lambda: store.record_evidence(scope, payload, str(user["user_id"]))
    )


@router.get("/evidence")
def list_evidence(
    repo: str = "",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    return success_response(store.evidence(write_scope(user, repo), limit, offset))


@router.post("/assessments")
def assess_criterion(
    payload: CriterionAssessment,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(lambda: store.assess(scope, payload, str(user["user_id"])))


@router.get("/assurance")
def requirement_assurance(
    requirement_id: str,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(lambda: store.report(scope, requirement_id))


@router.get("/assurance/history")
def assurance_history(
    requirement_id: str,
    repo: str = "",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(lambda: store.history(scope, requirement_id, limit, offset))


@router.get("/evidence/{evidence_id}")
def get_evidence_record(
    evidence_id: str,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_assurance),
):
    scope = write_scope(user, repo)
    return assurance_write(lambda: store.get_evidence(scope, evidence_id))

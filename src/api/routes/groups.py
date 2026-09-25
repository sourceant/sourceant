from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src.api.routes.requirements import write_scope
from src.api.routes.topology import get_scope
from src.auth import get_current_user
from src.core.grouping import groups as grouping
from src.core.groups import MAX_DEPTH, GroupMember, GroupQuery, GroupRollupReader, Group
from src.core.responses import success_response

router = APIRouter()


def get_groups():
    """The group store, or a plain answer that there is nowhere to keep them."""
    store = grouping()
    if store is None:
        raise HTTPException(503, "Grouping is unavailable")
    return store


class GroupInput(BaseModel):
    @field_validator("properties", mode="before")
    @classmethod
    def properties_map(cls, value):
        return {} if value is None else value

    @field_validator("parent_id", "external_ref", mode="before")
    @classmethod
    def optional_text(cls, value):
        return "" if value is None else value

    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=255)
    parent_id: str = Field(default="", max_length=255)
    external_ref: str = Field(default="", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)


class MemberInput(BaseModel):
    """What to file. Filing something already filed elsewhere adds it here
    rather than moving it, because a requirement can belong to more than one."""

    member_type: str = Field(min_length=1, max_length=64)
    member_id: str = Field(min_length=1, max_length=500)
    repo: str = ""


@router.get("")
def search(
    ids: list[str] = Query([], max_length=100),
    types: list[str] = Query([], max_length=100),
    statuses: list[str] = Query([], max_length=100),
    parent_ids: list[str] = Query([], max_length=100),
    roots: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    # An empty string cannot be asked for through the gateway, which drops
    # empty query values, so the outermost groups get a name of their own.
    wanted = frozenset({""}) if roots else frozenset(parent_ids)
    result = store.search(
        GroupQuery(
            scope=get_scope(user),
            ids=frozenset(ids),
            types=frozenset(types),
            statuses=frozenset(statuses),
            parent_ids=wanted,
            limit=limit,
            offset=offset,
        )
    )
    return {
        "data": [asdict(item) for item in result.items],
        "total": result.total,
        "has_more": result.has_more,
    }


@router.get("/rollup")
def rollup(
    ids: list[str] = Query([], max_length=100),
    depth: int = Query(MAX_DEPTH, ge=1, le=MAX_DEPTH),
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    if not isinstance(store, GroupRollupReader):
        raise HTTPException(503, "This group store cannot add a group up")
    rolled = store.rollup(get_scope(user), frozenset(ids), depth=depth)
    return success_response({"items": [asdict(item) for item in rolled]})


@router.get("/{identifier:path}/members")
def members(
    identifier: str,
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    held = store.members(get_scope(user), frozenset({identifier}))
    return success_response(
        [
            {
                "group_id": one.group_id,
                "member_type": one.member_type,
                "member_id": one.member_id,
                "repo": one.member_scope.get("repository", ""),
            }
            for one in held
        ]
    )


@router.put("")
def put(
    payload: GroupInput,
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    group = Group(**payload.model_dump())
    try:
        store.put(get_scope(user), group)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return success_response(asdict(group))


@router.put("/{identifier}/members")
def place(
    identifier: str,
    payload: MemberInput,
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    member = GroupMember(
        group_id=identifier,
        member_type=payload.member_type,
        member_id=payload.member_id,
        member_scope=write_scope(user, payload.repo),
    )
    try:
        store.place(get_scope(user), member)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return success_response(
        {
            "group_id": identifier,
            "member_type": member.member_type,
            "member_id": member.member_id,
            "repo": payload.repo,
        }
    )


@router.delete("/{identifier}/members/{member_type}/{member_id:path}")
def unfile(
    identifier: str,
    member_type: str,
    member_id: str,
    repo: str = "",
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    removed = store.unfile(
        get_scope(user), identifier, member_type, member_id, write_scope(user, repo)
    )
    return success_response({"unfiled": removed})


@router.delete("/{identifier:path}")
def remove(
    identifier: str,
    user: dict = Depends(get_current_user),
    store=Depends(get_groups),
):
    try:
        store.remove(get_scope(user), identifier)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return success_response({"deleted": True})

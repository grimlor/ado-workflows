"""Partial stubs for azure.devops.v7_1.work_item_tracking.models."""

from __future__ import annotations

from typing import Any


class Wiql:
    query: str
    def __init__(self, *, query: str | None = None, **kwargs: Any) -> None: ...


class WorkItemReference:
    id: int
    url: str


class WorkItemQueryResult:
    work_items: list[WorkItemReference] | None


class WorkItem:
    id: int
    url: str
    fields: dict[str, Any]


class TeamContext:
    project: str | None
    project_id: str | None
    team: str | None
    team_id: str | None
    def __init__(
        self,
        project: str | None = None,
        project_id: str | None = None,
        team: str | None = None,
        team_id: str | None = None,
    ) -> None: ...

"""Partial stub for azure.devops.v7_1.work_item_tracking.work_item_tracking_client."""

from typing import Any

from azure.devops.v7_1.work_item_tracking.models import (
    JsonPatchOperation,
    TeamContext,
    Wiql,
    WorkItem,
    WorkItemQueryResult,
    WorkItemTypeFieldWithReferences,
)

class WorkItemTrackingClient:
    def get_work_item(self, id: int, **kwargs: Any) -> WorkItem: ...
    def query_by_wiql(
        self,
        wiql: Wiql,
        team_context: TeamContext | None = None,
        time_precision: bool | None = None,
        top: int | None = None,
    ) -> WorkItemQueryResult: ...
    def get_work_items(
        self,
        ids: list[int],
        project: str | None = None,
        fields: list[str] | None = None,
        as_of: Any | None = None,
        expand: str | None = None,
        error_policy: str | None = None,
    ) -> list[WorkItem]: ...
    def update_work_item(
        self,
        document: list[JsonPatchOperation],
        id: int,
        project: str | None = None,
    ) -> WorkItem: ...
    def create_work_item(
        self,
        document: list[JsonPatchOperation],
        project: str,
        type: str,
    ) -> WorkItem: ...
    def get_work_item_type_fields_with_references(
        self,
        project: str,
        type: str,
    ) -> list[WorkItemTypeFieldWithReferences]: ...

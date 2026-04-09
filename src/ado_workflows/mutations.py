"""
Work item mutation functions — create, update, move, clone, and field discovery.

Wraps the Azure DevOps ``WorkItemTrackingClient`` mutation methods with
``dict[str, Any]`` → ``JsonPatchDocument`` conversion and consistent
:class:`~actionable_errors.ActionableError` handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

from ado_workflows._mapping import map_work_item_detail
from ado_workflows.errors import classify_ado_error
from ado_workflows.models import WorkItemDetail, WorkItemFieldInfo

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient

_HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"


def update_work_item(
    client: AdoClient,
    project: str,
    work_item_id: int,
    *,
    fields: dict[str, Any],
) -> WorkItemDetail:
    """
    Update fields on an existing work item.

    Converts *fields* to a JSON Patch document and calls
    ``client.work_items.update_work_item()``.

    Returns:
        The updated :class:`WorkItemDetail`.

    Raises:
        ActionableError: When the SDK call fails.

    """
    document = _build_patch_document(fields)
    try:
        raw = client.work_items.update_work_item(
            document,
            work_item_id,
            project=project,
        )
    except Exception as exc:
        raise classify_ado_error(
            exc,
            operation=f"update work item {work_item_id} in '{project}'",
            context_hint=project,
        ) from exc

    return map_work_item_detail(raw)


def create_work_item(
    client: AdoClient,
    project: str,
    work_item_type: str,
    *,
    fields: dict[str, Any],
    parent_id: int | None = None,
) -> WorkItemDetail:
    """
    Create a new work item of any type with specified fields.

    Converts *fields* to a JSON Patch document. When *parent_id* is
    given, appends a ``System.LinkTypes.Hierarchy-Reverse`` relation
    linking the new item as a child of the parent.

    Returns:
        The created :class:`WorkItemDetail`.

    Raises:
        ActionableError: When the SDK call fails.

    """
    document = _build_patch_document(fields)

    if parent_id is not None:
        document.append(
            JsonPatchOperation(
                op="add",
                path="/relations/-",
                value={
                    "rel": _HIERARCHY_REVERSE,
                    "url": (f"https://dev.azure.com/{project}/_apis/wit/workItems/{parent_id}"),
                },
            )
        )

    try:
        raw = client.work_items.create_work_item(
            document,
            project,
            work_item_type,
        )
    except Exception as exc:
        raise classify_ado_error(
            exc,
            operation=f"create {work_item_type} in '{project}'",
            context_hint=project,
        ) from exc

    return map_work_item_detail(raw)


def move_work_items_to_sprint(
    client: AdoClient,
    project: str,
    work_item_ids: list[int],
    iteration_path: str,
) -> list[WorkItemDetail]:
    """
    Move work items to a target sprint by updating their iteration path.

    Calls :func:`update_work_item` for each ID, setting
    ``System.IterationPath``. Does **not** auto-include children —
    callers decide which IDs to move.

    Returns:
        List of updated :class:`WorkItemDetail`.

    Raises:
        ActionableError: When any SDK call fails.

    """
    if not work_item_ids:
        return []

    return [
        update_work_item(
            client,
            project,
            wid,
            fields={"System.IterationPath": iteration_path},
        )
        for wid in work_item_ids
    ]


def clone_work_item(
    client: AdoClient,
    project: str,
    source_id: int,
    *,
    field_overrides: dict[str, Any] | None = None,
) -> WorkItemDetail:
    """
    Clone a work item into a new item of the same type.

    Reads the source work item with full field expansion, copies its
    fields into a new item of the same type, and applies any
    *field_overrides*. Preserves the parent link if present on the
    source. Does **not** close the source — callers handle that via
    :func:`update_work_item`.

    Returns:
        The created :class:`WorkItemDetail`.

    Raises:
        ActionableError: When any SDK call fails.

    """
    try:
        raw_source = client.work_items.get_work_item(
            source_id,
            project=project,
            expand="All",
        )
    except Exception as exc:
        raise classify_ado_error(
            exc,
            operation=f"fetch source work item {source_id} in '{project}'",
            context_hint=project,
        ) from exc

    source = map_work_item_detail(raw_source)

    # Build clone fields from source, then apply overrides
    clone_fields = dict(source.fields)
    if field_overrides:
        clone_fields.update(field_overrides)

    return create_work_item(
        client,
        project,
        source.work_item_type,
        fields=clone_fields,
        parent_id=source.parent_id,
    )


def get_work_item_type_fields(
    client: AdoClient,
    project: str,
    work_item_type: str,
) -> list[WorkItemFieldInfo]:
    """
    Discover available fields for a work item type in a project.

    Returns:
        List of :class:`WorkItemFieldInfo` with name, reference name,
        type, and whether the field is required.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        raw_fields = client.work_items.get_work_item_type_fields_with_references(
            project,
            work_item_type,
        )
    except Exception as exc:
        raise classify_ado_error(
            exc,
            operation=f"get fields for '{work_item_type}' in '{project}'",
            context_hint=project,
        ) from exc

    return [
        WorkItemFieldInfo(
            name=f.name,
            reference_name=f.reference_name,
            field_type=f.type,
            is_required=f.always_required,
        )
        for f in raw_fields
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_patch_document(
    fields: dict[str, Any],
) -> list[JsonPatchOperation]:
    """Convert a ``{field_ref_name: value}`` dict to a JSON Patch document."""
    return [
        JsonPatchOperation(op="add", path=f"/fields/{name}", value=value)
        for name, value in fields.items()
    ]

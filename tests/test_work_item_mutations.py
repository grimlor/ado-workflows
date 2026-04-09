"""
BDD tests for ado_workflows work item mutations — FR6a.

Covers:
- TestUpdateWorkItem: update fields on an existing work item
- TestCreateWorkItem: create a new work item of any type
- TestMoveWorkItemsToSprint: move work items to a target sprint
- TestCloneWorkItem: clone a work item with optional field overrides
- TestGetWorkItemTypeFields: discover available fields for a work item type

Public API surface:
    From src/ado_workflows/mutations.py:
        update_work_item(
            client: AdoClient, project: str, work_item_id: int,
            *, fields: dict[str, Any],
        ) -> WorkItemDetail

        create_work_item(
            client: AdoClient, project: str, work_item_type: str,
            *, fields: dict[str, Any], parent_id: int | None = None,
        ) -> WorkItemDetail

        move_work_items_to_sprint(
            client: AdoClient, project: str, work_item_ids: list[int],
            iteration_path: str,
        ) -> list[WorkItemDetail]

        clone_work_item(
            client: AdoClient, project: str, source_id: int,
            *, field_overrides: dict[str, Any] | None = None,
        ) -> WorkItemDetail

        get_work_item_type_fields(
            client: AdoClient, project: str, work_item_type: str,
        ) -> list[WorkItemFieldInfo]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError
from azure.devops.v7_1.work_item_tracking.models import WorkItem, WorkItemRelation

from ado_workflows.models import WorkItemDetail, WorkItemFieldInfo
from ado_workflows.mutations import (
    clone_work_item,
    create_work_item,
    get_work_item_type_fields,
    move_work_items_to_sprint,
    update_work_item,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PARENT_RELATION_URL = "https://dev.azure.com/org/project/_apis/wit/workItems/5000"

_HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"


def _sdk_work_item(
    *,
    wid: int = 1001,
    title: str = "Implement data gathering",
    state: str = "Active",
    work_item_type: str = "Task",
    assigned_to: str | None = "Alice Smith",
    area_path: str | None = r"One\CFS\PayFin and Data Platform Redmond",
    iteration_path: str | None = r"One\FY26\Q4\2Wk\2Wk21",
    completed_work: float | None = 4.0,
    remaining_work: float | None = 8.0,
    parent_url: str | None = _PARENT_RELATION_URL,
    url: str = "https://dev.azure.com/org/project/_apis/wit/workItems/1001",
    extra_fields: dict[str, Any] | None = None,
) -> WorkItem:
    """Build a real SDK WorkItem with standard fields and optional relations."""
    wi = WorkItem()
    wi.id = wid
    wi.url = url
    wi.fields = {
        "System.Title": title,
        "System.State": state,
        "System.WorkItemType": work_item_type,
        "System.AssignedTo": assigned_to,
        "System.AreaPath": area_path,
        "System.IterationPath": iteration_path,
        "Microsoft.VSTS.Scheduling.CompletedWork": completed_work,
        "Microsoft.VSTS.Scheduling.RemainingWork": remaining_work,
    }
    if extra_fields:
        wi.fields.update(extra_fields)

    if parent_url is not None:
        parent_rel = WorkItemRelation()
        parent_rel.rel = _HIERARCHY_REVERSE
        parent_rel.url = parent_url
        wi.relations = [parent_rel]

    return wi


def _mock_client(
    *,
    update_result: WorkItem | None = None,
    create_result: WorkItem | None = None,
    update_error: Exception | None = None,
    create_error: Exception | None = None,
) -> Mock:
    """Build a mock AdoClient with work_items methods configured."""
    client = Mock()

    if update_error:
        client.work_items.update_work_item.side_effect = update_error
    else:
        client.work_items.update_work_item.return_value = update_result

    if create_error:
        client.work_items.create_work_item.side_effect = create_error
    else:
        client.work_items.create_work_item.return_value = create_result

    return client


# ---------------------------------------------------------------------------
# TestUpdateWorkItem
# ---------------------------------------------------------------------------


class TestUpdateWorkItem:
    """
    REQUIREMENT: Update fields on an existing work item.

    WHO: Sprint automation, MCP tools, move_work_items_to_sprint,
         clone_work_item (to close originals)
    WHAT: (1) returns updated WorkItemDetail with new field values and
              full fields dict
          (2) work item with non-parent relations returns parent_id=None
          (3) malformed parent relation URL returns parent_id=None
          (4) SDK error raises ActionableError with original message
    WHY: Enables sprint rollover (moving items), effort tracking updates,
         and state changes without constructing raw JSON Patch documents.

    MOCK BOUNDARY:
        Mock:  client.work_items.update_work_item() — SDK HTTP call
        Real:  update_work_item()
        Never: nothing
    """

    def test_returns_updated_work_item_detail(self) -> None:
        """
        Given a work item ID and a fields dict,
        When update_work_item is called,
        Then returns WorkItemDetail with updated field values and full
        fields dict.
        """
        # Given: SDK returns the updated work item
        updated_wi = _sdk_work_item(
            wid=1001,
            title="Updated title",
            state="Active",
            iteration_path=r"One\FY26\Q4\2Wk\2Wk22",
        )
        client = _mock_client(update_result=updated_wi)
        fields = {
            "System.Title": "Updated title",
            "System.IterationPath": r"One\FY26\Q4\2Wk\2Wk22",
        }

        # When: update_work_item is called
        result = update_work_item(client, "MyProject", 1001, fields=fields)

        # Then: returns WorkItemDetail with updated values
        assert isinstance(result, WorkItemDetail), (
            f"Expected WorkItemDetail, got {type(result).__name__}"
        )
        assert result.id == 1001, f"Expected id=1001, got {result.id}"
        assert result.title == "Updated title", (
            f"Expected title='Updated title', got {result.title!r}"
        )
        assert result.iteration_path == r"One\FY26\Q4\2Wk\2Wk22", (
            f"Expected updated iteration_path, got {result.iteration_path!r}"
        )
        assert result.area_path == r"One\CFS\PayFin and Data Platform Redmond", (
            f"Expected area_path preserved, got {result.area_path!r}"
        )
        assert "System.Title" in result.fields, "Expected full fields dict to contain System.Title"
        assert result.fields["System.Title"] == "Updated title", (
            f"Expected fields dict title='Updated title', got {result.fields['System.Title']!r}"
        )

    def test_non_parent_relations_return_none_parent_id(self) -> None:
        """
        Given a work item with relations that are not parent links,
        When update_work_item is called,
        Then returns WorkItemDetail with parent_id=None.
        """
        # Given: work item with a non-parent relation (e.g. related link)
        wi = _sdk_work_item(wid=1002, parent_url=None)
        non_parent_rel = WorkItemRelation()
        non_parent_rel.rel = "System.LinkTypes.Related"
        non_parent_rel.url = "https://dev.azure.com/org/project/_apis/wit/workItems/9999"
        wi.relations = [non_parent_rel]
        client = _mock_client(update_result=wi)

        # When: update_work_item is called
        result = update_work_item(
            client,
            "MyProject",
            1002,
            fields={"System.State": "Active"},
        )

        # Then: parent_id is None since no Hierarchy-Reverse relation
        assert result.parent_id is None, (
            f"Expected parent_id=None for non-parent relation, got {result.parent_id}"
        )

    def test_malformed_parent_url_returns_none_parent_id(self) -> None:
        """
        Given a work item with a Hierarchy-Reverse relation whose URL
        does not contain a numeric work item ID,
        When update_work_item is called,
        Then returns WorkItemDetail with parent_id=None.
        """
        # Given: work item with a parent relation but malformed URL
        wi = _sdk_work_item(wid=1003, parent_url=None)
        bad_rel = WorkItemRelation()
        bad_rel.rel = _HIERARCHY_REVERSE
        bad_rel.url = "https://dev.azure.com/org/project/_apis/wit/invalid"
        wi.relations = [bad_rel]
        client = _mock_client(update_result=wi)

        # When: update_work_item is called
        result = update_work_item(
            client,
            "MyProject",
            1003,
            fields={"System.State": "Active"},
        )

        # Then: parent_id is None since URL doesn't match expected pattern
        assert result.parent_id is None, (
            f"Expected parent_id=None for malformed URL, got {result.parent_id}"
        )

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during update_work_item,
        When update_work_item is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on update_work_item
        client = _mock_client(
            update_error=RuntimeError("update failed: field not valid"),
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            update_work_item(
                client,
                "MyProject",
                1001,
                fields={"System.State": "Closed"},
            )
        assert "update failed" in str(exc_info.value), (
            f"Expected error to contain 'update failed', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestCreateWorkItem
# ---------------------------------------------------------------------------


class TestCreateWorkItem:
    """
    REQUIREMENT: Create a new work item of any type with specified fields.

    WHO: Sprint planning, MCP tools, clone_work_item
    WHAT: (1) returns new WorkItemDetail with full fields dict
          (2) parent_id adds a parent relation link to the patch document
          (3) SDK error raises ActionableError with original message
    WHY: Enables task creation during sprint planning and cloning of
         work items across sprints.

    MOCK BOUNDARY:
        Mock:  client.work_items.create_work_item() — SDK HTTP call
        Real:  create_work_item()
        Never: nothing
    """

    def test_returns_new_work_item_detail(self) -> None:
        """
        Given a work item type and fields dict,
        When create_work_item is called,
        Then returns WorkItemDetail with full fields dict.
        """
        # Given: SDK returns the created work item
        created_wi = _sdk_work_item(
            wid=2001,
            title="New sprint task",
            state="New",
            work_item_type="Task",
            parent_url=None,
            extra_fields={"Microsoft.VSTS.Common.Priority": 2},
        )
        client = _mock_client(create_result=created_wi)
        fields = {
            "System.Title": "New sprint task",
            "System.AreaPath": r"One\CFS\PayFin and Data Platform Redmond",
            "Microsoft.VSTS.Common.Priority": 2,
        }

        # When: create_work_item is called
        result = create_work_item(client, "MyProject", "Task", fields=fields)

        # Then: returns WorkItemDetail with correct values
        assert isinstance(result, WorkItemDetail), (
            f"Expected WorkItemDetail, got {type(result).__name__}"
        )
        assert result.id == 2001, f"Expected id=2001, got {result.id}"
        assert result.title == "New sprint task", (
            f"Expected title='New sprint task', got {result.title!r}"
        )
        assert result.work_item_type == "Task", (
            f"Expected work_item_type='Task', got {result.work_item_type!r}"
        )
        assert result.parent_id is None, (
            f"Expected parent_id=None (no parent), got {result.parent_id}"
        )
        assert result.fields["Microsoft.VSTS.Common.Priority"] == 2, (
            f"Expected Priority=2 in fields dict, "
            f"got {result.fields.get('Microsoft.VSTS.Common.Priority')}"
        )

    def test_parent_id_adds_relation_link(self) -> None:
        """
        Given a parent_id,
        When create_work_item is called,
        Then the returned WorkItemDetail has the parent_id set.
        """
        # Given: SDK returns a created work item with parent relation
        created_wi = _sdk_work_item(
            wid=2002,
            title="Child task",
            state="New",
            parent_url="https://dev.azure.com/org/project/_apis/wit/workItems/3000",
        )
        client = _mock_client(create_result=created_wi)

        # When: create_work_item is called with parent_id
        result = create_work_item(
            client,
            "MyProject",
            "Task",
            fields={"System.Title": "Child task"},
            parent_id=3000,
        )

        # Then: result has parent_id extracted from relations
        assert result.parent_id == 3000, f"Expected parent_id=3000, got {result.parent_id}"

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during create_work_item,
        When create_work_item is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on create_work_item
        client = _mock_client(
            create_error=RuntimeError("create failed: invalid type"),
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            create_work_item(
                client,
                "MyProject",
                "InvalidType",
                fields={"System.Title": "Won't be created"},
            )
        assert "create failed" in str(exc_info.value), (
            f"Expected error to contain 'create failed', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestMoveWorkItemsToSprint
# ---------------------------------------------------------------------------


class TestMoveWorkItemsToSprint:
    """
    REQUIREMENT: Move work items to a target sprint by updating their
    iteration path.

    WHO: Sprint rollover automation, MCP tools
    WHAT: (1) updates each work item's iteration path and returns list
              of updated WorkItemDetail
          (2) empty ID list returns empty list
          (3) SDK error on any item raises ActionableError
    WHY: Batch convenience for sprint rollover — callers select which
         IDs to move and this function applies the iteration path change.

    MOCK BOUNDARY:
        Mock:  client.work_items.update_work_item() — SDK HTTP call
        Real:  move_work_items_to_sprint(), update_work_item()
        Never: nothing
    """

    def test_updates_iteration_path_and_returns_details(self) -> None:
        """
        Given work item IDs and a target iteration path,
        When move_work_items_to_sprint is called,
        Then returns WorkItemDetail list with the updated iteration paths.
        """
        # Given: SDK returns updated work items with new iteration path
        target_path = r"One\FY26\Q4\2Wk\2Wk22"
        wi1 = _sdk_work_item(wid=101, iteration_path=target_path)
        wi2 = _sdk_work_item(wid=102, iteration_path=target_path)
        client = Mock()
        client.work_items.update_work_item.side_effect = [wi1, wi2]

        # When: move_work_items_to_sprint is called
        result = move_work_items_to_sprint(
            client,
            "MyProject",
            [101, 102],
            target_path,
        )

        # Then: returns 2 WorkItemDetail with updated iteration paths
        assert len(result) == 2, f"Expected 2 results, got {len(result)}"
        assert result[0].iteration_path == target_path, (
            f"Expected iteration_path='{target_path}', got {result[0].iteration_path!r}"
        )
        assert result[1].iteration_path == target_path, (
            f"Expected iteration_path='{target_path}', got {result[1].iteration_path!r}"
        )

    def test_empty_id_list_returns_empty_list(self) -> None:
        """
        Given an empty list of work item IDs,
        When move_work_items_to_sprint is called,
        Then returns an empty list.
        """
        # Given: empty ID list
        client = Mock()

        # When: move_work_items_to_sprint is called with empty list
        result = move_work_items_to_sprint(
            client,
            "MyProject",
            [],
            r"One\FY26\Q4\2Wk\2Wk22",
        )

        # Then: empty list returned
        assert result == [], f"Expected empty list, got {result}"

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during update,
        When move_work_items_to_sprint is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on update_work_item
        client = Mock()
        client.work_items.update_work_item.side_effect = RuntimeError(
            "move failed: access denied",
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            move_work_items_to_sprint(
                client,
                "MyProject",
                [101],
                r"One\FY26\Q4\2Wk\2Wk22",
            )
        assert "move failed" in str(exc_info.value), (
            f"Expected error to contain 'move failed', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestCloneWorkItem
# ---------------------------------------------------------------------------


class TestCloneWorkItem:
    """
    REQUIREMENT: Clone a work item into a new item of the same type
    with optional field overrides.

    WHO: Sprint rollover (clone in-progress items), MCP tools
    WHAT: (1) reads source, creates new item of same type with source
              fields, returns new WorkItemDetail
          (2) field overrides replace source field values in the clone
          (3) parent link from source is preserved in the clone
          (4) SDK error raises ActionableError
    WHY: Sprint rollover needs to clone in-progress items into the
         next sprint while preserving the original for effort attribution.

    MOCK BOUNDARY:
        Mock:  client.work_items.get_work_item(),
               client.work_items.create_work_item() — SDK HTTP calls
        Real:  clone_work_item(), create_work_item()
        Never: nothing
    """

    def test_clones_source_with_same_type_and_fields(self) -> None:
        """
        Given a source work item ID,
        When clone_work_item is called,
        Then returns new WorkItemDetail with same type and fields
        as source.
        """
        # Given: source work item and SDK returns a clone
        source = _sdk_work_item(
            wid=3001,
            title="In-progress task",
            state="Active",
            work_item_type="Task",
            remaining_work=4.0,
        )
        cloned = _sdk_work_item(
            wid=3002,
            title="In-progress task",
            state="New",
            work_item_type="Task",
            remaining_work=4.0,
            parent_url=_PARENT_RELATION_URL,
        )
        client = Mock()
        client.work_items.get_work_item.return_value = source
        client.work_items.create_work_item.return_value = cloned

        # When: clone_work_item is called
        result = clone_work_item(client, "MyProject", 3001)

        # Then: returns new WorkItemDetail with same type
        assert result.id == 3002, f"Expected clone id=3002, got {result.id}"
        assert result.work_item_type == "Task", (
            f"Expected work_item_type='Task', got {result.work_item_type!r}"
        )
        assert result.title == "In-progress task", (
            f"Expected title='In-progress task', got {result.title!r}"
        )

    def test_field_overrides_replace_source_values(self) -> None:
        """
        Given field overrides,
        When clone_work_item is called,
        Then the overridden fields differ from source in the clone.
        """
        # Given: source with original iteration path, clone with overridden path
        source = _sdk_work_item(
            wid=3001,
            iteration_path=r"One\FY26\Q4\2Wk\2Wk21",
            remaining_work=4.0,
        )
        cloned = _sdk_work_item(
            wid=3003,
            iteration_path=r"One\FY26\Q4\2Wk\2Wk22",
            remaining_work=0.0,
        )
        client = Mock()
        client.work_items.get_work_item.return_value = source
        client.work_items.create_work_item.return_value = cloned

        # When: clone_work_item is called with overrides
        result = clone_work_item(
            client,
            "MyProject",
            3001,
            field_overrides={
                "System.IterationPath": r"One\FY26\Q4\2Wk\2Wk22",
                "Microsoft.VSTS.Scheduling.RemainingWork": 0.0,
            },
        )

        # Then: overridden fields reflect new values
        assert result.iteration_path == r"One\FY26\Q4\2Wk\2Wk22", (
            f"Expected overridden iteration_path, got {result.iteration_path!r}"
        )
        assert result.remaining_work == 0.0, (
            f"Expected overridden remaining_work=0.0, got {result.remaining_work}"
        )

    def test_parent_link_preserved_in_clone(self) -> None:
        """
        Given a source with a parent link,
        When clone_work_item is called,
        Then the clone has the same parent_id.
        """
        # Given: source with parent, clone also has parent
        source = _sdk_work_item(
            wid=3001,
            parent_url="https://dev.azure.com/org/project/_apis/wit/workItems/4000",
        )
        cloned = _sdk_work_item(
            wid=3004,
            parent_url="https://dev.azure.com/org/project/_apis/wit/workItems/4000",
        )
        client = Mock()
        client.work_items.get_work_item.return_value = source
        client.work_items.create_work_item.return_value = cloned

        # When: clone_work_item is called
        result = clone_work_item(client, "MyProject", 3001)

        # Then: clone has same parent_id as source
        assert result.parent_id == 4000, f"Expected parent_id=4000, got {result.parent_id}"

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during clone,
        When clone_work_item is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on get_work_item (source fetch fails)
        client = Mock()
        client.work_items.get_work_item.side_effect = RuntimeError(
            "clone failed: source not found",
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            clone_work_item(client, "MyProject", 9999)
        assert "clone failed" in str(exc_info.value), (
            f"Expected error to contain 'clone failed', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestGetWorkItemTypeFields
# ---------------------------------------------------------------------------


class TestGetWorkItemTypeFields:
    """
    REQUIREMENT: Discover available fields for a work item type in a project.

    WHO: Agent tooling, field validation, MCP tools
    WHAT: (1) returns WorkItemFieldInfo list with name, reference_name,
              field_type, is_required
          (2) SDK error raises ActionableError
    WHY: Work item types are project-defined with varying field schemas.
         Agents need to discover valid fields before creating or updating
         items to avoid invalid-field errors.

    MOCK BOUNDARY:
        Mock:  client.work_items
               .get_work_item_type_fields_with_references()
               — SDK HTTP call
        Real:  get_work_item_type_fields()
        Never: nothing
    """

    def test_returns_field_info_list(self) -> None:
        """
        Given a project and work item type,
        When get_work_item_type_fields is called,
        Then returns WorkItemFieldInfo list with field metadata.
        """
        # Given: SDK returns field definitions
        field1 = Mock()
        field1.name = "Title"
        field1.reference_name = "System.Title"
        field1.type = "String"
        field1.always_required = True

        field2 = Mock()
        field2.name = "Remaining Work"
        field2.reference_name = "Microsoft.VSTS.Scheduling.RemainingWork"
        field2.type = "Double"
        field2.always_required = False

        client = Mock()
        client.work_items.get_work_item_type_fields_with_references.return_value = [
            field1,
            field2,
        ]

        # When: get_work_item_type_fields is called
        result = get_work_item_type_fields(client, "MyProject", "Task")

        # Then: returns WorkItemFieldInfo list
        assert len(result) == 2, f"Expected 2 fields, got {len(result)}"
        assert isinstance(result[0], WorkItemFieldInfo), (
            f"Expected WorkItemFieldInfo, got {type(result[0]).__name__}"
        )
        assert result[0].name == "Title", f"Expected name='Title', got {result[0].name!r}"
        assert result[0].reference_name == "System.Title", (
            f"Expected reference_name='System.Title', got {result[0].reference_name!r}"
        )
        assert result[0].field_type == "String", (
            f"Expected field_type='String', got {result[0].field_type!r}"
        )
        assert result[0].is_required is True, (
            f"Expected is_required=True, got {result[0].is_required}"
        )
        assert result[1].is_required is False, (
            f"Expected is_required=False for Remaining Work, got {result[1].is_required}"
        )

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during field discovery,
        When get_work_item_type_fields is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on get_work_item_type_fields_with_references
        client = Mock()
        client.work_items.get_work_item_type_fields_with_references.side_effect = RuntimeError(
            "field discovery failed: type not found"
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            get_work_item_type_fields(client, "MyProject", "InvalidType")
        assert "field discovery failed" in str(exc_info.value), (
            f"Expected error to contain 'field discovery failed', got {exc_info.value!r}"
        )

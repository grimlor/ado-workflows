"""
BDD tests for ado_workflows work item fetch-by-ID — FR6a read operations.

Covers:
- TestGetWorkItem: fetch a single work item by ID
- TestGetWorkItems: batch-fetch multiple work items by ID list

Public API surface:
    From src/ado_workflows/listing.py:
        get_work_item(
            client: AdoClient, project: str, work_item_id: int,
        ) -> WorkItemDetail

        get_work_items(
            client: AdoClient, project: str, work_item_ids: list[int],
        ) -> list[WorkItemDetail]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError
from azure.devops.v7_1.work_item_tracking.models import WorkItem, WorkItemRelation

from ado_workflows.listing import get_work_item, get_work_items
from ado_workflows.models import WorkItemDetail

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
    single_item: WorkItem | None = None,
    batch_items: list[WorkItem] | None = None,
    single_error: Exception | None = None,
    batch_error: Exception | None = None,
) -> Mock:
    """Build a mock AdoClient with work_items methods configured."""
    client = Mock()

    if single_error:
        client.work_items.get_work_item.side_effect = single_error
    else:
        client.work_items.get_work_item.return_value = single_item

    if batch_error:
        client.work_items.get_work_items.side_effect = batch_error
    elif batch_items is not None:
        client.work_items.get_work_items.return_value = batch_items
    else:
        client.work_items.get_work_items.return_value = []

    return client


# ---------------------------------------------------------------------------
# TestGetWorkItem — single fetch
# ---------------------------------------------------------------------------


class TestGetWorkItem:
    """
    REQUIREMENT: Fetch a single work item by ID with full field data.

    WHO: MCP tools, sprint automation, clone_work_item (FR6a mutation)
    WHAT: (1) returns WorkItemDetail with all fields, area_path,
              parent_id, and full fields dict
          (2) work item with no parent returns parent_id=None
          (3) SDK error raises ActionableError with original message
    WHY: Direct ID lookup avoids WIQL overhead for single-item operations
         and is a prerequisite for clone_work_item and sprint inspection.

    MOCK BOUNDARY:
        Mock:  client.work_items.get_work_item() — SDK HTTP call
        Real:  get_work_item()
        Never: nothing
    """

    def test_returns_work_item_detail_with_all_fields(self) -> None:
        """
        Given a valid work item ID,
        When get_work_item is called,
        Then returns WorkItemDetail with title, state, work_item_type,
        assigned_to, area_path, iteration_path, completed_work,
        remaining_work, parent_id, url, and full fields dict.
        """
        # Given: SDK returns a work item with all fields and parent relation
        wi = _sdk_work_item(
            wid=1001,
            title="Build query module",
            state="Active",
            work_item_type="Task",
            assigned_to="Alice Smith",
            area_path=r"One\CFS\PayFin and Data Platform Redmond",
            iteration_path=r"One\FY26\Q4\2Wk\2Wk21",
            completed_work=4.0,
            remaining_work=8.0,
            parent_url=_PARENT_RELATION_URL,
        )
        client = _mock_client(single_item=wi)

        # When: get_work_item is called
        result = get_work_item(client, "MyProject", 1001)

        # Then: returns WorkItemDetail with all expected fields
        assert isinstance(result, WorkItemDetail), (
            f"Expected WorkItemDetail, got {type(result).__name__}"
        )
        assert result.id == 1001, f"Expected id=1001, got {result.id}"
        assert result.title == "Build query module", (
            f"Expected title='Build query module', got {result.title!r}"
        )
        assert result.state == "Active", f"Expected state='Active', got {result.state!r}"
        assert result.work_item_type == "Task", (
            f"Expected work_item_type='Task', got {result.work_item_type!r}"
        )
        assert result.assigned_to == "Alice Smith", (
            f"Expected assigned_to='Alice Smith', got {result.assigned_to!r}"
        )
        assert result.area_path == r"One\CFS\PayFin and Data Platform Redmond", (
            f"Expected area_path, got {result.area_path!r}"
        )
        assert result.iteration_path == r"One\FY26\Q4\2Wk\2Wk21", (
            f"Expected iteration_path, got {result.iteration_path!r}"
        )
        assert result.completed_work == 4.0, (
            f"Expected completed_work=4.0, got {result.completed_work}"
        )
        assert result.remaining_work == 8.0, (
            f"Expected remaining_work=8.0, got {result.remaining_work}"
        )
        assert result.parent_id == 5000, f"Expected parent_id=5000, got {result.parent_id}"
        assert result.url is not None, "Expected url to be set"
        assert "System.Title" in result.fields, "Expected full fields dict to contain System.Title"

    def test_no_parent_returns_none_parent_id(self) -> None:
        """
        Given a work item with no parent relation,
        When get_work_item is called,
        Then returns WorkItemDetail with parent_id=None.
        """
        # Given: work item without parent relation
        wi = _sdk_work_item(
            wid=2001,
            parent_url=None,
        )
        client = _mock_client(single_item=wi)

        # When: get_work_item is called
        result = get_work_item(client, "MyProject", 2001)

        # Then: parent_id is None
        assert result.parent_id is None, f"Expected parent_id=None, got {result.parent_id}"

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error during get_work_item,
        When get_work_item is called,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on get_work_item
        client = _mock_client(single_error=RuntimeError("item not found"))

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            get_work_item(client, "MyProject", 9999)
        assert "item not found" in str(exc_info.value), (
            f"Expected error to contain 'item not found', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestGetWorkItems — batch fetch
# ---------------------------------------------------------------------------


class TestGetWorkItems:
    """
    REQUIREMENT: Batch-fetch multiple work items by ID with full field data.

    WHO: Sprint rollover, bulk inspection, MCP tools
    WHAT: (1) returns WorkItemDetail list for given IDs
          (2) empty ID list returns empty list
          (3) large ID lists return all results
          (4) SDK error raises ActionableError with original message
    WHY: Batch fetch by known IDs avoids WIQL construction overhead and
         supports sprint rollover and bulk inspection workflows.

    MOCK BOUNDARY:
        Mock:  client.work_items.get_work_items() — SDK HTTP call
        Real:  get_work_items()
        Never: nothing
    """

    def test_returns_work_item_details_for_given_ids(self) -> None:
        """
        Given a list of work item IDs,
        When get_work_items is called,
        Then returns WorkItemDetail list with all fields populated.
        """
        # Given: two work items
        wi1 = _sdk_work_item(wid=101, title="First task", state="Active")
        wi2 = _sdk_work_item(wid=102, title="Second task", state="Closed")
        client = _mock_client(batch_items=[wi1, wi2])

        # When: get_work_items is called
        result = get_work_items(client, "MyProject", [101, 102])

        # Then: returns 2 WorkItemDetail objects with correct fields
        assert len(result) == 2, f"Expected 2 results, got {len(result)}"
        assert isinstance(result[0], WorkItemDetail), (
            f"Expected WorkItemDetail, got {type(result[0]).__name__}"
        )
        assert result[0].id == 101, f"Expected first id=101, got {result[0].id}"
        assert result[0].title == "First task", (
            f"Expected title='First task', got {result[0].title!r}"
        )
        assert result[1].id == 102, f"Expected second id=102, got {result[1].id}"
        assert result[1].state == "Closed", f"Expected state='Closed', got {result[1].state!r}"

    def test_empty_id_list_returns_empty_list(self) -> None:
        """
        Given an empty list of work item IDs,
        When get_work_items is called,
        Then returns an empty list.
        """
        # Given: empty ID list
        client = _mock_client()

        # When: get_work_items is called with empty list
        result = get_work_items(client, "MyProject", [])

        # Then: empty list returned
        assert result == [], f"Expected empty list, got {result}"

    def test_large_id_list_returns_all_results(self) -> None:
        """
        Given more than 200 work item IDs,
        When get_work_items is called,
        Then returns all items.
        """
        # Given: 250 IDs with corresponding work items
        ids = list(range(1, 251))
        all_items = [_sdk_work_item(wid=i, title=f"Item {i}") for i in ids]
        batch_1 = all_items[:200]
        batch_2 = all_items[200:]

        client = Mock()
        client.work_items.get_work_items.side_effect = [batch_1, batch_2]

        # When: get_work_items is called
        result = get_work_items(client, "MyProject", ids)

        # Then: all 250 results are returned
        assert len(result) == 250, f"Expected 250 results, got {len(result)}"
        assert result[0].id == 1, f"Expected first item id=1, got {result[0].id}"
        assert result[249].id == 250, f"Expected last item id=250, got {result[249].id}"

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given work item IDs,
        When get_work_items SDK call raises an error,
        Then raises ActionableError with the original message.
        """
        # Given: SDK raises on get_work_items
        client = _mock_client(batch_error=RuntimeError("batch fetch failed"))

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            get_work_items(client, "MyProject", [1001, 1002])
        assert "batch fetch failed" in str(exc_info.value), (
            f"Expected error to contain 'batch fetch failed', got {exc_info.value!r}"
        )

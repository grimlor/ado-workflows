"""
BDD tests for ado_workflows work item querying — FR3a B2.

Covers:
- TestQueryWorkItems: execute WIQL and return enriched work item data

Public API surface (new in FR3a):
    From src/ado_workflows/listing.py:
        query_work_items(
            client: AdoClient, project: str, wiql: str, *,
            top: int | None,
        ) -> list[WorkItemSummary]

    From src/ado_workflows/models.py:
        WorkItemSummary(id, title, state, work_item_type, assigned_to,
            iteration_path, completed_work, remaining_work, url)
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.listing import query_work_items
from ado_workflows.models import WorkItemSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wiql_result(ids: list[int]) -> Mock:
    """Build a mock WorkItemQueryResult with the given IDs."""
    result = Mock()
    result.work_items = [Mock(id=wid) for wid in ids]
    return result


def _sdk_work_item(
    *,
    wid: int = 1001,
    title: str = "Implement data gathering",
    state: str = "Active",
    work_item_type: str = "Task",
    assigned_to: str = "Alice Smith",
    iteration_path: str = r"One\FY26\Q3\2Wk\2Wk05",
    completed_work: float | None = 4.0,
    remaining_work: float | None = 8.0,
    url: str = "https://dev.azure.com/org/project/_apis/wit/workItems/1001",
) -> Mock:
    """Build a mock SDK WorkItem with standard fields."""
    wi = Mock()
    wi.id = wid
    wi.url = url
    wi.fields = {
        "System.Title": title,
        "System.State": state,
        "System.WorkItemType": work_item_type,
        "System.AssignedTo": assigned_to,
        "System.IterationPath": iteration_path,
        "Microsoft.VSTS.Scheduling.CompletedWork": completed_work,
        "Microsoft.VSTS.Scheduling.RemainingWork": remaining_work,
    }
    return wi


def _mock_client(
    wiql_result: Mock | None = None,
    work_items: list[Mock] | None = None,
    *,
    wiql_error: Exception | None = None,
    get_error: Exception | None = None,
) -> Mock:
    """Build a mock AdoClient with work_items methods configured."""
    client = Mock()
    if wiql_error:
        client.work_items.query_by_wiql.side_effect = wiql_error
    else:
        client.work_items.query_by_wiql.return_value = wiql_result or _wiql_result([])

    if get_error:
        client.work_items.get_work_items.side_effect = get_error
    else:
        client.work_items.get_work_items.return_value = work_items or []
    return client


SAMPLE_WIQL = (
    "SELECT [System.Id] FROM WorkItems "
    "WHERE [System.AssignedTo] = @Me "
    "AND [System.IterationPath] UNDER 'One\\FY26\\Q3\\2Wk\\2Wk05'"
)


# ---------------------------------------------------------------------------
# TestQueryWorkItems — B2
# ---------------------------------------------------------------------------


class TestQueryWorkItems:
    """
    REQUIREMENT: Execute a WIQL query and return enriched work item data.

    WHO: Reporting tools, sprint dashboards, automation scripts
    WHAT: (1) executes WIQL and returns WorkItemSummary list with all fields
          (2) empty query result returns empty list
          (3) results >200 are batch-fetched in chunks of 200
          (4) missing optional fields (completed_work, remaining_work) return None
          (5) SDK errors raise ActionableError
    WHY: Enables querying work items with effort tracking for reporting,
         sprint analysis, and workflow automation.

    MOCK BOUNDARY:
        Mock:  client.work_items.query_by_wiql(), client.work_items.get_work_items()
        Real:  query_work_items(), model mapping, batching logic
        Never: nothing
    """

    def test_valid_wiql_returns_work_item_summary_with_all_fields(self) -> None:
        """
        Given a valid WIQL query,
        When called,
        Then returns WorkItemSummary list with title, state, work_item_type,
        assigned_to, iteration_path, completed_work, remaining_work, url.
        """
        # Given: WIQL returns 1 ID, get_work_items returns the full item
        wiql_result = _wiql_result([1001])
        wi = _sdk_work_item(wid=1001, title="Build query module", state="Active")
        client = _mock_client(wiql_result, [wi])

        # When: query_work_items is called
        result = query_work_items(client, "MyProject", SAMPLE_WIQL)

        # Then: returns WorkItemSummary with all expected fields
        assert len(result) == 1, f"Expected 1 WorkItemSummary, got {len(result)}"
        item = result[0]
        assert isinstance(item, WorkItemSummary), (
            f"Expected WorkItemSummary, got {type(item).__name__}"
        )
        assert item.id == 1001, f"Expected id=1001, got {item.id}"
        assert item.title == "Build query module", (
            f"Expected title='Build query module', got {item.title!r}"
        )
        assert item.state == "Active", f"Expected state='Active', got {item.state!r}"
        assert item.work_item_type == "Task", (
            f"Expected work_item_type='Task', got {item.work_item_type!r}"
        )
        assert item.assigned_to == "Alice Smith", (
            f"Expected assigned_to='Alice Smith', got {item.assigned_to!r}"
        )
        assert item.iteration_path == r"One\FY26\Q3\2Wk\2Wk05", (
            f"Expected iteration_path, got {item.iteration_path!r}"
        )
        assert item.completed_work == 4.0, (
            f"Expected completed_work=4.0, got {item.completed_work}"
        )
        assert item.remaining_work == 8.0, (
            f"Expected remaining_work=8.0, got {item.remaining_work}"
        )
        assert item.url is not None, "Expected url to be set"

    def test_empty_wiql_result_returns_empty_list(self) -> None:
        """
        Given a WIQL query returning zero results,
        When called,
        Then returns empty list.
        """
        # Given: WIQL returns no IDs
        client = _mock_client(_wiql_result([]), [])

        # When: query_work_items is called
        result = query_work_items(client, "MyProject", SAMPLE_WIQL)

        # Then: empty list returned, get_work_items not called
        assert result == [], f"Expected empty list, got {result}"

    def test_large_result_batches_in_chunks_of_200(self) -> None:
        """
        Given a WIQL query returning >200 results,
        When called,
        Then batches get_work_items() calls in chunks of 200.
        """
        # Given: WIQL returns 250 IDs
        ids = list(range(1, 251))
        wiql_result = _wiql_result(ids)

        # Build 250 mock work items
        all_items = [_sdk_work_item(wid=i, title=f"Item {i}") for i in ids]
        batch_1 = all_items[:200]
        batch_2 = all_items[200:]

        client = Mock()
        client.work_items.query_by_wiql.return_value = wiql_result
        client.work_items.get_work_items.side_effect = [batch_1, batch_2]

        # When: query_work_items is called
        result = query_work_items(client, "MyProject", SAMPLE_WIQL)

        # Then: all 250 results are returned
        assert len(result) == 250, f"Expected 250 results, got {len(result)}"
        # Verify first and last items to confirm all batches were processed
        assert result[0].id == 1, f"Expected first item id=1, got {result[0].id}"
        assert result[249].id == 250, f"Expected last item id=250, got {result[249].id}"

    def test_missing_optional_fields_return_none(self) -> None:
        """
        Given work items with missing optional fields (completed_work, remaining_work),
        When called,
        Then returns None for those fields.
        """
        # Given: work item without effort tracking fields
        wiql_result = _wiql_result([2001])
        wi = _sdk_work_item(
            wid=2001,
            completed_work=None,
            remaining_work=None,
        )
        client = _mock_client(wiql_result, [wi])

        # When: query_work_items is called
        result = query_work_items(client, "MyProject", SAMPLE_WIQL)

        # Then: optional fields are None
        assert len(result) == 1, f"Expected 1 result, got {len(result)}"
        assert result[0].completed_work is None, (
            f"Expected completed_work=None, got {result[0].completed_work}"
        )
        assert result[0].remaining_work is None, (
            f"Expected remaining_work=None, got {result[0].remaining_work}"
        )

    def test_sdk_error_on_wiql_raises_actionable_error(self) -> None:
        """
        Given an SDK error during WIQL query,
        When called,
        Then raises ActionableError.
        """
        # Given: SDK raises on query_by_wiql
        client = _mock_client(wiql_error=RuntimeError("service unavailable"))

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            query_work_items(client, "MyProject", SAMPLE_WIQL)
        assert "service unavailable" in str(exc_info.value), (
            f"Expected error to contain 'service unavailable', got {exc_info.value!r}"
        )

    def test_sdk_error_on_get_work_items_raises_actionable_error(self) -> None:
        """
        Given work item IDs returned by WIQL,
        When get_work_items raises an SDK error during batch fetch,
        Then raises ActionableError.
        """
        # Given: WIQL returns IDs, but get_work_items fails
        client = _mock_client(
            wiql_result=_wiql_result([1001]),
            get_error=RuntimeError("batch fetch failed"),
        )

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            query_work_items(client, "MyProject", SAMPLE_WIQL)
        assert "batch fetch failed" in str(exc_info.value), (
            f"Expected error to contain 'batch fetch failed', got {exc_info.value!r}"
        )

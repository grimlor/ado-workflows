"""
BDD tests for ado_workflows.iterations -- PR iteration tracking and change context.

Covers:
- TestGetPrIterations: fetch all iterations for a PR
- TestGetIterationChanges: fetch per-file changes for a specific iteration
- TestGetLatestIterationContext: convenience for latest iteration + file map

Public API surface (from src/ado_workflows/iterations.py):
    get_pr_iterations(client: AdoClient, repository: str, pr_id: int,
                      project: str) -> list[IterationInfo]
    get_iteration_changes(client: AdoClient, repository: str, pr_id: int,
                          iteration_id: int, project: str) -> list[FileChange]
    get_latest_iteration_context(client: AdoClient, repository: str,
                                 pr_id: int, project: str) -> IterationContext
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.iterations import (
    get_iteration_changes,
    get_latest_iteration_context,
    get_pr_iterations,
)
from ado_workflows.models import (
    FileChange,
    IterationContext,
    IterationInfo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(
    iterations: list[Mock] | None = None,
    changes: Mock | None = None,
) -> Mock:
    """Return a mock AdoClient with git iteration/change methods."""
    client = Mock()
    client.git.get_pull_request_iterations.return_value = iterations or []
    if changes is not None:
        client.git.get_pull_request_iteration_changes.return_value = changes
    return client


def _make_iteration(*, iteration_id: int = 1, description: str | None = None) -> Mock:
    """Build a mock GitPullRequestIteration."""
    it = Mock()
    it.id = iteration_id
    it.created_date = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
    it.description = description
    return it


def _make_changes_response(entries: list[dict[str, object]]) -> Mock:
    """Build a mock GitPullRequestIterationChanges with change_entries."""
    response = Mock()
    change_entries: list[Mock] = []
    for entry in entries:
        change = Mock()
        change.change_tracking_id = entry.get("change_tracking_id", 1)
        change.additional_properties = {
            "item": {"path": entry.get("path", "/unknown")},
            "changeType": entry.get("change_type", "edit"),
        }
        change_entries.append(change)
    response.change_entries = change_entries
    return response


# ---------------------------------------------------------------------------
# TestGetPrIterations
# ---------------------------------------------------------------------------


class TestGetPrIterations:
    """
    REQUIREMENT: Retrieve all iterations for a PR.

    WHO: Comment posting logic and any consumer needing iteration metadata.
    WHAT: (1) a PR with iterations returns a list of IterationInfo with correct ids and descriptions
          (2) a PR with no iterations returns an empty list
          (3) an SDK error raises ActionableError with the original error context and potential corrective actions for the user
    WHY: Iteration metadata is required to resolve changeTrackingId for
         line-positioned comment posting.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_iterations (SDK network call)
        Real:  get_pr_iterations mapping logic
        Never: get_pr_iterations itself
    """

    def test_given_pr_with_iterations_when_called_then_returns_iteration_info_list(
        self,
    ) -> None:
        """
        Given a PR with 3 iterations,
        When get_pr_iterations is called,
        Then it returns 3 IterationInfo objects with correct IDs.
        """
        # Given: a PR with 3 iterations
        iters = [
            _make_iteration(iteration_id=1, description="Initial push"),
            _make_iteration(iteration_id=2, description="Address feedback"),
            _make_iteration(iteration_id=3),
        ]
        client = _mock_client(iterations=iters)

        # When: get_pr_iterations is called
        result = get_pr_iterations(client, "MyRepo", 42, "MyProject")

        # Then: it returns 3 IterationInfo objects with correct IDs
        assert len(result) == 3, f"Expected 3 iterations, got {len(result)}"
        assert all(isinstance(r, IterationInfo) for r in result), (
            f"Expected all IterationInfo, got types: {[type(r).__name__ for r in result]}"
        )
        assert result[0].id == 1, f"Expected id=1, got {result[0].id}"
        assert result[2].id == 3, f"Expected id=3, got {result[2].id}"
        assert result[0].description == "Initial push", (
            f"Expected 'Initial push', got {result[0].description!r}"
        )

    def test_given_pr_with_no_iterations_when_called_then_returns_empty_list(
        self,
    ) -> None:
        """
        Given a PR with no iterations,
        When get_pr_iterations is called,
        Then it returns an empty list.
        """
        # Given: a PR with no iterations
        client = _mock_client(iterations=[])

        # When: get_pr_iterations is called
        result = get_pr_iterations(client, "MyRepo", 99, "MyProject")

        # Then: it returns an empty list
        assert result == [], f"Expected empty list, got {result!r}"

    def test_given_sdk_error_when_called_then_raises_actionable_error_with_guidance(
        self,
    ) -> None:
        """
        Given the SDK raises an exception,
        When get_pr_iterations is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: the SDK raises an exception
        client = Mock()
        client.git.get_pull_request_iterations.side_effect = Exception("Network timeout")

        # When / Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            get_pr_iterations(client, "MyRepo", 42, "MyProject")

        assert "Network timeout" in str(exc_info.value), (
            f"Expected original error context, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "connection", (
            f"Expected error_type='connection', got {exc_info.value.error_type!r}"
        )


# ---------------------------------------------------------------------------
# TestGetIterationChanges
# ---------------------------------------------------------------------------


class TestGetIterationChanges:
    """
    REQUIREMENT: Retrieve per-file changes for a specific PR iteration.

    WHO: Iteration context resolution and comment positioning logic.
    WHAT: (1) an iteration with file changes returns FileChange objects with paths (leading slash stripped), change types, and tracking IDs
          (2) an iteration with no changes returns an empty list
          (3) an SDK error raises ActionableError with the original error context and potential corrective actions for the user
          (4) a change entry with no item path is silently skipped
    WHY: Each file's changeTrackingId is required for anchoring comments
         to the correct iteration in the PR diff.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_iteration_changes (SDK network call)
        Real:  get_iteration_changes extraction logic
        Never: get_iteration_changes itself
    """

    def test_given_iteration_with_changes_when_called_then_returns_file_changes(
        self,
    ) -> None:
        """
        Given an iteration with 2 file changes,
        When get_iteration_changes is called,
        Then it returns 2 FileChange objects with correct paths and tracking IDs.
        """
        # Given: an iteration with 2 file changes
        changes_resp = _make_changes_response(
            [
                {"path": "/src/foo.py", "change_type": "edit", "change_tracking_id": 7},
                {"path": "/src/bar.py", "change_type": "add", "change_tracking_id": 12},
            ]
        )
        client = _mock_client(changes=changes_resp)

        # When: get_iteration_changes is called
        result = get_iteration_changes(client, "MyRepo", 42, 2, "MyProject")

        # Then: it returns 2 FileChange objects with correct paths and tracking IDs
        assert len(result) == 2, f"Expected 2 changes, got {len(result)}"
        assert all(isinstance(r, FileChange) for r in result), (
            f"Expected all FileChange, got types: {[type(r).__name__ for r in result]}"
        )
        assert result[0].path == "src/foo.py", (
            f"Expected 'src/foo.py' (no leading slash), got {result[0].path!r}"
        )
        assert result[0].change_tracking_id == 7, (
            f"Expected change_tracking_id=7, got {result[0].change_tracking_id}"
        )
        assert result[1].change_type == "add", (
            f"Expected change_type='add', got {result[1].change_type!r}"
        )

    def test_given_empty_changes_when_called_then_returns_empty_list(
        self,
    ) -> None:
        """
        Given an iteration with no file changes,
        When get_iteration_changes is called,
        Then it returns an empty list.
        """
        # Given: an iteration with no file changes
        changes_resp = _make_changes_response([])
        client = _mock_client(changes=changes_resp)

        # When: get_iteration_changes is called
        result = get_iteration_changes(client, "MyRepo", 42, 1, "MyProject")

        # Then: it returns an empty list
        assert result == [], f"Expected empty list, got {result!r}"

    def test_given_sdk_error_when_called_then_raises_actionable_error_with_guidance(
        self,
    ) -> None:
        """
        Given the SDK raises an exception,
        When get_iteration_changes is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: the SDK raises an exception
        client = Mock()
        client.git.get_pull_request_iteration_changes.side_effect = Exception("404 Not Found")

        # When / Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            get_iteration_changes(client, "MyRepo", 42, 1, "MyProject")

        assert "404" in str(exc_info.value), (
            f"Expected original error context, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "connection", (
            f"Expected error_type='connection', got {exc_info.value.error_type!r}"
        )

    def test_given_change_entry_with_no_path_when_called_then_entry_is_skipped(
        self,
    ) -> None:
        """
        Given an iteration with a change entry that has no item path,
        When get_iteration_changes is called,
        Then that entry is skipped and only entries with paths are returned.
        """
        # Given: one entry with a path, one with empty path
        response = Mock()
        good_entry = Mock()
        good_entry.change_tracking_id = 5
        good_entry.additional_properties = {
            "item": {"path": "/src/good.py"},
            "changeType": "edit",
        }
        bad_entry = Mock()
        bad_entry.change_tracking_id = 6
        bad_entry.additional_properties = {
            "item": {},
            "changeType": "edit",
        }
        response.change_entries = [good_entry, bad_entry]
        client = _mock_client(changes=response)

        # When: get_iteration_changes is called
        result = get_iteration_changes(client, "MyRepo", 42, 1, "MyProject")

        # Then: only the entry with a path is returned
        assert len(result) == 1, f"Expected 1 change (bad entry skipped), got {len(result)}"
        assert result[0].path == "src/good.py", f"Expected 'src/good.py', got {result[0].path!r}"


# ---------------------------------------------------------------------------
# TestGetLatestIterationContext
# ---------------------------------------------------------------------------


class TestGetLatestIterationContext:
    """
    REQUIREMENT: Resolve the latest iteration and per-file change tracking
                 metadata for a PR.

    WHO: Comment posting logic that needs to anchor to the correct iteration.
    WHAT: (1) a PR with iterations returns IterationContext with the latest iteration ID and a file-path-keyed dict of FileChange objects
          (2) a PR with no iterations raises ActionableError with potential corrective actions for the user
          (3) renamed files appear under their respective paths in the map
          (4) the changes request uses the latest iteration ID, not an earlier one
    WHY: Without changeTrackingId per file, comments anchor to iteration 1
         regardless of which iteration is current.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_iterations,
               client.git.get_pull_request_iteration_changes (SDK network calls)
        Real:  get_latest_iteration_context composition logic
        Never: get_latest_iteration_context itself
    """

    def test_given_pr_with_iterations_when_called_then_returns_latest_context(
        self,
    ) -> None:
        """
        Given a PR with 3 iterations where iteration 3 has 2 file changes,
        When get_latest_iteration_context is called,
        Then it returns IterationContext with iteration_id=3 and 2 file entries.
        """
        # Given: a PR with 3 iterations where iteration 3 has 2 file changes
        iters = [
            _make_iteration(iteration_id=1),
            _make_iteration(iteration_id=2),
            _make_iteration(iteration_id=3),
        ]
        changes_resp = _make_changes_response(
            [
                {"path": "/src/a.py", "change_type": "edit", "change_tracking_id": 5},
                {"path": "/src/b.py", "change_type": "add", "change_tracking_id": 9},
            ]
        )
        client = _mock_client(iterations=iters, changes=changes_resp)

        # When: get_latest_iteration_context is called
        result = get_latest_iteration_context(client, "MyRepo", 42, "MyProject")

        # Then: it returns IterationContext with iteration_id=3 and 2 file entries
        assert isinstance(result, IterationContext), (
            f"Expected IterationContext, got {type(result).__name__}"
        )
        assert result.iteration_id == 3, f"Expected iteration_id=3, got {result.iteration_id}"
        assert len(result.file_changes) == 2, (
            f"Expected 2 file entries, got {len(result.file_changes)}"
        )
        assert "src/a.py" in result.file_changes, (
            f"Expected 'src/a.py' in file_changes keys, got {list(result.file_changes.keys())}"
        )
        assert result.file_changes["src/a.py"].change_tracking_id == 5, (
            f"Expected change_tracking_id=5, got {result.file_changes['src/a.py'].change_tracking_id}"
        )

    def test_given_pr_with_no_iterations_when_called_then_raises_actionable_error_with_guidance(
        self,
    ) -> None:
        """
        Given a PR with no iterations,
        When get_latest_iteration_context is called,
        Then ActionableError is raised with potential corrective actions for the user.
        """
        # Given: a PR with no iterations
        client = _mock_client(iterations=[])

        # When / Then: ActionableError is raised with corrective actions
        with pytest.raises(ActionableError) as exc_info:
            get_latest_iteration_context(client, "MyRepo", 42, "MyProject")

        assert "iteration" in str(exc_info.value).lower(), (
            f"Expected error to mention iterations, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "validation", (
            f"Expected error_type='validation', got {exc_info.value.error_type!r}"
        )

    def test_given_changes_include_renamed_files_when_called_then_both_paths_in_map(
        self,
    ) -> None:
        """
        Given iteration changes include a file with a rename,
        When get_latest_iteration_context is called,
        Then the file change is keyed by its path in the map.
        """
        # Given: iteration changes include files with different paths
        iters = [_make_iteration(iteration_id=2)]
        changes_resp = _make_changes_response(
            [
                {"path": "/src/old_name.py", "change_type": "rename", "change_tracking_id": 3},
                {"path": "/src/new_name.py", "change_type": "edit", "change_tracking_id": 4},
            ]
        )
        client = _mock_client(iterations=iters, changes=changes_resp)

        # When: get_latest_iteration_context is called
        result = get_latest_iteration_context(client, "MyRepo", 42, "MyProject")

        # Then: both paths are in the map
        assert "src/old_name.py" in result.file_changes, (
            f"Expected 'src/old_name.py' in keys, got {list(result.file_changes.keys())}"
        )
        assert "src/new_name.py" in result.file_changes, (
            f"Expected 'src/new_name.py' in keys, got {list(result.file_changes.keys())}"
        )

    def test_given_iteration_fetched_when_changes_requested_then_uses_latest_id(
        self,
    ) -> None:
        """
        Given a PR with iterations [1, 2, 3],
        When get_latest_iteration_context is called,
        Then get_pull_request_iteration_changes is called with iteration_id=3.
        """
        # Given: a PR with iterations [1, 2, 3]
        iters = [
            _make_iteration(iteration_id=1),
            _make_iteration(iteration_id=2),
            _make_iteration(iteration_id=3),
        ]
        changes_resp = _make_changes_response([])
        client = _mock_client(iterations=iters, changes=changes_resp)

        # When: get_latest_iteration_context is called
        get_latest_iteration_context(client, "MyRepo", 42, "MyProject")

        # Then: get_pull_request_iteration_changes was called with iteration_id=3
        call_args = client.git.get_pull_request_iteration_changes.call_args
        assert call_args is not None, "Expected iteration changes to be called"
        # The iteration_id should be 3 (the latest)
        assert call_args[0][2] == 3 or call_args[1].get("iteration_id") == 3, (
            f"Expected iteration_id=3 in call args, got positional={call_args[0]}, kwargs={call_args[1]}"
        )

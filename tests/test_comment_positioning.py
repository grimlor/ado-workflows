"""BDD tests for ado_workflows.comments -- positioned comment posting.

Extends the existing test_comments.py with new positioning behaviors.
This file covers only the new post_comment positioning and post_comments
batch behaviors; existing comment tests remain in test_comments.py.

Covers:
- TestPostCommentWithPosition: line-positioned comment posting with iteration context
- TestBatchPostComments: batch posting with partial-success semantics

Public API surface (extended from src/ado_workflows/comments.py):
    post_comment(client: AdoClient, repository: str, pr_id: int,
                 content: str, project: str, *, status: str = "active",
                 file_path: str | None = None, line_number: int | None = None,
                 iteration_context: IterationContext | None = None) -> int
    post_comments(client: AdoClient, repository: str, pr_id: int,
                  comments: list[CommentPayload], project: str, *,
                  dry_run: bool = False) -> PostingResult
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.comments import (
    post_comment,
    post_comments,
)
from ado_workflows.models import (
    CommentPayload,
    FileChange,
    IterationContext,
    PostingResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(
    *,
    thread_id: int = 100,
    iterations: list[Mock] | None = None,
    changes: Mock | None = None,
) -> Mock:
    """Return a mock AdoClient with git thread/iteration/change methods."""
    client = Mock()

    # create_thread returns a mock with .id
    thread_response = Mock()
    thread_response.id = thread_id
    client.git.create_thread.return_value = thread_response

    # iteration support for auto-resolve path
    client.git.get_pull_request_iterations.return_value = iterations or []
    if changes is not None:
        client.git.get_pull_request_iteration_changes.return_value = changes

    return client


def _make_iteration(*, iteration_id: int = 1) -> Mock:
    """Build a mock GitPullRequestIteration."""
    it = Mock()
    it.id = iteration_id
    it.created_date = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
    it.description = None
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


def _make_iteration_context(
    iteration_id: int = 2,
    files: dict[str, int] | None = None,
) -> IterationContext:
    """Build an IterationContext with file_path -> change_tracking_id mapping."""
    file_changes: dict[str, FileChange] = {}
    for path, tracking_id in (files or {}).items():
        file_changes[path] = FileChange(
            path=path,
            change_type="edit",
            change_tracking_id=tracking_id,
        )
    return IterationContext(iteration_id=iteration_id, file_changes=file_changes)


# ---------------------------------------------------------------------------
# TestPostCommentWithPosition
# ---------------------------------------------------------------------------


class TestPostCommentWithPosition:
    """
    REQUIREMENT: A comment can be posted to a specific file and line in a PR.

    WHO: Any consumer that generates line-specific feedback (code review tools,
         linters, AI agents).
    WHAT: (1) content-only post creates a plain thread with no threadContext
          (2) content + file_path + line_number creates a thread with CommentThreadContext at the given line
          (3) content + file_path + line_number + iteration_context sets pullRequestThreadContext with correct changeTrackingId
          (4) content + file_path + line_number without iteration_context auto-resolves the latest iteration context
          (5) file_path without line_number raises ActionableError with corrective guidance
          (6) line_number without file_path raises ActionableError with corrective guidance
          (7) empty content raises ActionableError with corrective guidance
    WHY: Without iteration-aware positioning, comments anchor to stale
         iterations and show "file no longer exists" warnings.

    MOCK BOUNDARY:
        Mock:  client.git.create_thread, client.git.get_pull_request_iterations,
               client.git.get_pull_request_iteration_changes (SDK network calls)
        Real:  post_comment logic (thread context construction, iteration resolution)
        Never: post_comment itself (it IS the system under test)
    """

    def test_content_only_creates_plain_thread(self) -> None:
        """
        Given only content (no file_path or line_number),
        When post_comment is called,
        Then a plain thread is created with no threadContext.
        """
        # Given: only content
        client = _mock_client(thread_id=50)

        # When: post_comment is called
        thread_id = post_comment(client, "MyRepo", 42, "Good work", "MyProject")

        # Then: a plain thread is created with no threadContext
        assert thread_id == 50, f"Expected thread_id=50, got {thread_id}"
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        assert thread_obj.thread_context is None, (
            f"Expected no threadContext for plain comment, got {thread_obj.thread_context}"
        )

    def test_file_and_line_creates_positioned_thread(self) -> None:
        """
        Given content with file_path and line_number,
        When post_comment is called,
        Then the thread has CommentThreadContext with rightFileStart at the given line.
        """
        # Given: content with file_path and line_number, plus iteration context
        iter_ctx = _make_iteration_context(iteration_id=3, files={"src/main.py": 7})
        client = _mock_client(thread_id=60)

        # When: post_comment is called
        thread_id = post_comment(
            client,
            "MyRepo",
            42,
            "Fix this",
            "MyProject",
            file_path="src/main.py",
            line_number=25,
            iteration_context=iter_ctx,
        )

        # Then: the thread has CommentThreadContext with rightFileStart at line 25
        assert thread_id == 60, f"Expected thread_id=60, got {thread_id}"
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        assert thread_obj.thread_context is not None, (
            "Expected threadContext for positioned comment, got None"
        )
        assert thread_obj.thread_context.right_file_start.line == 25, (
            f"Expected line=25, got {thread_obj.thread_context.right_file_start.line}"
        )
        assert thread_obj.thread_context.file_path == "/src/main.py", (
            f"Expected file_path='/src/main.py', got {thread_obj.thread_context.file_path!r}"
        )

    def test_iteration_context_sets_change_tracking_id(self) -> None:
        """
        Given content + file_path + line_number + explicit iteration_context,
        When post_comment is called,
        Then the thread has pullRequestThreadContext with the file's changeTrackingId and iteration.
        """
        # Given: explicit iteration_context with changeTrackingId=42 for the file
        iter_ctx = _make_iteration_context(iteration_id=5, files={"src/app.py": 42})
        client = _mock_client(thread_id=70)

        # When: post_comment is called
        post_comment(
            client,
            "MyRepo",
            99,
            "Review this",
            "MyProject",
            file_path="src/app.py",
            line_number=10,
            iteration_context=iter_ctx,
        )

        # Then: pullRequestThreadContext has changeTrackingId=42 and iteration=5
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        pr_ctx = thread_obj.pull_request_thread_context
        assert pr_ctx is not None, "Expected pullRequestThreadContext, got None"
        assert pr_ctx.change_tracking_id == 42, (
            f"Expected changeTrackingId=42, got {pr_ctx.change_tracking_id}"
        )
        assert pr_ctx.iteration_context.second_comparing_iteration == 5, (
            f"Expected secondComparingIteration=5, got {pr_ctx.iteration_context.second_comparing_iteration}"
        )

    def test_missing_iteration_context_auto_resolves(self) -> None:
        """
        Given content + file_path + line_number but no iteration_context,
        When post_comment is called,
        Then iteration context is auto-resolved from the PR iterations API.
        """
        # Given: no iteration_context, but SDK returns iteration data
        iters = [_make_iteration(iteration_id=3)]
        changes_resp = _make_changes_response(
            [
                {"path": "/src/lib.py", "change_type": "edit", "change_tracking_id": 15},
            ]
        )
        client = _mock_client(thread_id=80, iterations=iters, changes=changes_resp)

        # When: post_comment is called without iteration_context
        thread_id = post_comment(
            client,
            "MyRepo",
            42,
            "Auto-resolved",
            "MyProject",
            file_path="src/lib.py",
            line_number=5,
        )

        # Then: the comment is posted with auto-resolved iteration context
        assert thread_id == 80, f"Expected thread_id=80, got {thread_id}"
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        pr_ctx = thread_obj.pull_request_thread_context
        assert pr_ctx is not None, "Expected auto-resolved pullRequestThreadContext, got None"
        assert pr_ctx.change_tracking_id == 15, (
            f"Expected auto-resolved changeTrackingId=15, got {pr_ctx.change_tracking_id}"
        )

    def test_file_path_without_line_number_raises_actionable_error_with_guidance(self) -> None:
        """
        Given file_path but no line_number,
        When post_comment is called,
        Then ActionableError is raised with corrective guidance.
        """
        # Given: file_path without line_number
        client = _mock_client()

        # When / Then: ActionableError is raised with corrective guidance
        with pytest.raises(ActionableError) as exc_info:
            post_comment(
                client,
                "MyRepo",
                42,
                "Missing line",
                "MyProject",
                file_path="src/foo.py",
            )

        assert "line" in str(exc_info.value).lower(), (
            f"Expected error to mention 'line', got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective guidance, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "validation", (
            f"Expected error_type='validation', got {exc_info.value.error_type!r}"
        )

    def test_line_number_without_file_path_raises_actionable_actionable_error_with_guidance(
        self,
    ) -> None:
        """
        Given line_number but no file_path,
        When post_comment is called,
        Then ActionableError is raised with corrective guidance.
        """
        # Given: line_number without file_path
        client = _mock_client()

        # When / Then: ActionableError is raised with corrective guidance
        with pytest.raises(ActionableError) as exc_info:
            post_comment(
                client,
                "MyRepo",
                42,
                "Missing file",
                "MyProject",
                line_number=10,
            )

        assert "file" in str(exc_info.value).lower(), (
            f"Expected error to mention 'file', got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective guidance, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "validation", (
            f"Expected error_type='validation', got {exc_info.value.error_type!r}"
        )

    def test_empty_content_raises_actionable_error_with_guidance(self) -> None:
        """
        Given empty content,
        When post_comment is called,
        Then ActionableError is raised with corrective guidance.
        """
        # Given: empty content
        client = _mock_client()

        # When / Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            post_comment(client, "MyRepo", 42, "   ", "MyProject")

        assert (
            "content" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()
        ), f"Expected error to mention content/empty, got: {exc_info.value}"
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective guidance, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "validation", (
            f"Expected error_type='validation', got {exc_info.value.error_type!r}"
        )


# ---------------------------------------------------------------------------
# TestBatchPostComments
# ---------------------------------------------------------------------------


class TestBatchPostComments:
    """
    REQUIREMENT: Multiple comments can be posted in a single operation with
                 per-comment positioning and partial-success semantics.

    WHO: Code review tools that generate multiple findings per PR.
    WHAT: (1) all valid comments are posted and PostingResult.posted contains their thread IDs
          (2) an invalid comment in the batch is collected as a failure while others succeed
          (3) dry_run=True validates without posting and PostingResult.skipped has all indices
          (4) an empty comments list returns PostingResult with empty lists
          (5) comments with file_path auto-resolve iteration context from the PR
    WHY: Posting comments one-at-a-time is slow and error-prone. Batch
         operations with partial-success match the existing resolve_comments
         pattern.

    MOCK BOUNDARY:
        Mock:  client.git.create_thread, client.git.get_pull_request_iterations,
               client.git.get_pull_request_iteration_changes (SDK network calls)
        Real:  post_comments composition logic, iteration resolution, error collection
        Never: post_comments itself
    """

    def test_all_valid_comments_are_posted(self) -> None:
        """
        Given 3 valid CommentPayload items,
        When post_comments is called,
        Then all 3 are posted and PostingResult.posted has 3 thread IDs.
        """
        # Given: 3 valid CommentPayload items
        comments = [
            CommentPayload(content="Comment 1"),
            CommentPayload(content="Comment 2"),
            CommentPayload(content="Comment 3"),
        ]
        thread_ids = iter([101, 102, 103])
        client = _mock_client()

        # Each create_thread call returns a different thread ID
        def _create_thread_side_effect(*args: object, **kwargs: object) -> Mock:
            resp = Mock()
            resp.id = next(thread_ids)
            return resp

        client.git.create_thread.side_effect = _create_thread_side_effect

        # When: post_comments is called
        result = post_comments(client, "MyRepo", 42, comments, "MyProject")

        # Then: all 3 posted, result has 3 thread IDs
        assert isinstance(result, PostingResult), (
            f"Expected PostingResult, got {type(result).__name__}"
        )
        assert len(result.posted) == 3, (
            f"Expected 3 posted thread IDs, got {len(result.posted)}: {result.posted}"
        )
        assert result.posted == [101, 102, 103], f"Expected [101, 102, 103], got {result.posted}"
        assert len(result.failures) == 0, f"Expected no failures, got {result.failures}"

    def test_invalid_comment_collected_as_failure_while_others_succeed(self) -> None:
        """
        Given 3 comments where the middle one has file_path but no line_number,
        When post_comments is called,
        Then 2 succeed and 1 failure is collected with error details.
        """
        # Given: 3 comments, middle one is invalid (file_path without line_number)
        comments = [
            CommentPayload(content="Good comment 1"),
            CommentPayload(content="Bad comment", file_path="src/bad.py"),  # no line_number
            CommentPayload(content="Good comment 3"),
        ]
        thread_ids = iter([201, 202])
        client = _mock_client()

        def _create_thread_side_effect(*args: object, **kwargs: object) -> Mock:
            resp = Mock()
            resp.id = next(thread_ids)
            return resp

        client.git.create_thread.side_effect = _create_thread_side_effect

        # When: post_comments is called
        result = post_comments(client, "MyRepo", 42, comments, "MyProject")

        # Then: 2 succeed, 1 failure collected
        assert len(result.posted) == 2, (
            f"Expected 2 posted, got {len(result.posted)}: {result.posted}"
        )
        assert len(result.failures) == 1, (
            f"Expected 1 failure, got {len(result.failures)}: {result.failures}"
        )
        failure = result.failures[0]
        assert failure.context is not None, (
            f"Expected context on failure, got None"
        )
        assert failure.context["index"] == 1, (
            f"Expected failure at index 1, got index {failure.context.get('index')}"
        )

    def test_dry_run_validates_without_posting(self) -> None:
        """
        Given dry_run=True and 2 valid comments,
        When post_comments is called,
        Then no API calls are made and PostingResult.skipped has all indices.
        """
        # Given: dry_run=True with valid comments
        comments = [
            CommentPayload(content="Comment A"),
            CommentPayload(content="Comment B"),
        ]
        client = _mock_client()

        # When: post_comments is called with dry_run=True
        result = post_comments(client, "MyRepo", 42, comments, "MyProject", dry_run=True)

        # Then: no API calls made, all indices skipped
        assert result.dry_run is True, f"Expected dry_run=True, got {result.dry_run}"
        assert len(result.skipped) == 2, (
            f"Expected 2 skipped indices, got {len(result.skipped)}: {result.skipped}"
        )
        assert client.git.create_thread.call_count == 0, (
            f"Expected 0 create_thread calls in dry_run, got {client.git.create_thread.call_count}"
        )

    def test_empty_comments_list_returns_empty_result(self) -> None:
        """
        Given an empty comments list,
        When post_comments is called,
        Then PostingResult has empty lists and no error.
        """
        # Given: empty comments list
        client = _mock_client()

        # When: post_comments is called
        result = post_comments(client, "MyRepo", 42, [], "MyProject")

        # Then: empty result
        assert isinstance(result, PostingResult), (
            f"Expected PostingResult, got {type(result).__name__}"
        )
        assert result.posted == [], f"Expected empty posted, got {result.posted}"
        assert result.failures == [], f"Expected empty failures, got {result.failures}"
        assert result.skipped == [], f"Expected empty skipped, got {result.skipped}"

    def test_positioned_comments_auto_resolve_iteration_context(self) -> None:
        """
        Given comments with file_path and line_number but no explicit iteration context,
        When post_comments is called,
        Then iteration context is auto-resolved and comments are posted with correct positioning.
        """
        # Given: positioned comments with iteration data available from SDK
        comments = [
            CommentPayload(content="Line comment", file_path="src/app.py", line_number=10),
        ]
        iters = [_make_iteration(iteration_id=4)]
        changes_resp = _make_changes_response(
            [
                {"path": "/src/app.py", "change_type": "edit", "change_tracking_id": 33},
            ]
        )
        client = _mock_client(thread_id=300, iterations=iters, changes=changes_resp)

        # When: post_comments is called
        result = post_comments(client, "MyRepo", 42, comments, "MyProject")

        # Then: comment posted with auto-resolved iteration context
        assert len(result.posted) == 1, (
            f"Expected 1 posted, got {len(result.posted)}: {result.posted}"
        )
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        pr_ctx = thread_obj.pull_request_thread_context
        assert pr_ctx is not None, (
            "Expected auto-resolved pullRequestThreadContext in batch, got None"
        )
        assert pr_ctx.change_tracking_id == 33, (
            f"Expected changeTrackingId=33, got {pr_ctx.change_tracking_id}"
        )

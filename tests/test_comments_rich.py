"""
BDD tests for ado_workflows.comments.post_rich_comments — rich comment orchestration.

Covers:
- TestBatchPosting: configurable batch sizes and iteration context sharing
- TestDryRunValidation: validate without posting when dry_run=True
- TestReplyThreading: parent_thread_id routes to reply_to_comment
- TestEndToEndOrchestration: full compose of format → filter → validate → post

Public API surface (from src/ado_workflows/comments.py):
    post_rich_comments(client, repository, pr_id, comments, project, *,
                       dry_run=False, batch_size=5, filter_self_praise=True,
                       formatter=None) -> RichPostingResult
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from ado_workflows.comments import post_rich_comments
from ado_workflows.models import (
    CommentSeverity,
    CommentType,
    RichComment,
    UserIdentity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(*, create_thread_ids: list[int] | None = None) -> Mock:
    """Return a mock AdoClient whose git.create_thread returns incrementing thread IDs."""
    client = Mock()
    if create_thread_ids:
        responses: list[Mock] = []
        for tid in create_thread_ids:
            resp = Mock()
            resp.id = tid
            responses.append(resp)
        client.git.create_thread.side_effect = responses
    else:
        resp = Mock()
        resp.id = 1
        client.git.create_thread.return_value = resp

    # reply returns a comment with id
    reply_resp = Mock()
    reply_resp.id = 1
    client.git.create_comment.return_value = reply_resp

    # For iteration context resolution
    iterations_resp = Mock()
    iterations_resp.id = 1
    iterations_resp.created_date = None
    iterations_resp.description = None
    client.git.get_pull_request_iterations.return_value = [iterations_resp]

    changes_resp = Mock()
    change_item = Mock()
    change_item.item = Mock()
    change_item.item.path = "/src/example.py"
    change_item.change_tracking_id = 1
    change_item.change_type = "edit"
    changes_resp.change_entries = [change_item]
    client.git.get_pull_request_iteration_changes.return_value = changes_resp

    return client


def _make_comment(
    comment_id: str = "c-001",
    title: str = "Test",
    content: str = "Test content.",
    *,
    severity: CommentSeverity = CommentSeverity.INFO,
    comment_type: CommentType = CommentType.GENERAL,
    file_path: str | None = None,
    line_number: int | None = None,
    parent_thread_id: int | None = None,
) -> RichComment:
    """Build a RichComment with sensible defaults."""
    return RichComment(
        comment_id=comment_id,
        title=title,
        content=content,
        severity=severity,
        comment_type=comment_type,
        file_path=file_path,
        line_number=line_number,
        parent_thread_id=parent_thread_id,
    )


def _mock_identities(*, same_user: bool = False) -> tuple[UserIdentity, UserIdentity]:
    """Return (pr_author, current_user) identities."""
    author = UserIdentity(display_name="Alice", id="author-guid-001")
    if same_user:
        current = UserIdentity(display_name="Alice", id="author-guid-001")
    else:
        current = UserIdentity(display_name="Bob", id="reviewer-guid-002")
    return author, current


class TestBatchPosting:
    """
    REQUIREMENT: Post rich comments in configurable batches with rate-limiting awareness.

    WHO: Any consumer posting multiple comments to a single PR.
    WHAT: (1) 12 comments with batch_size=5 sends 3 batches (5, 5, 2)
          (2) 3 comments with batch_size=10 sends 1 batch
          (3) batch_size=1 posts each comment individually
          (4) comments with file_path resolve iteration context once and reuse
    WHY: Azure DevOps rate-limits API calls. Batching prevents 429 errors
         on PRs with many review comments.

    MOCK BOUNDARY:
        Mock:  AdoClient (SDK boundary)
        Real:  formatting, filtering, batching logic, iteration resolution
        Never: never mock the formatter or filter functions
    """

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_twelve_comments_batch_size_five_sends_three_batches(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given 12 comments and batch_size=5
        When posting with post_rich_comments
        Then all 12 comments are posted (3 batches of 5, 5, 2)
        """
        # Given: 12 comments, mock client
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        thread_ids = list(range(100, 112))
        client = _mock_client(create_thread_ids=thread_ids)
        comments = [_make_comment(comment_id=f"c-{i:03d}") for i in range(12)]

        # When: posting with batch_size=5
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            batch_size=5,
            filter_self_praise=False,
        )

        # Then: all 12 posted
        assert len(result.posted) == 12, f"Expected 12 posted, got {len(result.posted)}"
        assert client.git.create_thread.call_count == 12, (
            f"Expected 12 create_thread calls, got {client.git.create_thread.call_count}"
        )

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_three_comments_batch_size_ten_sends_one_batch(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given 3 comments and batch_size=10
        When posting
        Then all 3 are posted in a single batch
        """
        # Given: 3 comments
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[200, 201, 202])
        comments = [_make_comment(comment_id=f"c-{i:03d}") for i in range(3)]

        # When: posting with batch_size=10
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            batch_size=10,
            filter_self_praise=False,
        )

        # Then: all 3 posted
        assert len(result.posted) == 3, f"Expected 3 posted, got {len(result.posted)}"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_batch_size_one_posts_individually(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given 3 comments and batch_size=1
        When posting
        Then each comment is its own batch (3 individual posts)
        """
        # Given: 3 comments
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[300, 301, 302])
        comments = [_make_comment(comment_id=f"c-{i:03d}") for i in range(3)]

        # When: posting with batch_size=1
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            batch_size=1,
            filter_self_praise=False,
        )

        # Then: all 3 posted individually
        assert len(result.posted) == 3, f"Expected 3 posted, got {len(result.posted)}"
        assert client.git.create_thread.call_count == 3, "Each comment should be a separate post"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_file_positioned_comments_resolve_iteration_once(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given comments with file_path
        When posting
        Then iteration context is resolved once and reused across all comments
        """
        # Given: 3 positioned comments
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[400, 401, 402])
        comments = [
            _make_comment(comment_id=f"c-{i:03d}", file_path="src/example.py", line_number=i + 1)
            for i in range(3)
        ]

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            filter_self_praise=False,
        )

        # Then: iteration resolved once (get_pull_request_iterations called once)
        assert client.git.get_pull_request_iterations.call_count == 1, (
            "Iteration context should be resolved once for all positioned comments"
        )
        assert len(result.posted) == 3, "All 3 positioned comments should be posted"


class TestDryRunValidation:
    """
    REQUIREMENT: Validate comment structure without posting when dry_run=True.

    WHO: AI agents wanting to verify payloads before committing to posting.
    WHAT: (1) dry_run=True with valid comments → all in skipped, no API calls
          (2) dry_run=True with missing title → failure
          (3) dry_run=True with line_number but no file_path → failure
          (4) dry_run=True with filter_self_praise=True → local_praise populated
          (5) dry_run=True with empty content → failure with field_name="content"
          (6) dry_run=True with whitespace-only title → failure
          (7) dry_run=True with whitespace-only content → failure
          (8) validation failures include context with index and comment_id
    WHY: Agents can catch formatting/validation errors before wasting API calls.

    MOCK BOUNDARY:
        Mock:  AdoClient (should never be called in dry_run)
        Real:  validation, formatting, filtering
        Never: never mock validation logic
    """

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_valid_comments_all_skipped_no_api_calls(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given dry_run=True and valid comments
        When posting
        Then RichPostingResult has all indices in skipped, no API calls made
        """
        # Given: valid comments, dry_run
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        comments = [_make_comment(comment_id=f"c-{i:03d}") for i in range(3)]

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: all skipped, no API calls
        assert result.dry_run is True, "dry_run should be True"
        assert len(result.skipped) == 3, f"Expected 3 skipped, got {len(result.skipped)}"
        assert len(result.posted) == 0, "No comments should be posted in dry_run"
        assert client.git.create_thread.call_count == 0, "No API calls in dry_run"

    def test_missing_title_appears_in_failures(self) -> None:
        """
        Given dry_run=True and a comment with empty title
        When posting
        Then that comment appears in failures
        """
        # Given: comment with empty title
        client = _mock_client()
        comment = RichComment(
            comment_id="v-001",
            title="",
            content="Some content.",
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure for missing title
        assert len(result.failures) >= 1, "Should have at least one failure for empty title"

    def test_line_number_without_file_path_appears_in_failures(self) -> None:
        """
        Given dry_run=True and a comment with line_number but no file_path
        When posting
        Then that comment appears in failures
        """
        # Given: line_number without file_path
        client = _mock_client()
        comment = RichComment(
            comment_id="v-002",
            title="Orphan Line",
            content="Has line but no file.",
            line_number=42,
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure for missing file_path
        assert len(result.failures) >= 1, "Should have at least one failure for line without file"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_dry_run_with_self_praise_filtering(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given dry_run=True with filter_self_praise=True on own PR
        When posting
        Then local_praise is populated (filtering still runs)
        """
        # Given: same user, praise comment
        author, current = _mock_identities(same_user=True)
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        praise = RichComment(
            comment_id="dp-001",
            title="Excellent work!",
            content="Great implementation.",
        )
        criticism = RichComment(
            comment_id="dp-002",
            title="Bug",
            content="Null reference risk.",
            severity=CommentSeverity.ERROR,
        )

        # When: dry_run with self-praise filtering
        result = post_rich_comments(
            client,
            "repo",
            1,
            [praise, criticism],
            "project",
            dry_run=True,
            filter_self_praise=True,
        )

        # Then: local_praise populated, criticism skipped (not posted)
        assert len(result.local_praise) >= 1, "local_praise should have the praise comment"
        assert result.dry_run is True, "dry_run should be True"
        assert client.git.create_thread.call_count == 0, "No API calls in dry_run"

    def test_empty_content_appears_in_failures(self) -> None:
        """
        Given dry_run=True and a comment with empty content
        When posting
        Then that comment appears in failures with field_name='content'
        """
        # Given: comment with empty content
        client = _mock_client()
        comment = RichComment(
            comment_id="v-003",
            title="Has Title",
            content="",
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure for empty content
        assert len(result.failures) >= 1, "Should have at least one failure for empty content"
        assert any("content" in str(f).lower() for f in result.failures), (
            f"Failure should mention 'content', got: {result.failures}"
        )

    def test_whitespace_only_title_appears_in_failures(self) -> None:
        """
        Given dry_run=True and a comment with whitespace-only title
        When posting
        Then that comment appears in failures (whitespace is treated as empty)
        """
        # Given: comment with whitespace-only title
        client = _mock_client()
        comment = RichComment(
            comment_id="v-004",
            title="   ",
            content="Some valid content.",
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure for whitespace-only title
        assert len(result.failures) >= 1, (
            "Should have at least one failure for whitespace-only title"
        )
        assert any("title" in str(f).lower() for f in result.failures), (
            f"Failure should mention 'title', got: {result.failures}"
        )

    def test_whitespace_only_content_appears_in_failures(self) -> None:
        """
        Given dry_run=True and a comment with whitespace-only content
        When posting
        Then that comment appears in failures (whitespace is treated as empty)
        """
        # Given: comment with whitespace-only content
        client = _mock_client()
        comment = RichComment(
            comment_id="v-005",
            title="Valid Title",
            content="   \t\n  ",
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure for whitespace-only content
        assert len(result.failures) >= 1, (
            "Should have at least one failure for whitespace-only content"
        )
        assert any("content" in str(f).lower() for f in result.failures), (
            f"Failure should mention 'content', got: {result.failures}"
        )

    def test_validation_failure_includes_context_with_index_and_comment_id(self) -> None:
        """
        Given dry_run=True and an invalid comment at index 1
        When posting
        Then the failure's context includes index=1 and the comment's comment_id
        """
        # Given: valid first comment, invalid second comment
        client = _mock_client()
        valid = _make_comment(comment_id="ctx-001")
        invalid = RichComment(
            comment_id="ctx-002",
            title="",
            content="Has content but no title.",
        )

        # When: dry_run=True
        result = post_rich_comments(
            client,
            "repo",
            1,
            [valid, invalid],
            "project",
            dry_run=True,
            filter_self_praise=False,
        )

        # Then: failure context includes index and comment_id
        assert len(result.failures) == 1, f"Expected 1 failure, got {len(result.failures)}"
        failure = result.failures[0]
        assert failure.context is not None, "Failure should have a non-None context"
        assert failure.context["index"] == 1, (
            f"Failure context index should be 1, got {failure.context.get('index')}"
        )
        assert failure.context["comment_id"] == "ctx-002", (
            f"Failure context comment_id should be 'ctx-002', got {failure.context.get('comment_id')}"
        )


class TestReplyThreading:
    """
    REQUIREMENT: Comments with parent_thread_id are posted as replies to existing threads.

    WHO: AI agents that want to reply to specific review threads.
    WHAT: (1) parent_thread_id=42 → reply_to_comment called with thread_id=42
          (2) parent_thread_id=None → post_comment called (new thread)
          (3) mix of replies and new threads → each dispatched correctly
    WHY: Threaded replies keep related discussion together instead of scattering
         it across separate threads.

    MOCK BOUNDARY:
        Mock:  AdoClient (SDK boundary)
        Real:  threading dispatch logic, formatting
        Never: never mock the dispatch decision
    """

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_reply_to_existing_thread(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a comment with parent_thread_id=42
        When posting
        Then reply_to_comment is called with thread_id=42
        """
        # Given: reply comment
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        reply = _make_comment(comment_id="r-001", parent_thread_id=42)

        # When: posting
        post_rich_comments(
            client,
            "repo",
            1,
            [reply],
            "project",
            filter_self_praise=False,
        )

        # Then: reply_to_comment called
        assert client.git.create_comment.call_count == 1, "reply_to_comment should be called once"
        call_args = client.git.create_comment.call_args
        assert (
            call_args[0][3] == 42 or call_args[1].get("thread_id") == 42 or 42 in call_args[0]
        ), "reply_to_comment should be called with thread_id=42"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_new_thread_when_no_parent(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a comment with parent_thread_id=None
        When posting
        Then post_comment is called (new thread created)
        """
        # Given: new thread comment
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[500])
        new_comment = _make_comment(comment_id="n-001")

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            [new_comment],
            "project",
            filter_self_praise=False,
        )

        # Then: create_thread called (new thread)
        assert client.git.create_thread.call_count == 1, (
            "create_thread should be called for new threads"
        )
        assert len(result.posted) == 1, "One comment should be posted"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_mix_of_replies_and_new_threads(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a mix of replies (parent_thread_id set) and new threads (None)
        When posting
        Then each is dispatched correctly
        """
        # Given: one reply, one new thread
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[600])
        reply = _make_comment(comment_id="m-001", parent_thread_id=10)
        new_thread = _make_comment(comment_id="m-002")

        # When: posting
        post_rich_comments(
            client,
            "repo",
            1,
            [reply, new_thread],
            "project",
            filter_self_praise=False,
        )

        # Then: one reply, one new thread
        assert client.git.create_comment.call_count == 1, "One reply should use create_comment"
        assert client.git.create_thread.call_count == 1, "One new thread should use create_thread"


class TestEndToEndOrchestration:
    """
    REQUIREMENT: post_rich_comments composes formatting, filtering, validation,
    and posting into a single call.

    WHO: Library consumers and the MCP server.
    WHAT: (1) filter_self_praise=True on own PR → praise filtered, non-praise posted
          (2) filter_self_praise=False → all comments posted regardless
          (3) mix of valid and invalid → partial success
          (4) all comments fail → posted empty, all in failures
          (5) custom formatter → each posted comment uses custom formatter output
          (6) empty comments list → RichPostingResult with all empty lists, no API calls
          (7) self-praise author lookup failure → posting continues without filtering
          (8) iteration context failure → posting continues without file positioning
          (9) reply SDK failure → recorded in result.failures
    WHY: Consumers shouldn't need to manually compose these steps. The orchestrator
         handles correct ordering and shares iteration context.

    MOCK BOUNDARY:
        Mock:  AdoClient (SDK boundary), get_pr_author, get_current_user
        Real:  formatting, filtering, batching, validation, result assembly
        Never: never mock the formatter or filter logic
    """

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_self_praise_filtered_on_own_pr(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given filter_self_praise=True and own PR
        When posting a mix of praise and criticism
        Then praise is filtered to local_praise, criticism is posted
        """
        # Given: same user
        author, current = _mock_identities(same_user=True)
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[700])
        praise = RichComment(
            comment_id="e-001",
            title="Excellent work!",
            content="Great implementation.",
        )
        criticism = RichComment(
            comment_id="e-002",
            title="Bug Found",
            content="Null reference risk.",
            severity=CommentSeverity.ERROR,
        )

        # When: posting with self-praise filtering
        result = post_rich_comments(
            client,
            "repo",
            1,
            [praise, criticism],
            "project",
            filter_self_praise=True,
        )

        # Then: praise filtered, criticism posted
        assert len(result.local_praise) >= 1, "Praise should be in local_praise"
        assert len(result.posted) >= 1, "Criticism should be posted"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_no_filtering_when_disabled(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given filter_self_praise=False
        When posting
        Then all comments are posted regardless of praise content
        """
        # Given: same user but filtering disabled
        author, current = _mock_identities(same_user=True)
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[800, 801])
        praise = RichComment(
            comment_id="e-003",
            title="Great work!",
            content="Excellent.",
        )
        criticism = RichComment(
            comment_id="e-004",
            title="Bug",
            content="Issue found.",
            severity=CommentSeverity.ERROR,
        )

        # When: posting with filtering disabled
        result = post_rich_comments(
            client,
            "repo",
            1,
            [praise, criticism],
            "project",
            filter_self_praise=False,
        )

        # Then: all posted
        assert len(result.posted) == 2, "Both comments should be posted when filtering is disabled"
        assert len(result.local_praise) == 0, (
            "local_praise should be empty when filtering disabled"
        )

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_partial_success_with_mixed_validity(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a mix of valid and failing comments
        When posting
        Then valid ones succeed, failures appear in result.failures
        """
        # Given: one valid comment, one that will fail on SDK call
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        resp = Mock()
        resp.id = 900
        client.git.create_thread.side_effect = [resp, Exception("SDK error")]

        valid = _make_comment(comment_id="e-005")
        also_valid = _make_comment(comment_id="e-006")

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            [valid, also_valid],
            "project",
            filter_self_praise=False,
        )

        # Then: partial success
        assert len(result.posted) == 1, "One comment should succeed"
        assert len(result.failures) == 1, "One comment should fail"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_all_fail_posted_empty(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given all comments fail to post
        When posting
        Then posted is empty, all in failures
        """
        # Given: all SDK calls fail
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        client.git.create_thread.side_effect = Exception("Server error")

        comments = [_make_comment(comment_id=f"e-{i:03d}") for i in range(3)]

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            filter_self_praise=False,
        )

        # Then: all failed
        assert len(result.posted) == 0, "No comments should be posted when all fail"
        assert len(result.failures) == 3, "All 3 should appear in failures"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_custom_formatter_used_for_content(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a custom formatter
        When posting
        Then each posted comment uses the custom formatter's output as content
        """
        # Given: custom formatter
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[1000])

        def custom_fmt(c: RichComment) -> str:
            return f"[CUSTOM] {c.title}: {c.content}"

        comment = _make_comment(comment_id="e-010", title="Test Title", content="Test body.")

        # When: posting with custom formatter
        result = post_rich_comments(
            client,
            "repo",
            1,
            [comment],
            "project",
            filter_self_praise=False,
            formatter=custom_fmt,
        )

        # Then: custom formatted content was passed to create_thread
        assert len(result.posted) == 1, "Comment should be posted"
        # Verify the content passed to SDK contains custom format
        call_args = client.git.create_thread.call_args
        thread_obj = call_args[0][0]
        posted_content = thread_obj.comments[0].content
        assert "[CUSTOM]" in posted_content, (
            f"Posted content should use custom formatter, got: {posted_content[:80]}"
        )

    def test_empty_comments_list_returns_empty_result(self) -> None:
        """
        Given an empty comments list
        When posting
        Then RichPostingResult has all empty lists and no API calls are made
        """
        # Given: no comments
        client = _mock_client()

        # When: posting empty list
        result = post_rich_comments(
            client,
            "repo",
            1,
            [],
            "project",
        )

        # Then: empty result, no API calls
        assert len(result.posted) == 0, "posted should be empty"
        assert len(result.failures) == 0, "failures should be empty"
        assert len(result.skipped) == 0, "skipped should be empty"
        assert len(result.local_praise) == 0, "local_praise should be empty"
        assert result.dry_run is False, "dry_run should default to False"
        assert client.git.create_thread.call_count == 0, "No API calls for empty list"

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_author_lookup_failure_continues_without_filtering(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given filter_self_praise=True but get_pr_author raises an exception
        When posting
        Then all comments are posted (filtering skipped gracefully)
        """
        # Given: author lookup fails
        mock_author.side_effect = Exception("API unavailable")
        mock_user.return_value = UserIdentity(display_name="Bob", id="bob-guid")

        client = _mock_client(create_thread_ids=[1100, 1101])
        praise = RichComment(
            comment_id="g-001",
            title="Great work!",
            content="Excellent implementation.",
        )
        criticism = RichComment(
            comment_id="g-002",
            title="Bug Found",
            content="Null reference risk.",
            severity=CommentSeverity.ERROR,
        )

        # When: posting with self-praise filtering enabled
        result = post_rich_comments(
            client,
            "repo",
            1,
            [praise, criticism],
            "project",
            filter_self_praise=True,
        )

        # Then: all comments posted (filtering skipped due to lookup failure)
        assert len(result.posted) == 2, (
            f"All comments should be posted when author lookup fails, got {len(result.posted)}"
        )
        assert len(result.local_praise) == 0, (
            "local_praise should be empty when filtering could not run"
        )

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_iteration_context_failure_continues_without_positioning(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given comments with file_path but iteration context lookup fails initially
        When posting
        Then comments are still posted (post_comment auto-resolves iteration context)
        """
        # Given: positioned comments, iteration lookup fails once then succeeds
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client(create_thread_ids=[1200, 1201])
        # First call (from post_rich_comments) fails; subsequent calls (from post_comment)
        # succeed via the default mock setup in _mock_client.
        iterations_resp = Mock()
        iterations_resp.id = 1
        iterations_resp.created_date = None
        iterations_resp.description = None
        client.git.get_pull_request_iterations.side_effect = [
            Exception("Iteration API unavailable"),
            [iterations_resp],
            [iterations_resp],
        ]

        comments = [
            _make_comment(
                comment_id=f"iter-{i:03d}",
                file_path="src/example.py",
                line_number=i + 1,
            )
            for i in range(2)
        ]

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            comments,
            "project",
            filter_self_praise=False,
        )

        # Then: comments still posted despite initial iteration failure
        assert len(result.posted) == 2, (
            f"Comments should be posted even when initial iteration lookup fails, "
            f"got {len(result.posted)}"
        )
        # The first call raised, so post_rich_comments' iter_ctx is None,
        # but post_comment auto-resolves iteration context on its own.
        assert client.git.get_pull_request_iterations.call_count == 3, (
            "Should be called 3 times: 1 failed in post_rich_comments + 2 in post_comment"
        )

    @patch("ado_workflows.auth.get_current_user")
    @patch("ado_workflows.pr.get_pr_author")
    def test_reply_sdk_failure_recorded_in_failures(
        self,
        mock_author: Mock,
        mock_user: Mock,
    ) -> None:
        """
        Given a reply comment whose SDK call (create_comment) raises an exception
        When posting
        Then the failure is recorded in result.failures with the comment_id
        """
        # Given: reply that will fail
        author, current = _mock_identities()
        mock_author.return_value = author
        mock_user.return_value = current

        client = _mock_client()
        client.git.create_comment.side_effect = Exception("Reply API error")

        reply = _make_comment(comment_id="rf-001", parent_thread_id=42)

        # When: posting
        result = post_rich_comments(
            client,
            "repo",
            1,
            [reply],
            "project",
            filter_self_praise=False,
        )

        # Then: failure recorded
        assert len(result.posted) == 0, "No comments should be posted when reply fails"
        assert len(result.failures) == 1, "Reply failure should be recorded"
        failure = result.failures[0]
        assert failure.context is not None, "Failure should have a non-None context"
        assert failure.context["comment_id"] == "rf-001", (
            f"Failure context should reference comment_id 'rf-001', got: {failure.context}"
        )

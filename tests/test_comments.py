"""
BDD tests for ado_workflows.comments — comment analysis, sanitization, and writes.

Covers:
- TestSanitizeAdoResponse: Windows-1252 smart-quote fix (pure function)
- TestAnalyzePRComments: thread fetching, categorization, author stats
- TestPostComment: new comment thread creation via SDK
- TestReplyToComment: reply to existing thread via SDK
- TestResolveComments: batch thread resolution with partial success

Public API surface (from src/ado_workflows/comments.py):
    sanitize_ado_response(raw_data: bytes | str) -> str
    analyze_pr_comments(client: AdoClient, pr_id: int, project: str,
                        repository: str) -> CommentAnalysis
    post_comment(client: AdoClient, repository: str, pr_id: int,
                 content: str, project: str, *, status: str = "active") -> int
    reply_to_comment(client: AdoClient, repository: str, pr_id: int,
                     thread_id: int, content: str, project: str) -> int
    resolve_comments(client: AdoClient, repository: str, pr_id: int,
                     thread_ids: list[int], project: str, *,
                     status: str = "fixed") -> ResolveResult
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.comments import (
    analyze_pr_comments,
    post_comment,
    reply_to_comment,
    resolve_comments,
    sanitize_ado_response,
)

# ---------------------------------------------------------------------------
# Helpers for mock thread construction
# ---------------------------------------------------------------------------


def _mock_client(threads: list[Mock] | None = None) -> Mock:
    """Return a mock AdoClient whose git.get_threads returns *threads*."""
    client = Mock()
    client.git.get_threads.return_value = threads or []
    return client


def _make_thread(
    *,
    thread_id: int = 1,
    status: str = "active",
    comments: list[dict[str, object]] | None = None,
    file_path: str | None = None,
    right_line_start: int | None = None,
    right_line_end: int | None = None,
    left_line_start: int | None = None,
    left_line_end: int | None = None,
) -> Mock:
    """Build a mock GitPullRequestCommentThread with SDK-shaped attributes."""
    thread = Mock()
    thread.id = thread_id
    thread.status = status

    # Build mock comments
    mock_comments: list[Mock] = []
    for c in comments or []:
        comment = Mock()
        author = Mock()
        author.display_name = c.get("author", "Unknown")
        comment.author = author
        comment.content = c.get("content", "")
        comment.published_date = c.get("published_date")
        comment.is_deleted = c.get("is_deleted", False)
        comment.id = c.get("id", 1)
        mock_comments.append(comment)
    thread.comments = mock_comments if mock_comments else []

    # Thread context → file path and line ranges
    if file_path is not None:
        ctx = Mock()
        ctx.file_path = file_path

        right_start = Mock()
        right_start.line = right_line_start
        ctx.right_file_start = right_start if right_line_start is not None else None

        right_end = Mock()
        right_end.line = right_line_end
        ctx.right_file_end = right_end if right_line_end is not None else None

        left_start = Mock()
        left_start.line = left_line_start
        ctx.left_file_start = left_start if left_line_start is not None else None

        left_end = Mock()
        left_end.line = left_line_end
        ctx.left_file_end = left_end if left_line_end is not None else None

        thread.thread_context = ctx
    else:
        thread.thread_context = None

    return thread


class TestSanitizeAdoResponse:
    """
    REQUIREMENT: sanitize_ado_response() fixes Windows-1252 smart-quote
    corruption in raw bytes from ADO responses.

    WHO: analyze_pr_comments() and any consumer processing raw ADO content.
    WHAT: (1) string inputs pass through unchanged
          (2) Windows-1252 left double quote (0x93) is replaced with UTF-8 U+201C
          (3) Windows-1252 right double quote (0x94) is replaced with UTF-8 U+201D
          (4) Windows-1252 left single quote (0x91) is replaced with UTF-8 U+2018
          (5) Windows-1252 right single quote (0x92) is replaced with UTF-8 U+2019
          (6) clean UTF-8 bytes are decoded normally with no replacements
          (7) mixed smart quotes and normal text are all handled correctly
    WHY: ADO REST API sometimes returns Windows-1252 encoded smart quotes in
         PR comments. These bytes are invalid UTF-8 and cause
         UnicodeDecodeError without sanitization. Battle-tested in production.

    MOCK BOUNDARY:
        Mock:  nothing — pure function, bytes/str in → str out
        Real:  sanitize_ado_response
        Never: N/A
    """

    def test_string_input_passes_through_unchanged(self) -> None:
        """
        Given a string input
        When sanitize_ado_response is called
        Then the same string is returned unchanged
        """
        # Given: a normal string
        text = "This is a normal comment with no special characters"

        # When: called with string input
        result = sanitize_ado_response(text)

        # Then: same string returned
        assert result == text, f"Expected string passthrough, got '{result}'"

    def test_left_double_quote_replaced(self) -> None:
        """
        Given bytes containing Windows-1252 left double quote (0x93)
        When sanitize_ado_response is called
        Then it is replaced with UTF-8 left double quote (U+201C)
        """
        # Given: bytes with 0x93 (Windows-1252 left double quote)
        raw = b"He said \x93hello"

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: 0x93 replaced with UTF-8 U+201C (\u201c = \xe2\x80\x9c)
        assert "\u201c" in result, (
            f"Expected UTF-8 left double quote (U+201C) in result, got '{result}'"
        )
        assert result == "He said \u201chello", f"Expected 'He said \u201chello', got '{result}'"

    def test_right_double_quote_replaced(self) -> None:
        """
        Given bytes containing Windows-1252 right double quote (0x94)
        When sanitize_ado_response is called
        Then it is replaced with UTF-8 right double quote (U+201D)
        """
        # Given: bytes with 0x94 (Windows-1252 right double quote)
        raw = b"hello\x94 he said"

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: 0x94 replaced with UTF-8 U+201D
        assert "\u201d" in result, (
            f"Expected UTF-8 right double quote (U+201D) in result, got '{result}'"
        )

    def test_left_single_quote_replaced(self) -> None:
        """
        Given bytes containing Windows-1252 left single quote (0x91)
        When sanitize_ado_response is called
        Then it is replaced with UTF-8 left single quote (U+2018)
        """
        # Given: bytes with 0x91
        raw = b"it\x91s fine"

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: 0x91 replaced with UTF-8 U+2018
        assert "\u2018" in result, (
            f"Expected UTF-8 left single quote (U+2018) in result, got '{result}'"
        )

    def test_right_single_quote_replaced(self) -> None:
        """
        Given bytes containing Windows-1252 right single quote (0x92)
        When sanitize_ado_response is called
        Then it is replaced with UTF-8 right single quote (U+2019)
        """
        # Given: bytes with 0x92
        raw = b"don\x92t worry"

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: 0x92 replaced with UTF-8 U+2019
        assert "\u2019" in result, (
            f"Expected UTF-8 right single quote (U+2019) in result, got '{result}'"
        )

    def test_clean_utf8_bytes_decoded_normally(self) -> None:
        """
        Given clean UTF-8 bytes with no Windows-1252 characters
        When sanitize_ado_response is called
        Then bytes are decoded normally with no replacements
        """
        # Given: clean UTF-8 bytes
        raw = "Normal comment with émojis 🎉".encode()

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: decoded normally
        assert result == "Normal comment with émojis 🎉", (
            f"Expected clean UTF-8 decode, got '{result}'"
        )

    def test_mixed_smart_quotes_and_normal_text(self) -> None:
        """
        Given bytes with multiple smart quote types mixed with normal text
        When sanitize_ado_response is called
        Then all smart quotes are replaced and normal text is preserved
        """
        # Given: bytes with both single and double smart quotes
        raw = b"\x93Hello,\x94 she said. \x91It\x92s fine.\x91"

        # When: sanitized
        result = sanitize_ado_response(raw)

        # Then: all four smart quote types replaced, normal text preserved
        assert "\u201c" in result, f"Expected left double quote in result, got '{result}'"
        assert "\u201d" in result, f"Expected right double quote in result, got '{result}'"
        assert "\u2018" in result, f"Expected left single quote in result, got '{result}'"
        assert "\u2019" in result, f"Expected right single quote in result, got '{result}'"
        assert "Hello," in result, f"Expected normal text 'Hello,' preserved, got '{result}'"
        assert "she said." in result, f"Expected normal text 'she said.' preserved, got '{result}'"


class TestAnalyzePRComments:
    """
    REQUIREMENT: analyze_pr_comments() fetches and categorizes all comment
    threads on a PR.

    WHO: MCP tools, code review dashboards, any consumer needing comment status.
    WHAT: (1) mixed thread statuses produce correct counts in CommentSummary
          (2) no threads produce an empty analysis with resolution_ready=True
          (3) threads with file context populate file_path, line_start, line_end
          (4) threads without file context have None for file fields
          (5) long comment content is truncated at 200 chars with "..." appended
          (6) short comment content is not truncated
          (7) multiple authors are counted correctly in comment_authors
          (8) author_samples contain the latest comment and status
          (9) deleted comments are excluded from author_samples
          (10) all threads resolved means resolution_ready=True
          (11) at least one active thread means resolution_ready=False
          (12) line_start falls back to leftFileStart when rightFileStart is absent
          (13) the SDK call receives the correct parameters
          (14) author with all comments deleted is absent from author_samples
    WHY: Comment analysis drives PR review workflows — knowing which threads
         are unresolved, who commented, and whether a PR is resolution-ready.

    MOCK BOUNDARY:
        Mock:  client.git.get_threads (SDK I/O edge)
        Real:  analyze_pr_comments, sanitize_ado_response, all dataclasses
        Never: N/A
    """

    def test_mixed_thread_statuses_produce_correct_summary(self) -> None:
        """
        Given threads with mixed statuses (2 active, 3 fixed)
        When analyze_pr_comments is called
        Then CommentSummary has total=5, active=2, fixed=3, active_percentage=40.0
        """
        # Given: 2 active and 3 fixed threads
        threads = [
            _make_thread(
                thread_id=i,
                status="active",
                comments=[{"author": "Alice Smith", "content": f"Comment {i}"}],
            )
            for i in range(1, 3)
        ] + [
            _make_thread(
                thread_id=i,
                status="fixed",
                comments=[{"author": "Bob Jones", "content": f"Resolved {i}"}],
            )
            for i in range(3, 6)
        ]
        client = _mock_client(threads)

        # When: analyze_pr_comments is called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: summary counts are correct
        assert result.comment_summary.total_threads == 5, (
            f"Expected total_threads 5, got {result.comment_summary.total_threads}"
        )
        assert result.comment_summary.active_threads == 2, (
            f"Expected active_threads 2, got {result.comment_summary.active_threads}"
        )
        assert result.comment_summary.fixed_threads == 3, (
            f"Expected fixed_threads 3, got {result.comment_summary.fixed_threads}"
        )
        assert result.comment_summary.active_percentage == 40.0, (
            f"Expected active_percentage 40.0, got {result.comment_summary.active_percentage}"
        )

    def test_no_threads_produces_empty_analysis(self) -> None:
        """
        Given no threads
        When analyze_pr_comments is called
        Then CommentSummary has all zeros and resolution_ready=True
        """
        # Given: no threads
        client = _mock_client([])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: empty analysis
        assert result.comment_summary.total_threads == 0, (
            f"Expected total_threads 0, got {result.comment_summary.total_threads}"
        )
        assert result.comment_summary.active_threads == 0, (
            f"Expected active_threads 0, got {result.comment_summary.active_threads}"
        )
        assert result.resolution_ready is True, (
            f"Expected resolution_ready True, got {result.resolution_ready}"
        )

    def test_threads_with_file_context_populate_comment_info(self) -> None:
        """
        Given a thread with file context (filePath, rightFileStart, rightFileEnd)
        When analyze_pr_comments is called
        Then CommentInfo contains file_path, line_start, line_end
        """
        # Given: a thread with file context on right side
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[{"author": "Alice Smith", "content": "Fix this null check"}],
            file_path="/src/payment.py",
            right_line_start=42,
            right_line_end=45,
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: file context populated
        assert len(result.active_comments) == 1, (
            f"Expected 1 active comment, got {len(result.active_comments)}"
        )
        comment = result.active_comments[0]
        assert comment.file_path == "/src/payment.py", (
            f"Expected file_path '/src/payment.py', got '{comment.file_path}'"
        )
        assert comment.line_start == 42, f"Expected line_start 42, got {comment.line_start}"
        assert comment.line_end == 45, f"Expected line_end 45, got {comment.line_end}"

    def test_threads_without_file_context_have_none_fields(self) -> None:
        """
        Given a thread with no file context (general PR comment)
        When analyze_pr_comments is called
        Then file_path, line_start, and line_end are None
        """
        # Given: a thread with no thread_context
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[{"author": "Alice Smith", "content": "LGTM overall"}],
            file_path=None,
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: nullable fields are None
        comment = result.active_comments[0]
        assert comment.file_path is None, f"Expected file_path None, got '{comment.file_path}'"
        assert comment.line_start is None, f"Expected line_start None, got {comment.line_start}"
        assert comment.line_end is None, f"Expected line_end None, got {comment.line_end}"

    def test_long_comment_content_truncated_at_200_chars(self) -> None:
        """
        Given a comment with content longer than 200 characters
        When analyze_pr_comments is called
        Then content_preview is truncated at 200 chars with "..." appended
        """
        # Given: a comment with 250 chars of content
        long_content = "A" * 250
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[{"author": "Alice Smith", "content": long_content}],
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: preview truncated at 200 + "..."
        comment = result.active_comments[0]
        assert len(comment.content_preview) == 203, (
            f"Expected content_preview length 203 (200 + '...'), "
            f"got {len(comment.content_preview)}"
        )
        assert comment.content_preview.endswith("..."), (
            f"Expected content_preview to end with '...', got '{comment.content_preview[-10:]}'"
        )
        assert comment.full_content == long_content, (
            f"Expected full_content to be untruncated, got length {len(comment.full_content)}"
        )

    def test_short_comment_content_not_truncated(self) -> None:
        """
        Given a comment with content <= 200 characters
        When analyze_pr_comments is called
        Then content_preview equals full_content (no truncation)
        """
        # Given: a short comment
        short_content = "LGTM, ship it!"
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[{"author": "Alice Smith", "content": short_content}],
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: no truncation
        comment = result.active_comments[0]
        assert comment.content_preview == short_content, (
            f"Expected content_preview '{short_content}', got '{comment.content_preview}'"
        )

    def test_multiple_authors_counted_correctly(self) -> None:
        """
        Given comments from multiple authors
        When analyze_pr_comments is called
        Then comment_authors has correct counts per author
        """
        # Given: threads from two authors with different comment counts
        threads = [
            _make_thread(
                thread_id=1,
                status="active",
                comments=[
                    {"author": "Alice Smith", "content": "Comment 1"},
                    {"author": "Alice Smith", "content": "Comment 2"},
                ],
            ),
            _make_thread(
                thread_id=2,
                status="fixed",
                comments=[{"author": "Bob Jones", "content": "Fixed this"}],
            ),
        ]
        client = _mock_client(threads)

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: author counts correct
        assert result.comment_authors.get("Alice Smith") == 2, (
            f"Expected Alice Smith count 2, got {result.comment_authors.get('Alice Smith')}"
        )
        assert result.comment_authors.get("Bob Jones") == 1, (
            f"Expected Bob Jones count 1, got {result.comment_authors.get('Bob Jones')}"
        )

    def test_author_samples_populate_latest_comment_and_status(self) -> None:
        """
        Given comments from an author
        When analyze_pr_comments is called
        Then author_samples contains the author's latest comment and status
        """
        # Given: an author with two comments in the same thread
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[
                {"author": "Alice Smith", "content": "First comment"},
                {"author": "Alice Smith", "content": "Follow-up comment"},
            ],
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: author sample has latest comment
        assert "Alice Smith" in result.author_samples, (
            f"Expected Alice Smith in author_samples, "
            f"got keys: {list(result.author_samples.keys())}"
        )
        sample = result.author_samples["Alice Smith"]
        assert sample.latest_comment == "Follow-up comment", (
            f"Expected latest_comment 'Follow-up comment', got '{sample.latest_comment}'"
        )
        assert sample.latest_status == "active", (
            f"Expected latest_status 'active', got '{sample.latest_status}'"
        )
        assert sample.count == 2, f"Expected count 2, got {sample.count}"

    def test_deleted_comments_excluded_from_author_samples(self) -> None:
        """
        Given a deleted comment from an author
        When analyze_pr_comments is called
        Then the deleted comment is excluded from author_samples
        """
        # Given: one deleted and one active comment from the same author
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[
                {"author": "Alice Smith", "content": "Please delete this", "is_deleted": True},
                {"author": "Alice Smith", "content": "This is the real comment"},
            ],
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: author sample uses only the non-deleted comment
        sample = result.author_samples["Alice Smith"]
        assert sample.latest_comment == "This is the real comment", (
            f"Expected latest non-deleted comment, got '{sample.latest_comment}'"
        )

    def test_author_with_all_deleted_comments_absent_from_author_samples(self) -> None:
        """
        Given an author whose only comments are all deleted
        When analyze_pr_comments is called
        Then that author does not appear in author_samples
        """
        # Given: one author with all deleted comments, another with a live comment
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[
                {"author": "Ghost User", "content": "Deleted remark", "is_deleted": True},
                {"author": "Alice Smith", "content": "Real feedback"},
            ],
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: Ghost User absent, Alice present
        assert "Ghost User" not in result.author_samples, (
            f"Author with all deleted comments should not appear in author_samples, "
            f"got keys: {list(result.author_samples.keys())}"
        )
        assert "Alice Smith" in result.author_samples, (
            "Author with non-deleted comments should still appear"
        )

    def test_all_threads_resolved_means_resolution_ready(self) -> None:
        """
        Given all threads have status 'fixed' (no active)
        When analyze_pr_comments is called
        Then resolution_ready is True
        """
        # Given: all fixed threads
        threads = [
            _make_thread(
                thread_id=i,
                status="fixed",
                comments=[{"author": "Alice Smith", "content": f"Fixed {i}"}],
            )
            for i in range(1, 4)
        ]
        client = _mock_client(threads)

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: resolution ready
        assert result.resolution_ready is True, (
            f"Expected resolution_ready True, got {result.resolution_ready}"
        )

    def test_at_least_one_active_thread_means_not_resolution_ready(self) -> None:
        """
        Given at least one active thread among fixed threads
        When analyze_pr_comments is called
        Then resolution_ready is False
        """
        # Given: 2 fixed + 1 active
        threads = [
            _make_thread(
                thread_id=1, status="fixed", comments=[{"author": "A", "content": "Done"}]
            ),
            _make_thread(
                thread_id=2, status="fixed", comments=[{"author": "A", "content": "Done"}]
            ),
            _make_thread(
                thread_id=3, status="active", comments=[{"author": "B", "content": "Still open"}]
            ),
        ]
        client = _mock_client(threads)

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: not resolution ready
        assert result.resolution_ready is False, (
            f"Expected resolution_ready False, got {result.resolution_ready}"
        )

    def test_line_start_falls_back_to_left_file_start(self) -> None:
        """
        Given a thread with only leftFileStart (no rightFileStart)
        When analyze_pr_comments is called
        Then line_start uses leftFileStart as fallback
        """
        # Given: thread with leftFileStart only
        thread = _make_thread(
            thread_id=1,
            status="active",
            comments=[{"author": "Alice Smith", "content": "Check this"}],
            file_path="/src/old_file.py",
            right_line_start=None,
            right_line_end=None,
            left_line_start=10,
            left_line_end=15,
        )
        client = _mock_client([thread])

        # When: called
        result = analyze_pr_comments(client, pr_id=42, project="Proj", repository="Repo")

        # Then: line_start falls back to leftFileStart
        comment = result.active_comments[0]
        assert comment.line_start == 10, (
            f"Expected line_start 10 from leftFileStart fallback, got {comment.line_start}"
        )
        assert comment.line_end == 15, (
            f"Expected line_end 15 from leftFileEnd fallback, got {comment.line_end}"
        )

    def test_sdk_call_receives_correct_parameters(self) -> None:
        """
        When analyze_pr_comments is called
        Then client.git.get_threads is called with repository, pr_id, project
        """
        # Given: a mock client
        client = _mock_client([])

        # When: called with specific parameters
        analyze_pr_comments(client, pr_id=42, project="MyProject", repository="MyRepo")

        # Then: SDK method called with correct arguments
        client.git.get_threads.assert_called_once_with("MyRepo", 42, project="MyProject")


# ---------------------------------------------------------------------------
# Phase 6d — Comment write operations
# ---------------------------------------------------------------------------


class TestPostComment:
    """
    REQUIREMENT: post_comment() creates a new comment thread on a PR.

    WHO: MCP tools posting AI analysis, review feedback, or status updates.
    WHAT: (1) valid content creates a thread and returns the new thread ID
          (2) a custom status is passed through to the SDK model
          (3) an SDK exception raises ActionableError
          (4) empty content raises ActionableError
          (5) whitespace-only content raises ActionableError
    WHY: Replaces az rest POST .../threads plus GUID-resolution subprocess.

    MOCK BOUNDARY:
        Mock:  client.git.create_thread
        Real:  post_comment, model construction
        Never: N/A
    """

    def test_valid_content_returns_thread_id(self) -> None:
        """
        Given valid content
        When post_comment is called
        Then returns thread ID from SDK response
        """
        # Given: a mock client whose create_thread returns a thread with id
        client = Mock()
        response = Mock()
        response.id = 77
        client.git.create_thread.return_value = response

        # When: post_comment is called
        result = post_comment(client, "Repo", 42, "Great work!", "Proj")

        # Then: the thread ID is returned
        assert result == 77, f"Expected thread_id=77, got {result}"

    def test_custom_status_passed_to_sdk(self) -> None:
        """
        Given custom status
        When post_comment is called
        Then thread status matches
        """
        # Given: a mock client
        client = Mock()
        response = Mock()
        response.id = 10
        client.git.create_thread.return_value = response

        # When: called with status="closed"
        post_comment(client, "Repo", 42, "Closing note", "Proj", status="closed")

        # Then: the SDK model has the custom status
        call_args = client.git.create_thread.call_args
        thread_model = call_args[0][0]  # first positional arg
        assert thread_model.status == "closed", (
            f"Expected thread status='closed', got {thread_model.status!r}"
        )

    def test_sdk_exception_raises_actionable_error(self) -> None:
        """
        Given the SDK raises an exception
        When post_comment is called
        Then raises ActionableError
        """
        # Given: a client whose create_thread raises
        client = Mock()
        client.git.create_thread.side_effect = Exception("403 Forbidden")

        # When/Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            post_comment(client, "Repo", 42, "Test comment", "Proj")

        error_msg = str(exc_info.value)
        assert "403 Forbidden" in error_msg, (
            f"Expected SDK error in ActionableError, got: {error_msg}"
        )

    def test_empty_content_raises_actionable_error(self) -> None:
        """
        Given empty content
        When post_comment is called
        Then raises ActionableError (input validation)
        """
        # Given: a mock client (won't be called)
        client = Mock()

        # When/Then: ActionableError raised for empty content
        with pytest.raises(ActionableError) as exc_info:
            post_comment(client, "Repo", 42, "", "Proj")

        assert "content" in str(exc_info.value).lower(), (
            f"Expected 'content' in error message, got: {exc_info.value}"
        )

    def test_whitespace_only_content_raises_actionable_error(self) -> None:
        """
        Given whitespace-only content
        When post_comment is called
        Then raises ActionableError (input validation)
        """
        # Given: a mock client (won't be called)
        client = Mock()

        # When/Then: ActionableError raised for whitespace content
        with pytest.raises(ActionableError) as exc_info:
            post_comment(client, "Repo", 42, "   \n\t  ", "Proj")

        assert "content" in str(exc_info.value).lower(), (
            f"Expected 'content' in error message, got: {exc_info.value}"
        )


class TestReplyToComment:
    """
    REQUIREMENT: reply_to_comment() adds a reply to an existing comment thread.

    WHO: MCP tools replying to review feedback or continuing conversations.
    WHAT: (1) valid thread_id and content returns the new comment ID
          (2) an SDK exception raises ActionableError
          (3) empty content raises ActionableError
    WHY: Replaces az rest POST .../threads/{id}/comments plus
         GUID-resolution subprocess.

    MOCK BOUNDARY:
        Mock:  client.git.create_comment
        Real:  reply_to_comment, model construction
        Never: N/A
    """

    def test_valid_reply_returns_comment_id(self) -> None:
        """
        Given valid thread_id and content
        When reply_to_comment is called
        Then returns comment ID
        """
        # Given: a mock client whose create_comment returns a comment with id
        client = Mock()
        response = Mock()
        response.id = 5
        client.git.create_comment.return_value = response

        # When: reply_to_comment is called
        result = reply_to_comment(client, "Repo", 42, 77, "Thanks!", "Proj")

        # Then: the comment ID is returned
        assert result == 5, f"Expected comment_id=5, got {result}"

    def test_sdk_exception_raises_actionable_error(self) -> None:
        """
        Given the SDK raises an exception (e.g., thread not found)
        When reply_to_comment is called
        Then raises ActionableError
        """
        # Given: a client whose create_comment raises
        client = Mock()
        client.git.create_comment.side_effect = Exception("Thread 999 not found")

        # When/Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            reply_to_comment(client, "Repo", 42, 999, "Reply text", "Proj")

        error_msg = str(exc_info.value)
        assert "Thread 999 not found" in error_msg, (
            f"Expected SDK error in ActionableError, got: {error_msg}"
        )

    def test_empty_content_raises_actionable_error(self) -> None:
        """
        Given empty content
        When reply_to_comment is called
        Then raises ActionableError
        """
        # Given: a mock client (won't be called)
        client = Mock()

        # When/Then: ActionableError raised for empty content
        with pytest.raises(ActionableError) as exc_info:
            reply_to_comment(client, "Repo", 42, 77, "", "Proj")

        assert "content" in str(exc_info.value).lower(), (
            f"Expected 'content' in error message, got: {exc_info.value}"
        )


class TestResolveComments:
    """
    REQUIREMENT: resolve_comments() batch-resolves comment threads with
    partial-success reporting.

    WHO: MCP tools resolving addressed review comments in bulk.
    WHAT: (1) all resolvable threads appear in resolved with empty errors
          (2) partial failure puts successes in resolved and failures in errors
              with ActionableError containing context={"thread_id": tid}
          (3) a thread already in the target status appears in skipped
          (4) an empty thread list returns all-empty result lists
          (5) a custom status is passed through to the SDK model
          (6) all threads failing puts every error in errors with resolved=[]
    WHY: Preserves batch semantics and partial-success pattern. Replaces
         az rest PATCH .../threads/{id} per-thread subprocess calls. Surfacing
         structured errors (not just IDs) ensures callers can report meaningful
         diagnostics.

    MOCK BOUNDARY:
        Mock:  client.git.update_thread
        Real:  resolve_comments, batch iteration, ResolveResult construction
        Never: N/A
    """

    def test_all_threads_resolvable(self) -> None:
        """
        Given 3 thread IDs all resolvable
        When resolve_comments is called
        Then resolved=[1,2,3], errors=[], skipped=[]
        """
        # Given: a mock client where update_thread succeeds for all
        # and get_threads returns threads with non-matching statuses
        client = Mock()
        threads: list[Mock] = []
        for tid in (1, 2, 3):
            t = Mock()
            t.id = tid
            t.status = "active"  # not yet "fixed"
            threads.append(t)
        client.git.get_threads.return_value = threads
        response = Mock()
        response.status = "fixed"
        client.git.update_thread.return_value = response

        # When: called with 3 thread IDs
        result = resolve_comments(client, "Repo", 42, [1, 2, 3], "Proj")

        # Then: all threads are in resolved
        assert result.resolved == [1, 2, 3], f"Expected resolved=[1,2,3], got {result.resolved}"
        assert result.errors == [], f"Expected errors=[], got {result.errors}"
        assert result.skipped == [], f"Expected skipped=[], got {result.skipped}"

    def test_partial_failure(self) -> None:
        """
        Given 1 of 3 threads fails
        When resolve_comments is called
        Then partial result: resolved=[1,3], errors contains one ActionableError
             with context={"thread_id": 2}
        """
        # Given: a mock client where thread 2 raises on update
        client = Mock()
        threads: list[Mock] = []
        for tid in (1, 2, 3):
            t = Mock()
            t.id = tid
            t.status = "active"
            threads.append(t)
        client.git.get_threads.return_value = threads
        success_response = Mock()
        success_response.status = "fixed"
        client.git.update_thread.side_effect = [
            success_response,
            Exception("Thread 2 locked"),
            success_response,
        ]

        # When: called with 3 thread IDs
        result = resolve_comments(client, "Repo", 42, [1, 2, 3], "Proj")

        # Then: thread 2 is in errors, others in resolved
        assert result.resolved == [1, 3], f"Expected resolved=[1,3], got {result.resolved}"
        assert len(result.errors) == 1, f"Expected 1 error, got {len(result.errors)}"
        assert isinstance(result.errors[0], ActionableError), (
            f"Expected ActionableError, got {type(result.errors[0])}"
        )
        assert result.errors[0].context == {"thread_id": 2}, (
            f"Expected context with thread_id=2, got {result.errors[0].context}"
        )
        assert result.skipped == [], f"Expected skipped=[], got {result.skipped}"

    def test_thread_already_in_target_status_is_skipped(self) -> None:
        """
        Given a thread already in target status
        When resolve_comments is called
        Then it appears in skipped
        """
        # Given: a mock client where thread returns the target status already
        client = Mock()
        already_fixed = Mock()
        already_fixed.status = "fixed"
        # When the thread is already in the target status before we call
        # update, the SDK still returns the thread — but it was already there.
        # We detect this via a pre-check using get_threads or by the SDK
        # returning the same status. Implementation detail: the production
        # code fetches thread status first and skips if already matching.
        client.git.get_threads.return_value = [already_fixed]
        already_fixed.id = 10

        new_response = Mock()
        new_response.status = "fixed"
        client.git.update_thread.return_value = new_response

        # When: called with that thread ID
        result = resolve_comments(client, "Repo", 42, [10], "Proj")

        # Then: thread is in skipped
        assert 10 in result.skipped, (
            f"Expected thread 10 in skipped, got resolved={result.resolved}, "
            f"skipped={result.skipped}"
        )

    def test_empty_thread_list(self) -> None:
        """
        Given empty thread list
        When resolve_comments is called
        Then all lists empty
        """
        # Given: a mock client
        client = Mock()

        # When: called with no thread IDs
        result = resolve_comments(client, "Repo", 42, [], "Proj")

        # Then: all result lists are empty
        assert result.resolved == [], f"Expected resolved=[], got {result.resolved}"
        assert result.errors == [], f"Expected errors=[], got {result.errors}"
        assert result.skipped == [], f"Expected skipped=[], got {result.skipped}"

    def test_custom_status_passed_to_sdk(self) -> None:
        """
        Given custom status "wontFix"
        When resolve_comments is called
        Then SDK called with that status
        """
        # Given: a mock client with thread not yet in target status
        client = Mock()
        t = Mock()
        t.id = 1
        t.status = "active"
        client.git.get_threads.return_value = [t]
        response = Mock()
        response.status = "wontFix"
        client.git.update_thread.return_value = response

        # When: called with status="wontFix"
        resolve_comments(client, "Repo", 42, [1], "Proj", status="wontFix")

        # Then: update_thread received the custom status
        call_args = client.git.update_thread.call_args
        thread_model = call_args[0][0]  # first positional arg
        assert thread_model.status == "wontFix", (
            f"Expected status='wontFix' in SDK model, got {thread_model.status!r}"
        )

    def test_all_threads_fail(self) -> None:
        """
        Given all threads fail
        When resolve_comments is called
        Then resolved=[], errors contains three ActionableError instances
             with corresponding thread_ids in context
        """
        # Given: a mock client where every update_thread raises
        client = Mock()
        threads: list[Mock] = []
        for tid in (1, 2, 3):
            t = Mock()
            t.id = tid
            t.status = "active"
            threads.append(t)
        client.git.get_threads.return_value = threads
        client.git.update_thread.side_effect = Exception("Service unavailable")

        # When: called with 3 thread IDs
        result = resolve_comments(client, "Repo", 42, [1, 2, 3], "Proj")

        # Then: all threads in errors, none in resolved
        assert result.resolved == [], f"Expected resolved=[], got {result.resolved}"
        assert len(result.errors) == 3, f"Expected 3 errors, got {len(result.errors)}"
        for err in result.errors:
            assert err.context is not None, "Expected context to be set on each error"
        error_thread_ids = [e.context["thread_id"] for e in result.errors if e.context is not None]
        assert error_thread_ids == [1, 2, 3], (
            f"Expected thread_ids [1,2,3], got {error_thread_ids}"
        )

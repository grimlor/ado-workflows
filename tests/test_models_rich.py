"""
BDD tests for rich comment models — CommentSeverity, CommentType, RichComment,
RichPostingResult, PostedCommentDetail.

Covers:
- TestCommentSeverityEnum: CommentSeverity values and membership
- TestCommentTypeEnum: CommentType values and membership
- TestRichCommentConstruction: RichComment frozen dataclass with full metadata
- TestRichPostingResultConstruction: RichPostingResult result container
- TestPostedCommentDetailConstruction: PostedCommentDetail for successful posts

Public API surface (from src/ado_workflows/models.py):
    CommentSeverity(Enum): INFO, SUGGESTION, WARNING, ERROR, CRITICAL
    CommentType(Enum): GENERAL, LINE_COMMENT, FILE_COMMENT, SUGGESTION, SECURITY, PERFORMANCE
    RichComment(comment_id, title, content, comment_type=GENERAL, severity=INFO,
                file_path=None, line_number=None, suggested_code=None,
                reasoning=None, business_impact=None, tags=[], status="active",
                parent_thread_id=None)
    RichPostingResult(posted, failures, skipped, dry_run, local_praise)
    PostedCommentDetail(thread_id, comment_id, title, file_path, line_number)
"""

from __future__ import annotations

import pytest
from actionable_errors import ActionableError

from ado_workflows.models import (
    CommentSeverity,
    CommentType,
    PostedCommentDetail,
    RichComment,
    RichPostingResult,
)


class TestCommentSeverityEnum:
    """
    REQUIREMENT: CommentSeverity enum defines five severity levels for review comments.

    WHO: RichComment.severity field; formatting logic for severity icons.
    WHAT: (1) five members exist: INFO, SUGGESTION, WARNING, ERROR, CRITICAL
          (2) each member has the expected string value
          (3) members are constructible from their string values
    WHY: Severity drives formatting (icon selection) and filtering (praise detection
         skips ERROR/CRITICAL). Undefined members would silently pass through formatting.

    MOCK BOUNDARY:
        Mock:  nothing — enum construction
        Real:  CommentSeverity
        Never: N/A
    """

    def test_all_five_members_exist_with_correct_values(self) -> None:
        """
        Given the CommentSeverity enum
        When all members are accessed
        Then five members exist with expected string values
        """
        # Given: the enum class
        # When: accessing each member
        # Then: values match
        assert CommentSeverity.INFO.value == "info", "INFO should have value 'info'"
        assert CommentSeverity.SUGGESTION.value == "suggestion", (
            "SUGGESTION should have value 'suggestion'"
        )
        assert CommentSeverity.WARNING.value == "warning", "WARNING should have value 'warning'"
        assert CommentSeverity.ERROR.value == "error", "ERROR should have value 'error'"
        assert CommentSeverity.CRITICAL.value == "critical", (
            "CRITICAL should have value 'critical'"
        )

    def test_constructible_from_string_values(self) -> None:
        """
        Given valid severity string values
        When constructing CommentSeverity from each value
        Then the correct enum member is returned
        """
        # Given: valid string values
        values = ["info", "suggestion", "warning", "error", "critical"]

        for value in values:
            # When: constructing from string
            severity = CommentSeverity(value)

            # Then: valid enum member
            assert isinstance(severity, CommentSeverity), (
                f"'{value}' should produce a CommentSeverity member"
            )

    def test_invalid_value_raises_value_error(self) -> None:
        """
        Given an invalid severity string
        When constructing CommentSeverity
        Then ValueError is raised
        """
        # Given: an invalid string
        # When / Then: construction fails
        with pytest.raises(ValueError, match="not_a_severity"):
            CommentSeverity("not_a_severity")


class TestCommentTypeEnum:
    """
    REQUIREMENT: CommentType enum defines six semantic types for review comments.

    WHO: RichComment.comment_type field; formatting logic for type-specific headers.
    WHAT: (1) six members exist: GENERAL, LINE_COMMENT, FILE_COMMENT, SUGGESTION, SECURITY, PERFORMANCE
          (2) each member has the expected string value
          (3) members are constructible from their string values
    WHY: Comment type drives formatting headers (e.g., "SECURITY:", "PERFORMANCE:").
         Missing or incorrect types produce wrong headers in ADO comments.

    MOCK BOUNDARY:
        Mock:  nothing — enum construction
        Real:  CommentType
        Never: N/A
    """

    def test_all_six_members_exist_with_correct_values(self) -> None:
        """
        Given the CommentType enum
        When all members are accessed
        Then six members exist with expected string values
        """
        # Given: the enum class
        # When: accessing each member
        # Then: values match
        assert CommentType.GENERAL.value == "general", "GENERAL should have value 'general'"
        assert CommentType.LINE_COMMENT.value == "line", "LINE_COMMENT should have value 'line'"
        assert CommentType.FILE_COMMENT.value == "file", "FILE_COMMENT should have value 'file'"
        assert CommentType.SUGGESTION.value == "suggestion", (
            "SUGGESTION should have value 'suggestion'"
        )
        assert CommentType.SECURITY.value == "security", "SECURITY should have value 'security'"
        assert CommentType.PERFORMANCE.value == "performance", (
            "PERFORMANCE should have value 'performance'"
        )

    def test_constructible_from_string_values(self) -> None:
        """
        Given valid type string values
        When constructing CommentType from each value
        Then the correct enum member is returned
        """
        # Given: valid string values
        values = ["general", "line", "file", "suggestion", "security", "performance"]

        for value in values:
            # When: constructing from string
            comment_type = CommentType(value)

            # Then: valid enum member
            assert isinstance(comment_type, CommentType), (
                f"'{value}' should produce a CommentType member"
            )


class TestRichCommentConstruction:
    """
    REQUIREMENT: RichComment frozen dataclass carries all metadata for formatting and filtering.

    WHO: Library consumers, MCP server, downstream domain servers.
    WHAT: (1) all fields are accessible with correct values when fully specified
          (2) defaults are applied when only required fields are given
              (GENERAL type, INFO severity, empty tags, "active" status, None optionals)
          (3) file_path without line_number succeeds (validation is at post time)
          (4) instances are frozen (immutable)
    WHY: The existing CommentPayload only carries content/file_path/line_number/status.
         Rich comments need metadata for formatting, severity-based filtering, and
         self-praise detection.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  RichComment
        Never: N/A
    """

    def test_all_fields_accessible_with_correct_values(self) -> None:
        """
        Given full field values for a rich comment
        When RichComment is constructed
        Then all fields are accessible with correct values
        """
        # Given: full field values
        comment = RichComment(
            comment_id="sec-001",
            title="SQL Injection Risk",
            content="User input is interpolated directly into the query.",
            comment_type=CommentType.SECURITY,
            severity=CommentSeverity.ERROR,
            file_path="src/db/queries.py",
            line_number=42,
            suggested_code='cursor.execute("SELECT * FROM t WHERE id = %s", (user_id,))',
            reasoning="Parameterized queries prevent SQL injection.",
            business_impact="Data breach risk if exploited.",
            tags=["security", "sql-injection"],
            status="active",
            parent_thread_id=99,
        )

        # Then: all fields match
        assert comment.comment_id == "sec-001", "comment_id should be 'sec-001'"
        assert comment.title == "SQL Injection Risk", "title should match"
        assert comment.content == "User input is interpolated directly into the query.", (
            "content should match"
        )
        assert comment.comment_type == CommentType.SECURITY, "comment_type should be SECURITY"
        assert comment.severity == CommentSeverity.ERROR, "severity should be ERROR"
        assert comment.file_path == "src/db/queries.py", "file_path should match"
        assert comment.line_number == 42, "line_number should be 42"
        assert comment.suggested_code is not None, "suggested_code should be set"
        assert comment.reasoning == "Parameterized queries prevent SQL injection.", (
            "reasoning should match"
        )
        assert comment.business_impact == "Data breach risk if exploited.", (
            "business_impact should match"
        )
        assert comment.tags == ["security", "sql-injection"], "tags should match"
        assert comment.status == "active", "status should be 'active'"
        assert comment.parent_thread_id == 99, "parent_thread_id should be 99"

    def test_defaults_applied_with_only_required_fields(self) -> None:
        """
        Given only required fields (comment_id, title, content)
        When RichComment is constructed
        Then defaults are applied: GENERAL type, INFO severity, empty tags, "active" status
        """
        # Given: only required fields
        comment = RichComment(
            comment_id="gen-001",
            title="Consider renaming",
            content="This variable name is unclear.",
        )

        # Then: defaults applied
        assert comment.comment_type == CommentType.GENERAL, (
            "default comment_type should be GENERAL"
        )
        assert comment.severity == CommentSeverity.INFO, "default severity should be INFO"
        assert comment.file_path is None, "default file_path should be None"
        assert comment.line_number is None, "default line_number should be None"
        assert comment.suggested_code is None, "default suggested_code should be None"
        assert comment.reasoning is None, "default reasoning should be None"
        assert comment.business_impact is None, "default business_impact should be None"
        assert comment.tags == [], "default tags should be empty list"
        assert comment.status == "active", "default status should be 'active'"
        assert comment.parent_thread_id is None, "default parent_thread_id should be None"

    def test_file_path_without_line_number_succeeds(self) -> None:
        """
        Given file_path is set but line_number is None
        When RichComment is constructed
        Then construction succeeds (validation is at post time, not model time)
        """
        # Given / When: file_path without line_number
        comment = RichComment(
            comment_id="file-001",
            title="File-level concern",
            content="This file has too many responsibilities.",
            file_path="src/monolith.py",
        )

        # Then: construction succeeded
        assert comment.file_path == "src/monolith.py", "file_path should be set"
        assert comment.line_number is None, "line_number should be None"

    def test_frozen_instances_are_immutable(self) -> None:
        """
        Given a constructed RichComment
        When attempting to modify a field
        Then FrozenInstanceError is raised
        """
        # Given: a constructed comment
        comment = RichComment(
            comment_id="imm-001",
            title="Test",
            content="Test content",
        )

        # When / Then: modification is rejected
        with pytest.raises(AttributeError):
            comment.title = "Modified"  # type: ignore[misc]


class TestRichPostingResultConstruction:
    """
    REQUIREMENT: RichPostingResult contains posting outcomes plus self-praise filtering results.

    WHO: post_rich_comments return value; MCP server tool return type.
    WHAT: (1) all fields are accessible with correct values
          (2) local_praise holds RichComment instances filtered out by self-praise logic
          (3) posted holds PostedCommentDetail instances for successes
    WHY: Consumers need to know which comments were posted, which failed, which were
         skipped (dry-run), and which were diverted to local-only praise display.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  RichPostingResult, PostedCommentDetail, RichComment
        Never: N/A
    """

    def test_all_fields_accessible_with_correct_values(self) -> None:
        """
        Given a RichPostingResult with posted, failures, skipped, and local_praise
        When accessing fields
        Then all values are correct
        """
        # Given: result components
        posted_detail = PostedCommentDetail(
            thread_id=100,
            comment_id="c-001",
            title="Fix typo",
            file_path="README.md",
            line_number=5,
        )
        praise_comment = RichComment(
            comment_id="p-001",
            title="Great work!",
            content="Excellent implementation.",
        )
        failure = ActionableError.validation(
            service="AzureDevOps",
            field_name="content",
            reason="Empty content",
        )

        result = RichPostingResult(
            posted=[posted_detail],
            failures=[failure],
            skipped=[2, 3],
            dry_run=False,
            local_praise=[praise_comment],
        )

        # Then: all fields match
        assert len(result.posted) == 1, "should have 1 posted comment"
        assert result.posted[0].thread_id == 100, "thread_id should be 100"
        assert result.posted[0].comment_id == "c-001", "comment_id should be 'c-001'"
        assert len(result.failures) == 1, "should have 1 failure"
        assert result.skipped == [2, 3], "skipped indices should be [2, 3]"
        assert result.dry_run is False, "dry_run should be False"
        assert len(result.local_praise) == 1, "should have 1 local praise comment"
        assert result.local_praise[0].comment_id == "p-001", "praise comment_id should be 'p-001'"


class TestPostedCommentDetailConstruction:
    """
    REQUIREMENT: PostedCommentDetail records per-comment success metadata.

    WHO: RichPostingResult.posted field.
    WHAT: (1) all fields are accessible with correct values
          (2) file_path and line_number can be None (PR-level comments)
          (3) instances are frozen
    WHY: Consumers need thread_id for follow-up operations (replies, resolution)
         and comment_id for tracing back to the original RichComment.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  PostedCommentDetail
        Never: N/A
    """

    def test_all_fields_accessible(self) -> None:
        """
        Given field values for a file-positioned comment
        When PostedCommentDetail is constructed
        Then all fields are accessible
        """
        # Given / When
        detail = PostedCommentDetail(
            thread_id=42,
            comment_id="det-001",
            title="Use parameterized query",
            file_path="src/db.py",
            line_number=10,
        )

        # Then
        assert detail.thread_id == 42, "thread_id should be 42"
        assert detail.comment_id == "det-001", "comment_id should match"
        assert detail.title == "Use parameterized query", "title should match"
        assert detail.file_path == "src/db.py", "file_path should match"
        assert detail.line_number == 10, "line_number should be 10"

    def test_none_file_path_and_line_number_for_pr_level_comment(self) -> None:
        """
        Given a PR-level comment with no file positioning
        When PostedCommentDetail is constructed with None file_path and line_number
        Then construction succeeds with None values
        """
        # Given / When
        detail = PostedCommentDetail(
            thread_id=43,
            comment_id="det-002",
            title="General feedback",
            file_path=None,
            line_number=None,
        )

        # Then
        assert detail.file_path is None, "file_path should be None"
        assert detail.line_number is None, "line_number should be None"

    def test_frozen_instances_are_immutable(self) -> None:
        """
        Given a constructed PostedCommentDetail
        When attempting to modify a field
        Then FrozenInstanceError is raised
        """
        # Given
        detail = PostedCommentDetail(
            thread_id=44,
            comment_id="det-003",
            title="Test",
            file_path=None,
            line_number=None,
        )

        # When / Then
        with pytest.raises(AttributeError):
            detail.thread_id = 999  # type: ignore[misc]

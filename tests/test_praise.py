"""
BDD tests for ado_workflows.praise — self-praise detection and filtering.

Covers:
- TestIsPraiseComment: heuristic detection of praise comments
- TestFilterSelfPraise: partitioning comments by self-praise logic

Public API surface (from src/ado_workflows/praise.py):
    is_praise_comment(comment: RichComment) -> bool
    filter_self_praise(comments: list[RichComment], pr_author: UserIdentity,
                       current_user: UserIdentity) -> tuple[list[RichComment], list[RichComment]]
"""

from __future__ import annotations

from ado_workflows.models import (
    CommentSeverity,
    RichComment,
    UserIdentity,
)
from ado_workflows.praise import filter_self_praise, is_praise_comment


class TestIsPraiseComment:
    """
    REQUIREMENT: Heuristically detect praise comments based on content analysis.

    WHO: Self-praise filtering logic, any consumer wanting to classify comments.
    WHAT: (1) comment with "excellent work" in title returns True
          (2) comment with "great job, well done" in content returns True
          (3) comment with technical criticism ("buffer overflow") returns False
          (4) short comment with 2+ positive adjectives returns True
          (5) long comment (100+ words) with only 2 positive words returns False (diluted)
          (6) comment with severity ERROR returns False (critical feedback is not praise)
          (7) comment with severity CRITICAL returns False (same bypass as ERROR)
          (8) plain non-praise comment with normal severity and no positive words returns False
    WHY: Prevents AI agents from posting self-congratulatory comments when
         reviewing their own PRs.

    MOCK BOUNDARY:
        Mock:  nothing — pure function
        Real:  is_praise_comment, regex matching, word counting
        Never: N/A
    """

    def test_excellent_work_in_title_is_praise(self) -> None:
        """
        Given a comment with "excellent work" in the title
        When checking with is_praise_comment
        Then returns True
        """
        # Given: praise in title
        comment = RichComment(
            comment_id="p-001",
            title="Excellent work on this refactor",
            content="The code is much cleaner now.",
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: detected as praise
        assert result is True, "Comment with 'excellent work' in title should be praise"

    def test_great_job_well_done_in_content_is_praise(self) -> None:
        """
        Given a comment with "great job, well done" in the content
        When checking with is_praise_comment
        Then returns True
        """
        # Given: praise in content
        comment = RichComment(
            comment_id="p-002",
            title="Review Summary",
            content="Great job on this implementation, well done!",
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: detected as praise
        assert result is True, "Comment with 'great job, well done' should be praise"

    def test_technical_criticism_is_not_praise(self) -> None:
        """
        Given a comment about a buffer overflow vulnerability
        When checking with is_praise_comment
        Then returns False
        """
        # Given: technical criticism
        comment = RichComment(
            comment_id="p-003",
            title="Buffer Overflow Risk",
            content="This will cause a buffer overflow when input exceeds 256 bytes.",
            severity=CommentSeverity.ERROR,
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: not praise
        assert result is False, "Technical criticism should not be detected as praise"

    def test_short_comment_with_multiple_positive_adjectives_is_praise(self) -> None:
        """
        Given a short comment with 2+ positive adjectives
        When checking with is_praise_comment
        Then returns True
        """
        # Given: short comment with multiple positive words
        comment = RichComment(
            comment_id="p-004",
            title="Code Quality",
            content="This is a clean and elegant solution.",
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: detected as praise
        assert result is True, "Short comment with multiple positive adjectives should be praise"

    def test_long_comment_with_few_positive_words_is_not_praise(self) -> None:
        """
        Given a long comment (100+ words) with only 2 positive words
        When checking with is_praise_comment
        Then returns False (positive words are diluted)
        """
        # Given: long technical comment with a couple of positive words
        filler = " ".join(["word"] * 100)
        comment = RichComment(
            comment_id="p-005",
            title="Detailed Analysis",
            content=f"This code has a good structure. {filler} The clean architecture helps.",
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: not praise (diluted)
        assert result is False, "Long comment with diluted positive words should not be praise"

    def test_error_severity_is_not_praise(self) -> None:
        """
        Given a comment with severity ERROR
        When checking with is_praise_comment
        Then returns False (critical feedback is not praise)
        """
        # Given: error-severity comment with some positive words
        comment = RichComment(
            comment_id="p-006",
            title="Good attempt but critical issue",
            content="Great effort, but this introduces a regression.",
            severity=CommentSeverity.ERROR,
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: not praise (ERROR severity overrides)
        assert result is False, "ERROR severity comments should not be classified as praise"

    def test_critical_severity_is_not_praise(self) -> None:
        """
        Given a comment with severity CRITICAL and praise-like words
        When checking with is_praise_comment
        Then returns False (CRITICAL feedback is never suppressed)
        """
        # Given: critical-severity comment with praise words
        comment = RichComment(
            comment_id="p-007",
            title="Excellent design, but data loss risk",
            content="Great architecture, however this will corrupt the database.",
            severity=CommentSeverity.CRITICAL,
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: not praise (CRITICAL severity overrides)
        assert result is False, "CRITICAL severity comments should not be classified as praise"

    def test_plain_non_praise_with_normal_severity_returns_false(self) -> None:
        """
        Given a comment with INFO severity, no praise patterns, and no positive adjectives
        When checking with is_praise_comment
        Then returns False
        """
        # Given: plain technical comment, no praise words at all
        comment = RichComment(
            comment_id="p-008",
            title="Missing null check",
            content="The variable can be None here but is accessed without a guard.",
            severity=CommentSeverity.INFO,
        )

        # When: checking
        result = is_praise_comment(comment)

        # Then: not praise
        assert result is False, (
            "Plain technical comment with no positive words should not be praise"
        )


class TestFilterSelfPraise:
    """
    REQUIREMENT: Partition comments into to-post and local-only when the current
    user is the PR author.

    WHO: post_rich_comments, any consumer wanting pre-post filtering.
    WHAT: (1) same user.id → praise goes to local_praise, non-praise to to_post
          (2) different user.id → all go to to_post, local_praise empty
          (3) same user but no praise comments → all go to to_post
          (4) same user and all praise → all go to local_praise
    WHY: Posting "great work!" on your own PR is awkward. Moving praise to
         local display preserves feedback while avoiding self-congratulation.

    MOCK BOUNDARY:
        Mock:  nothing — pure function
        Real:  filter_self_praise, UserIdentity.id comparison, is_praise_comment
        Never: never mock UserIdentity construction
    """

    def test_same_user_praise_goes_to_local(self) -> None:
        """
        Given same user.id for PR author and current user
        When filtering a mix of praise and non-praise comments
        Then praise comments go to local_praise, non-praise to to_post
        """
        # Given: same user
        author = UserIdentity(display_name="Alice", id="user-guid-001")
        current = UserIdentity(display_name="Alice", id="user-guid-001")

        praise = RichComment(
            comment_id="f-001",
            title="Great work!",
            content="Excellent implementation.",
        )
        criticism = RichComment(
            comment_id="f-002",
            title="Bug",
            content="This has a null reference.",
            severity=CommentSeverity.ERROR,
        )

        # When: filtering
        to_post, local_praise = filter_self_praise([praise, criticism], author, current)

        # Then: praise filtered, criticism kept
        assert len(to_post) == 1, "One non-praise comment should be in to_post"
        assert to_post[0].comment_id == "f-002", "Criticism should be in to_post"
        assert len(local_praise) == 1, "One praise comment should be in local_praise"
        assert local_praise[0].comment_id == "f-001", "Praise should be in local_praise"

    def test_different_user_all_go_to_post(self) -> None:
        """
        Given different user.id for PR author and current user
        When filtering comments
        Then all comments go to to_post, local_praise is empty
        """
        # Given: different users
        author = UserIdentity(display_name="Alice", id="user-guid-001")
        current = UserIdentity(display_name="Bob", id="user-guid-002")

        praise = RichComment(
            comment_id="f-003",
            title="Great work!",
            content="Excellent implementation.",
        )
        criticism = RichComment(
            comment_id="f-004",
            title="Bug",
            content="This has a null reference.",
            severity=CommentSeverity.ERROR,
        )

        # When: filtering
        to_post, local_praise = filter_self_praise([praise, criticism], author, current)

        # Then: all go to to_post
        assert len(to_post) == 2, "Both comments should be in to_post for different users"
        assert len(local_praise) == 0, "local_praise should be empty for different users"

    def test_same_user_no_praise_all_go_to_post(self) -> None:
        """
        Given same user.id but no praise comments
        When filtering
        Then all go to to_post, local_praise is empty
        """
        # Given: same user, only criticism
        author = UserIdentity(display_name="Alice", id="user-guid-001")
        current = UserIdentity(display_name="Alice", id="user-guid-001")

        criticism1 = RichComment(
            comment_id="f-005",
            title="Bug",
            content="Null reference risk.",
            severity=CommentSeverity.ERROR,
        )
        criticism2 = RichComment(
            comment_id="f-006",
            title="Performance",
            content="O(n^2) loop detected.",
            severity=CommentSeverity.WARNING,
        )

        # When: filtering
        to_post, local_praise = filter_self_praise([criticism1, criticism2], author, current)

        # Then: all go to to_post
        assert len(to_post) == 2, "All non-praise should be in to_post"
        assert len(local_praise) == 0, "local_praise should be empty when no praise exists"

    def test_same_user_all_praise_all_go_to_local(self) -> None:
        """
        Given same user.id and all comments are praise
        When filtering
        Then all go to local_praise, to_post is empty
        """
        # Given: same user, all praise
        author = UserIdentity(display_name="Alice", id="user-guid-001")
        current = UserIdentity(display_name="Alice", id="user-guid-001")

        praise1 = RichComment(
            comment_id="f-007",
            title="Excellent work!",
            content="Very clean code.",
        )
        praise2 = RichComment(
            comment_id="f-008",
            title="Great job!",
            content="Well done on this refactor.",
        )

        # When: filtering
        to_post, local_praise = filter_self_praise([praise1, praise2], author, current)

        # Then: all go to local_praise
        assert len(to_post) == 0, "to_post should be empty when all are praise on own PR"
        assert len(local_praise) == 2, "All praise should be in local_praise"

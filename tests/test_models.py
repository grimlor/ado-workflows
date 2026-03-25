"""
BDD tests for ado_workflows.models — domain types.

Covers:
- TestReviewerInfoConstruction: ReviewerInfo dataclass with reviewer identity and vote metadata
- TestPendingPRConstruction: PendingPR dataclass with PR metadata for review reminders
- TestApprovalStatusConstruction: ApprovalStatus with typed VoteStatus lists
- TestReviewStatusConstruction: ReviewStatus with nested ApprovalStatus
- TestCommentSummaryConstruction: CommentSummary thread count statistics
- TestAuthorSampleConstruction: AuthorSample single-author comment activity
- TestCommentInfoConstruction: CommentInfo single comment with context
- TestCommentAnalysisConstruction: CommentAnalysis full comment analysis result

Public API surface (from src/ado_workflows/models.py):
    VOTE_TEXT: dict[int, str]
    ReviewerInfo(display_name, unique_name, vote, is_required, is_container)
    VoteStatus(name, email, vote, vote_text, vote_invalidated, invalidated_by_commit,
               is_container, voted_for_ids, reviewer_id)
    PendingPR(pr_id, title, author, creation_date, repository, organization, project,
              web_url, pending_reviewers, days_open, merge_status, has_conflicts,
              needs_approvals_count=0, valid_approvals_count=0)
    ApprovalStatus(is_approved, needs_approvals_count, has_rejection,
                   valid_approvers, invalidated_approvers, rejecting_reviewers,
                   waiting_reviewers, pending_reviewers)
    ReviewStatus(pr_id, title, author, url, days_open, last_commit_date,
                 approval_status, summary)
    CommentSummary(total_threads, active_threads, fixed_threads, active_percentage)
    AuthorSample(count, latest_comment, latest_status)
    CommentInfo(thread_id, thread_status, author, content_preview, full_content,
                created_date, is_deleted, file_path, line_start, line_end)
    CommentAnalysis(pr_id, comment_summary, comment_authors, author_samples,
                    active_comments, resolution_ready)
"""

from __future__ import annotations

from datetime import datetime

from ado_workflows.models import (
    ApprovalStatus,
    AuthorSample,
    CommentAnalysis,
    CommentInfo,
    CommentSummary,
    PendingPR,
    ReviewerInfo,
    ReviewStatus,
    VoteStatus,
)


class TestReviewerInfoConstruction:
    """
    REQUIREMENT: ReviewerInfo dataclass holds reviewer identity and vote metadata.

    WHO: PendingPR.pending_reviewers field; Phase 6c reminder logic.
    WHAT: (1) five fields (display_name, unique_name, vote, is_required,
              is_container) are accessible with correct values
          (2) a container reviewer has is_container=True
    WHY: Without a typed container, reviewer data would be passed as untyped dicts,
         making field access error-prone and undiscoverable.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  ReviewerInfo
        Never: N/A
    """

    def test_all_fields_accessible_with_correct_values(self) -> None:
        """
        Given field values for a required individual reviewer
        When ReviewerInfo is constructed
        Then all five fields are accessible with correct values
        """
        # Given: field values for a required individual reviewer
        info = ReviewerInfo(
            display_name="Alice Smith",
            unique_name="alice@contoso.com",
            vote=10,
            is_required=True,
            is_container=False,
        )

        # When / Then: all fields are accessible with correct values
        assert info.display_name == "Alice Smith", (
            f"Expected display_name 'Alice Smith', got '{info.display_name}'"
        )
        assert info.unique_name == "alice@contoso.com", (
            f"Expected unique_name 'alice@contoso.com', got '{info.unique_name}'"
        )
        assert info.vote == 10, f"Expected vote 10, got {info.vote}"
        assert info.is_required is True, f"Expected is_required True, got {info.is_required}"
        assert info.is_container is False, f"Expected is_container False, got {info.is_container}"

    def test_container_reviewer_has_is_container_true(self) -> None:
        """
        Given is_container=True
        When ReviewerInfo is constructed
        Then is_container is True
        """
        # Given: a team/container reviewer
        info = ReviewerInfo(
            display_name="Payments Team",
            unique_name="payments-team@contoso.com",
            vote=0,
            is_required=True,
            is_container=True,
        )

        # When / Then: is_container reflects the team reviewer status
        assert info.is_container is True, (
            f"Expected is_container True for team reviewer, got {info.is_container}"
        )


class TestPendingPRConstruction:
    """
    REQUIREMENT: PendingPR dataclass holds PR metadata for review reminders.

    WHO: Phase 6c send_pr_review_reminders().
    WHAT: (1) all required fields plus a nested list[ReviewerInfo] for
              pending_reviewers are accessible with correct values
          (2) needs_approvals_count and valid_approvals_count default to 0
    WHY: Without a typed container, PR data would be passed as untyped dicts,
         making field access error-prone and the approval count defaults implicit.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  PendingPR, ReviewerInfo
        Never: N/A
    """

    def test_all_required_fields_plus_reviewerinfo_list(self) -> None:
        """
        Given all required fields and a ReviewerInfo list
        When PendingPR is constructed
        Then all fields are accessible with correct values
        """
        # Given: a ReviewerInfo for the pending reviewers list
        reviewer = ReviewerInfo(
            display_name="Bob Jones",
            unique_name="bob@contoso.com",
            vote=0,
            is_required=True,
            is_container=False,
        )
        creation = datetime(2026, 3, 1, 12, 0, 0)

        # When: PendingPR is constructed with all required fields
        pr = PendingPR(
            pr_id=42,
            title="Add payment validation",
            author="Alice Smith",
            creation_date=creation,
            repository="PaymentsRepo",
            organization="ContosoOrg",
            project="Payments",
            web_url="https://dev.azure.com/ContosoOrg/Payments/_git/PaymentsRepo/pullrequest/42",
            pending_reviewers=[reviewer],
            days_open=3,
            merge_status="succeeded",
            has_conflicts=False,
        )

        # Then: all fields are accessible with correct values
        assert pr.pr_id == 42, f"Expected pr_id 42, got {pr.pr_id}"
        assert pr.title == "Add payment validation", (
            f"Expected title 'Add payment validation', got '{pr.title}'"
        )
        assert pr.author == "Alice Smith", f"Expected author 'Alice Smith', got '{pr.author}'"
        assert pr.creation_date == creation, (
            f"Expected creation_date {creation}, got {pr.creation_date}"
        )
        assert pr.repository == "PaymentsRepo", (
            f"Expected repository 'PaymentsRepo', got '{pr.repository}'"
        )
        assert pr.organization == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{pr.organization}'"
        )
        assert pr.project == "Payments", f"Expected project 'Payments', got '{pr.project}'"
        assert pr.web_url.endswith("/pullrequest/42"), (
            f"Expected web_url to end with '/pullrequest/42', got '{pr.web_url}'"
        )
        assert pr.pending_reviewers == [reviewer], (
            f"Expected pending_reviewers to contain one reviewer, got {pr.pending_reviewers}"
        )
        assert pr.days_open == 3, f"Expected days_open 3, got {pr.days_open}"
        assert pr.merge_status == "succeeded", (
            f"Expected merge_status 'succeeded', got '{pr.merge_status}'"
        )
        assert pr.has_conflicts is False, f"Expected has_conflicts False, got {pr.has_conflicts}"

    def test_defaults_for_approval_counts(self) -> None:
        """
        Given only required fields
        When PendingPR is constructed
        Then needs_approvals_count=0 and valid_approvals_count=0
        """
        # Given: only required fields (no approval counts specified)
        pr = PendingPR(
            pr_id=99,
            title="Fix typo",
            author="Charlie Brown",
            creation_date=datetime(2026, 3, 4, 8, 0, 0),
            repository="DocsRepo",
            organization="ContosoOrg",
            project="Docs",
            web_url="https://dev.azure.com/ContosoOrg/Docs/_git/DocsRepo/pullrequest/99",
            pending_reviewers=[],
            days_open=1,
            merge_status="succeeded",
            has_conflicts=False,
        )

        # When / Then: defaults are applied
        assert pr.needs_approvals_count == 0, (
            f"Expected needs_approvals_count default 0, got {pr.needs_approvals_count}"
        )
        assert pr.valid_approvals_count == 0, (
            f"Expected valid_approvals_count default 0, got {pr.valid_approvals_count}"
        )


# ---------------------------------------------------------------------------
# Phase 6c dataclasses
# ---------------------------------------------------------------------------


def _make_vote_status(
    *,
    name: str = "Alice Smith",
    vote: int = 10,
    vote_text: str = "Approved",
    vote_invalidated: bool = False,
    is_container: bool = False,
    reviewer_id: str = "reviewer-1",
) -> VoteStatus:
    """Helper to build a VoteStatus with sensible defaults."""
    return VoteStatus(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@contoso.com",
        vote=vote,
        vote_text=vote_text,
        vote_invalidated=vote_invalidated,
        invalidated_by_commit=vote_invalidated,
        is_container=is_container,
        voted_for_ids=[],
        reviewer_id=reviewer_id,
    )


class TestApprovalStatusConstruction:
    """
    REQUIREMENT: ApprovalStatus holds computed approval state for a PR.

    WHO: ReviewStatus.approval_status field; any consumer computing approval state.
    WHAT: (1) all fields are accessible with VoteStatus lists for each
              reviewer category
          (2) a fully approved status with empty rejection/waiting/pending lists
              reflects is_approved=True
    WHY: Replaces untyped Dict[str, Any] from PDP. Enables IDE autocompletion and
         pyright validation of approval state access patterns.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  ApprovalStatus, VoteStatus
        Never: N/A
    """

    def test_all_fields_accessible_with_vote_status_lists(self) -> None:
        """
        Given typed VoteStatus lists for each reviewer category
        When ApprovalStatus is constructed
        Then all fields are accessible and list fields contain VoteStatus instances
        """
        # Given: VoteStatus instances for each category
        approver = _make_vote_status(name="Alice Smith", vote=10)
        invalidated = _make_vote_status(name="Bob Jones", vote=10, vote_invalidated=True)
        rejector = _make_vote_status(name="Charlie Brown", vote=-10, vote_text="Rejected")
        waiter = _make_vote_status(name="Diana Prince", vote=-5, vote_text="Waiting for author")
        pending = _make_vote_status(name="Eve Wilson", vote=0, vote_text="No vote")

        # When: ApprovalStatus is constructed
        status = ApprovalStatus(
            is_approved=False,
            needs_approvals_count=1,
            has_rejection=True,
            valid_approvers=[approver],
            invalidated_approvers=[invalidated],
            rejecting_reviewers=[rejector],
            waiting_reviewers=[waiter],
            pending_reviewers=[pending],
        )

        # Then: all fields are accessible with correct values
        assert status.is_approved is False, f"Expected is_approved False, got {status.is_approved}"
        assert status.needs_approvals_count == 1, (
            f"Expected needs_approvals_count 1, got {status.needs_approvals_count}"
        )
        assert status.has_rejection is True, (
            f"Expected has_rejection True, got {status.has_rejection}"
        )
        assert len(status.valid_approvers) == 1, (
            f"Expected 1 valid approver, got {len(status.valid_approvers)}"
        )
        assert status.valid_approvers[0].name == "Alice Smith", (
            f"Expected approver name 'Alice Smith', got '{status.valid_approvers[0].name}'"
        )
        assert len(status.invalidated_approvers) == 1, (
            f"Expected 1 invalidated approver, got {len(status.invalidated_approvers)}"
        )
        assert len(status.rejecting_reviewers) == 1, (
            f"Expected 1 rejecting reviewer, got {len(status.rejecting_reviewers)}"
        )
        assert len(status.waiting_reviewers) == 1, (
            f"Expected 1 waiting reviewer, got {len(status.waiting_reviewers)}"
        )
        assert len(status.pending_reviewers) == 1, (
            f"Expected 1 pending reviewer, got {len(status.pending_reviewers)}"
        )

    def test_approved_status_with_empty_lists(self) -> None:
        """
        Given is_approved=True with no rejecting/waiting/pending reviewers
        When ApprovalStatus is constructed
        Then the approval state reflects a fully approved PR
        """
        # Given: fully approved with two valid approvers, no issues
        approvers = [
            _make_vote_status(name="Alice Smith", reviewer_id="r1"),
            _make_vote_status(name="Bob Jones", reviewer_id="r2"),
        ]

        # When: constructed with all-clear state
        status = ApprovalStatus(
            is_approved=True,
            needs_approvals_count=0,
            has_rejection=False,
            valid_approvers=approvers,
            invalidated_approvers=[],
            rejecting_reviewers=[],
            waiting_reviewers=[],
            pending_reviewers=[],
        )

        # Then: approval state reflects fully approved
        assert status.is_approved is True, f"Expected is_approved True, got {status.is_approved}"
        assert status.needs_approvals_count == 0, (
            f"Expected needs_approvals_count 0, got {status.needs_approvals_count}"
        )
        assert len(status.valid_approvers) == 2, (
            f"Expected 2 valid approvers, got {len(status.valid_approvers)}"
        )


class TestReviewStatusConstruction:
    """
    REQUIREMENT: ReviewStatus holds the full review status for a single PR.

    WHO: Consumers of get_review_status() — MCP tools, CI integrations.
    WHAT: (1) all fields including nested ApprovalStatus are accessible with
              correct values
          (2) last_commit_date=None is accepted for empty commit history
    WHY: Replaces untyped Dict[str, Any] returns from PDP. Provides typed,
         discoverable access to all review state.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  ReviewStatus, ApprovalStatus, VoteStatus
        Never: N/A
    """

    def test_all_fields_accessible_with_nested_approval_status(self) -> None:
        """
        Given all required fields including a nested ApprovalStatus
        When ReviewStatus is constructed
        Then all fields are accessible including nested approval data
        """
        # Given: a nested ApprovalStatus
        approval = ApprovalStatus(
            is_approved=True,
            needs_approvals_count=0,
            has_rejection=False,
            valid_approvers=[_make_vote_status()],
            invalidated_approvers=[],
            rejecting_reviewers=[],
            waiting_reviewers=[],
            pending_reviewers=[],
        )
        commit_date = datetime(2026, 3, 3, 14, 30, 0)

        # When: ReviewStatus is constructed
        review = ReviewStatus(
            pr_id=42,
            title="Add payment validation",
            author="Alice Smith",
            url="https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42",
            days_open=3,
            last_commit_date=commit_date,
            approval_status=approval,
            summary="Ready to merge (approved by 1 reviewers)",
        )

        # Then: all fields accessible
        assert review.pr_id == 42, f"Expected pr_id 42, got {review.pr_id}"
        assert review.title == "Add payment validation", (
            f"Expected title 'Add payment validation', got '{review.title}'"
        )
        assert review.author == "Alice Smith", (
            f"Expected author 'Alice Smith', got '{review.author}'"
        )
        assert review.url.endswith("/pullrequest/42"), (
            f"Expected url to end with '/pullrequest/42', got '{review.url}'"
        )
        assert review.days_open == 3, f"Expected days_open 3, got {review.days_open}"
        assert review.last_commit_date == commit_date, (
            f"Expected last_commit_date {commit_date}, got {review.last_commit_date}"
        )
        assert review.approval_status.is_approved is True, (
            f"Expected nested is_approved True, got {review.approval_status.is_approved}"
        )
        assert review.summary.startswith("Ready to merge"), (
            f"Expected summary starting with 'Ready to merge', got '{review.summary}'"
        )

    def test_last_commit_date_none_for_empty_commit_history(self) -> None:
        """
        Given last_commit_date=None
        When ReviewStatus is constructed
        Then last_commit_date is None
        """
        # Given: no commits
        approval = ApprovalStatus(
            is_approved=False,
            needs_approvals_count=2,
            has_rejection=False,
            valid_approvers=[],
            invalidated_approvers=[],
            rejecting_reviewers=[],
            waiting_reviewers=[],
            pending_reviewers=[],
        )

        # When: constructed with None commit date
        review = ReviewStatus(
            pr_id=99,
            title="Empty PR",
            author="Bob Jones",
            url="https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/99",
            days_open=0,
            last_commit_date=None,
            approval_status=approval,
            summary="Needs 2 approval(s)",
        )

        # Then: last_commit_date is None
        assert review.last_commit_date is None, (
            f"Expected last_commit_date None, got {review.last_commit_date}"
        )


class TestCommentSummaryConstruction:
    """
    REQUIREMENT: CommentSummary holds thread count statistics for a PR.

    WHO: CommentAnalysis.comment_summary field; any consumer needing thread stats.
    WHAT: (1) four fields (total_threads, active_threads, fixed_threads,
              active_percentage) are accessible with correct values
    WHY: Typed container replaces nested dict keys for comment thread statistics.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  CommentSummary
        Never: N/A
    """

    def test_all_fields_accessible(self) -> None:
        """
        Given thread count values and a percentage
        When CommentSummary is constructed
        Then all fields are accessible with correct values
        """
        # Given: thread statistics
        summary = CommentSummary(
            total_threads=10,
            active_threads=3,
            fixed_threads=7,
            active_percentage=30.0,
        )

        # When / Then: all fields accessible
        assert summary.total_threads == 10, (
            f"Expected total_threads 10, got {summary.total_threads}"
        )
        assert summary.active_threads == 3, (
            f"Expected active_threads 3, got {summary.active_threads}"
        )
        assert summary.fixed_threads == 7, f"Expected fixed_threads 7, got {summary.fixed_threads}"
        assert summary.active_percentage == 30.0, (
            f"Expected active_percentage 30.0, got {summary.active_percentage}"
        )


class TestAuthorSampleConstruction:
    """
    REQUIREMENT: AuthorSample summarizes a single author's comment activity.

    WHO: CommentAnalysis.author_samples field.
    WHAT: (1) three fields (count, latest_comment, latest_status) are
              accessible with correct values
    WHY: Typed container replaces nested dict for per-author comment summaries.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  AuthorSample
        Never: N/A
    """

    def test_all_fields_accessible(self) -> None:
        """
        Given author sample data
        When AuthorSample is constructed
        Then all fields are accessible
        """
        # Given: author activity data
        sample = AuthorSample(
            count=5,
            latest_comment="Looks good, one minor nit on line 42",
            latest_status="active",
        )

        # When / Then: all fields accessible
        assert sample.count == 5, f"Expected count 5, got {sample.count}"
        assert sample.latest_comment.startswith("Looks good"), (
            f"Expected latest_comment starting with 'Looks good', got '{sample.latest_comment}'"
        )
        assert sample.latest_status == "active", (
            f"Expected latest_status 'active', got '{sample.latest_status}'"
        )


class TestCommentInfoConstruction:
    """
    REQUIREMENT: CommentInfo holds a single comment with thread and file context.

    WHO: CommentAnalysis.active_comments field.
    WHAT: (1) a comment with full file context has all ten fields accessible
          (2) a comment without file context has None for file_path,
              line_start, and line_end
    WHY: Typed container replaces nested dicts for individual comment metadata.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  CommentInfo
        Never: N/A
    """

    def test_comment_with_full_file_context(self) -> None:
        """
        Given a comment with file path and line range
        When CommentInfo is constructed
        Then all fields including file context are accessible
        """
        # Given: a comment with full file context
        info = CommentInfo(
            thread_id=1,
            thread_status="active",
            author="Alice Smith",
            content_preview="Consider using a guard clause here...",
            full_content="Consider using a guard clause here to reduce nesting",
            created_date="2026-03-03T14:30:00Z",
            is_deleted=False,
            file_path="/src/payment.py",
            line_start=42,
            line_end=45,
        )

        # When / Then: all fields accessible
        assert info.thread_id == 1, f"Expected thread_id 1, got {info.thread_id}"
        assert info.thread_status == "active", (
            f"Expected thread_status 'active', got '{info.thread_status}'"
        )
        assert info.author == "Alice Smith", f"Expected author 'Alice Smith', got '{info.author}'"
        assert info.file_path == "/src/payment.py", (
            f"Expected file_path '/src/payment.py', got '{info.file_path}'"
        )
        assert info.line_start == 42, f"Expected line_start 42, got {info.line_start}"
        assert info.line_end == 45, f"Expected line_end 45, got {info.line_end}"
        assert info.is_deleted is False, f"Expected is_deleted False, got {info.is_deleted}"

    def test_comment_without_file_context(self) -> None:
        """
        Given a comment with no file context (general PR comment)
        When CommentInfo is constructed with None file fields
        Then file_path, line_start, and line_end are None
        """
        # Given: a general PR comment with no file context
        info = CommentInfo(
            thread_id=2,
            thread_status="fixed",
            author="Bob Jones",
            content_preview="LGTM",
            full_content="LGTM",
            created_date="2026-03-02T10:00:00Z",
            is_deleted=False,
            file_path=None,
            line_start=None,
            line_end=None,
        )

        # When / Then: nullable fields are None
        assert info.file_path is None, f"Expected file_path None, got '{info.file_path}'"
        assert info.line_start is None, f"Expected line_start None, got {info.line_start}"
        assert info.line_end is None, f"Expected line_end None, got {info.line_end}"


class TestCommentAnalysisConstruction:
    """
    REQUIREMENT: CommentAnalysis holds full comment analysis results for a PR.

    WHO: Consumers of analyze_pr_comments() — MCP tools, review dashboards.
    WHAT: (1) all fields including nested CommentSummary, AuthorSample, and
              CommentInfo are accessible with correct types
          (2) resolution_ready=True when no active comments remain
    WHY: Replaces untyped Dict[str, Any] returns from PDP. Provides typed
         access to all comment analysis state.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  CommentAnalysis, CommentSummary, AuthorSample, CommentInfo
        Never: N/A
    """

    def test_all_fields_accessible_with_nested_types(self) -> None:
        """
        Given nested CommentSummary, AuthorSample, and CommentInfo instances
        When CommentAnalysis is constructed
        Then all fields are accessible with correct nested types
        """
        # Given: nested type instances
        summary = CommentSummary(
            total_threads=5,
            active_threads=2,
            fixed_threads=3,
            active_percentage=40.0,
        )
        sample = AuthorSample(
            count=3,
            latest_comment="Please fix the null check",
            latest_status="active",
        )
        comment = CommentInfo(
            thread_id=1,
            thread_status="active",
            author="Alice Smith",
            content_preview="Please fix the null check",
            full_content="Please fix the null check on line 42",
            created_date="2026-03-03T14:30:00Z",
            is_deleted=False,
            file_path="/src/payment.py",
            line_start=42,
            line_end=42,
        )

        # When: CommentAnalysis is constructed
        analysis = CommentAnalysis(
            pr_id=42,
            comment_summary=summary,
            comment_authors={"Alice Smith": 3, "Bob Jones": 2},
            author_samples={"Alice Smith": sample},
            active_comments=[comment],
            resolution_ready=False,
        )

        # Then: all fields accessible with correct types
        assert analysis.pr_id == 42, f"Expected pr_id 42, got {analysis.pr_id}"
        assert analysis.comment_summary.total_threads == 5, (
            f"Expected total_threads 5, got {analysis.comment_summary.total_threads}"
        )
        assert analysis.comment_authors["Alice Smith"] == 3, (
            f"Expected Alice Smith count 3, got {analysis.comment_authors.get('Alice Smith')}"
        )
        assert "Alice Smith" in analysis.author_samples, (
            f"Expected Alice Smith in author_samples, "
            f"got keys: {list(analysis.author_samples.keys())}"
        )
        assert len(analysis.active_comments) == 1, (
            f"Expected 1 active comment, got {len(analysis.active_comments)}"
        )
        assert analysis.resolution_ready is False, (
            f"Expected resolution_ready False, got {analysis.resolution_ready}"
        )

    def test_resolution_ready_when_no_active_comments(self) -> None:
        """
        Given no active comments
        When CommentAnalysis is constructed with resolution_ready=True
        Then resolution_ready is True
        """
        # Given: all threads resolved
        summary = CommentSummary(
            total_threads=3,
            active_threads=0,
            fixed_threads=3,
            active_percentage=0.0,
        )

        # When: constructed with resolution_ready=True
        analysis = CommentAnalysis(
            pr_id=42,
            comment_summary=summary,
            comment_authors={"Alice Smith": 2},
            author_samples={},
            active_comments=[],
            resolution_ready=True,
        )

        # Then: resolution_ready reflects all-resolved state
        assert analysis.resolution_ready is True, (
            f"Expected resolution_ready True, got {analysis.resolution_ready}"
        )

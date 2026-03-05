"""Domain types for Azure DevOps PR workflows.

Data containers with no business logic — used by :mod:`votes`, :mod:`review`,
and :mod:`comments` modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from actionable_errors import ActionableError

VOTE_TEXT: dict[int, str] = {
    10: "Approved",
    5: "Approved with suggestions",
    0: "No vote",
    -5: "Waiting for author",
    -10: "Rejected",
}
"""Map ADO vote integers to human-readable text."""


@dataclass
class ReviewerInfo:
    """Reviewer identity and vote metadata for pending-review workflows."""

    display_name: str
    unique_name: str  # email
    vote: int  # -10, -5, 0, 5, 10
    is_required: bool
    is_container: bool  # True for team/group reviewers


@dataclass
class VoteStatus:
    """Classified vote for a single reviewer.

    Produced by :func:`votes.determine_vote_status`.  Includes two-tier
    staleness detection results and team-container relationship data.
    """

    name: str
    email: str
    vote: int
    vote_text: str
    vote_invalidated: bool
    invalidated_by_commit: bool
    is_container: bool
    voted_for_ids: list[str]
    reviewer_id: str


@dataclass
class PendingPR:
    """PR metadata for review-reminder workflows."""

    pr_id: int
    title: str
    author: str
    creation_date: datetime
    repository: str
    organization: str
    project: str
    web_url: str
    pending_reviewers: list[ReviewerInfo]
    days_open: int
    merge_status: str  # 'succeeded', 'conflicts', 'queued', etc.
    has_conflicts: bool
    needs_approvals_count: int = 0
    valid_approvals_count: int = 0


# ---------------------------------------------------------------------------
# Phase 6c — review and comment analysis types
# ---------------------------------------------------------------------------


@dataclass
class ApprovalStatus:
    """Computed approval state for a PR.

    Categorised lists of :class:`VoteStatus` instances, plus aggregate
    approval/rejection flags used by :func:`review.get_review_status`.
    """

    is_approved: bool
    needs_approvals_count: int
    has_rejection: bool
    valid_approvers: list[VoteStatus]
    invalidated_approvers: list[VoteStatus]
    rejecting_reviewers: list[VoteStatus]
    waiting_reviewers: list[VoteStatus]
    pending_reviewers: list[VoteStatus]


@dataclass
class ReviewStatus:
    """Full review status for a single PR.

    Returned by :func:`review.get_review_status`.  Contains PR metadata,
    nested :class:`ApprovalStatus`, a human-readable summary, and any
    non-fatal enrichment warnings.
    """

    pr_id: int
    title: str
    author: str
    url: str
    days_open: int
    last_commit_date: datetime | None
    approval_status: ApprovalStatus
    summary: str
    warnings: list[ActionableError] = field(default_factory=list)


@dataclass
class CommentSummary:
    """Thread count statistics for a PR."""

    total_threads: int
    active_threads: int
    fixed_threads: int
    active_percentage: float


@dataclass
class AuthorSample:
    """Summary of a single author's comment activity."""

    count: int
    latest_comment: str
    latest_status: str


@dataclass
class CommentInfo:
    """Single comment with thread and file context."""

    thread_id: int
    thread_status: str
    author: str
    content_preview: str
    full_content: str
    created_date: str | None
    is_deleted: bool
    file_path: str | None
    line_start: int | None
    line_end: int | None


@dataclass
class CommentAnalysis:
    """Full comment analysis for a PR.

    Returned by :func:`comments.analyze_pr_comments`.
    """

    pr_id: int
    comment_summary: CommentSummary
    comment_authors: dict[str, int]
    author_samples: dict[str, AuthorSample]
    active_comments: list[CommentInfo]
    resolution_ready: bool


# ---------------------------------------------------------------------------
# Phase 6d — PR write operation types
# ---------------------------------------------------------------------------


@dataclass
class CreatedPR:
    """Result of :func:`lifecycle.create_pull_request`."""

    pr_id: int
    url: str
    title: str
    source_branch: str
    target_branch: str
    is_draft: bool


@dataclass
class ResolveResult:
    """Batch thread-resolution outcome.

    Returned by :func:`comments.resolve_comments`.  Threads are
    partitioned into *resolved* (status changed), *errors* (SDK error,
    as :class:`ActionableError` with ``context={"thread_id": tid}``),
    and *skipped* (already in target status).
    """

    resolved: list[int]
    errors: list[ActionableError]
    skipped: list[int]


# ---------------------------------------------------------------------------
# Phase 6e — Pending review analysis types
# ---------------------------------------------------------------------------


@dataclass
class PendingReviewResult:
    """Result container for pending review analysis.

    Returned by :func:`review.analyze_pending_reviews`.  Surfaces both
    successful results and per-PR enrichment failures so callers can
    report partial-success diagnostics.
    """

    pending_prs: list[PendingPR]
    skipped: list[ActionableError]

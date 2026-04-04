"""
Domain types for Azure DevOps PR workflows.

Data containers with no business logic — used by :mod:`votes`, :mod:`review`,
and :mod:`comments` modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
    """
    Classified vote for a single reviewer.

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
    """
    Computed approval state for a PR.

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


def _empty_errors() -> list[ActionableError]:
    return []


@dataclass
class ReviewStatus:
    """
    Full review status for a single PR.

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
    warnings: list[ActionableError] = field(default_factory=_empty_errors)


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
    """
    Full comment analysis for a PR.

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
    """
    Batch thread-resolution outcome.

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
    """
    Result container for pending review analysis.

    Returned by :func:`review.analyze_pending_reviews`.  Surfaces both
    successful results and per-PR enrichment failures so callers can
    report partial-success diagnostics.
    """

    pending_prs: list[PendingPR]
    skipped: list[ActionableError]


# ---------------------------------------------------------------------------
# Code review operations types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationInfo:
    """
    Metadata for a single PR iteration.

    Produced by :func:`iterations.get_pr_iterations`.
    """

    id: int
    created_date: datetime | None
    description: str | None


@dataclass(frozen=True)
class FileChange:
    """
    A file changed in a PR iteration.

    Produced by :func:`iterations.get_iteration_changes`.  The
    ``change_tracking_id`` is required for anchoring comment threads
    to the correct iteration in the PR diff.
    """

    path: str
    change_type: str  # "add", "edit", "delete", "rename"
    change_tracking_id: int


@dataclass(frozen=True)
class IterationContext:
    """
    Resolved iteration state for comment positioning.

    Produced by :func:`iterations.get_latest_iteration_context`.
    Maps file paths (no leading slash) to their :class:`FileChange`
    so callers can look up the ``change_tracking_id`` for any file.
    """

    iteration_id: int
    file_changes: dict[str, FileChange]


@dataclass(frozen=True)
class UserIdentity:
    """
    An Azure DevOps user identity with stable GUID for comparison.

    Display names vary by context (e.g., ``"Alice"`` vs ``"Alice (CONTOSO)"``).
    The ``id`` GUID is the only reliable comparator across ADO surfaces.
    ``unique_name`` (email) is available on PR author identities but not
    on authenticated-user identities.
    """

    display_name: str
    id: str
    unique_name: str | None = None


@dataclass(frozen=True)
class FileContent:
    """
    Content of a single file from a repository.

    Produced by :func:`content.get_file_content`.
    """

    path: str
    content: str
    encoding: str
    size_bytes: int


@dataclass(frozen=True)
class ContentResult:
    """
    Result of a batch file content fetch.

    Follows partial-success pattern: successfully fetched files in
    ``files``, failed fetches in ``failures`` as :class:`ActionableError`
    instances with ``context={"path": path}``.
    """

    files: list[FileContent]
    failures: list[ActionableError]


@dataclass(frozen=True)
class CommentPayload:
    """Input for batch comment posting via :func:`comments.post_comments`."""

    content: str
    file_path: str | None = None
    line_number: int | None = None
    status: str = "active"


@dataclass(frozen=True)
class PostingResult:
    """
    Result of a batch comment posting operation.

    Follows partial-success pattern (same as :class:`ResolveResult`).
    Failures are :class:`ActionableError` instances with
    ``context={"index": i, "content_preview": ...}``.
    """

    posted: list[int]  # thread IDs of successfully posted comments
    failures: list[ActionableError]
    skipped: list[int]  # indices skipped (e.g., dry_run)
    dry_run: bool


# ---------------------------------------------------------------------------
# Rich comment posting types
# ---------------------------------------------------------------------------


class CommentSeverity(Enum):
    """Severity levels for code review comments."""

    INFO = "info"
    SUGGESTION = "suggestion"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CommentType(Enum):
    """Semantic types for code review comments."""

    GENERAL = "general"
    LINE_COMMENT = "line"
    FILE_COMMENT = "file"
    SUGGESTION = "suggestion"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DOMAIN = "domain"


@dataclass(frozen=True)
class RichComment:
    """
    Structured code review comment with metadata for formatting and filtering.

    Extends the information carried by :class:`CommentPayload` with title,
    severity, type, suggested code, reasoning, business impact, and tags.
    """

    comment_id: str
    title: str
    content: str
    comment_type: CommentType = CommentType.GENERAL
    severity: CommentSeverity = CommentSeverity.INFO
    file_path: str | None = None
    line_number: int | None = None
    suggested_code: str | None = None
    reasoning: str | None = None
    business_impact: str | None = None
    tags: list[str] = field(default_factory=list[str])
    status: str = "active"
    parent_thread_id: int | None = None


@dataclass(frozen=True)
class PostedCommentDetail:
    """Detail for a successfully posted rich comment."""

    thread_id: int
    comment_id: str
    title: str
    file_path: str | None
    line_number: int | None


@dataclass(frozen=True)
class RichPostingResult:
    """
    Result of a rich comment posting operation.

    Extends :class:`PostingResult` with self-praise filtering results
    and per-comment detail.
    """

    posted: list[PostedCommentDetail]
    failures: list[ActionableError]
    skipped: list[int]
    dry_run: bool
    local_praise: list[RichComment]


# ---------------------------------------------------------------------------
# PR lifecycle types (FR2)
# ---------------------------------------------------------------------------


class MergeStrategy(Enum):
    """Merge strategies for PR completion."""

    NO_FAST_FORWARD = "noFastForward"
    SQUASH = "squash"
    REBASE = "rebase"
    REBASE_MERGE = "rebaseMerge"


@dataclass(frozen=True)
class ReviewerDetail:
    """Reviewer identity on a PR."""

    id: str  # GUID
    display_name: str
    unique_name: str  # email
    vote: int
    vote_text: str
    is_required: bool
    is_container: bool  # True for team/group reviewers


@dataclass(frozen=True)
class LabelDetail:
    """Label/tag on a PR."""

    id: str
    name: str


@dataclass(frozen=True)
class WorkItemRef:
    """Lightweight reference to a linked work item."""

    id: str  # numeric string, e.g. "37290513"
    url: str  # full REST URL


@dataclass(frozen=True)
class PullRequestDetail:
    """Read result for a single PR — richer than CreatedPR."""

    pr_id: int
    url: str
    title: str
    description: str | None
    source_branch: str
    target_branch: str
    status: str  # "active" | "completed" | "abandoned"
    is_draft: bool
    created_by: str  # display name
    creation_date: str  # ISO-8601
    merge_status: str  # "succeeded" | "conflicts" | "queued" | ...
    reviewers: list[ReviewerDetail]
    labels: list[LabelDetail]
    work_item_refs: list[WorkItemRef]


# ---------------------------------------------------------------------------
# Data-gathering types (FR3a)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PullRequestSummary:
    """Lightweight PR data from list endpoints — includes computed web_url."""

    pr_id: int
    title: str
    status: str
    created_by: str
    creation_date: str
    source_branch: str
    target_branch: str
    repository_name: str
    web_url: str
    is_draft: bool
    merge_status: str


@dataclass(frozen=True)
class WorkItemSummary:
    """Enriched work item from WIQL query + batch fetch."""

    id: int
    title: str
    state: str
    work_item_type: str
    assigned_to: str | None
    iteration_path: str | None
    completed_work: float | None
    remaining_work: float | None
    url: str


@dataclass(frozen=True)
class CommitSummary:
    """Git commit metadata from a local repository."""

    sha: str
    message: str
    author: str
    date: int  # Unix timestamp
    repo_name: str

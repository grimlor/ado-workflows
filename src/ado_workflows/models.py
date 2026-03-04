"""Domain types for Azure DevOps PR workflows.

Data containers with no business logic — used by :mod:`votes` and Phase 6c
read/write operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

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

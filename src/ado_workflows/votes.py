"""Vote classification and team-container deduplication.

Pure functions — no I/O, no SDK calls.  Operates on SDK
:class:`~azure.devops.v7_1.git.models.IdentityRefWithVote` model
objects and produces typed :class:`~models.VoteStatus` instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from azure.devops.v7_1.git.models import IdentityRefWithVote

from ado_workflows.models import VOTE_TEXT, VoteStatus


def determine_vote_status(
    reviewer: IdentityRefWithVote,
    stale_voter_ids: set[str] | None = None,
    vote_timestamps: dict[str, datetime] | None = None,
    latest_commit_date: datetime | None = None,
) -> VoteStatus:
    """Classify a single reviewer's vote with two-tier staleness detection.

    Staleness tiers (only for approval votes 10, 5):

    1. **Primary** — ``reviewer_id in stale_voter_ids``
       (from ADO ``OneReviewPolicyPilot`` property; authoritative).
    2. **Fallback** — vote timestamp < *latest_commit_date*
       (catches cases the primary source misses).

    Args:
        reviewer: An ``IdentityRefWithVote`` SDK model object.
        stale_voter_ids: Reviewer GUIDs marked stale by branch policy.
        vote_timestamps: Mapping of reviewer GUID → vote datetime.
        latest_commit_date: Datetime of the most recent push.

    Returns:
        A fully populated :class:`VoteStatus`.
    """
    vote: int = reviewer.vote or 0
    display_name: str = reviewer.display_name or "Unknown"
    unique_name: str = reviewer.unique_name or ""
    reviewer_id: str = reviewer.id or ""
    is_container: bool = reviewer.is_container or False

    # voted_for can be None from the API — default to empty list
    voted_for = reviewer.voted_for or []
    voted_for_ids: list[str] = [
        vf.id for vf in voted_for if vf and vf.id
    ]

    vote_text = VOTE_TEXT.get(vote, f"Unknown vote: {vote}")

    # Two-tier staleness detection — only for approvals
    vote_invalidated = False
    invalidated_by_commit = False

    if vote in (10, 5):
        # Tier 1: ADO branch policy stale list (authoritative)
        if stale_voter_ids and reviewer_id in stale_voter_ids:
            vote_invalidated = True
            invalidated_by_commit = True
        # Tier 2: timestamp comparison (fallback)
        elif vote_timestamps and latest_commit_date:
            vote_time = vote_timestamps.get(reviewer_id)
            if vote_time and vote_time < latest_commit_date:
                vote_invalidated = True
                invalidated_by_commit = True

    return VoteStatus(
        name=display_name,
        email=unique_name,
        vote=vote,
        vote_text=vote_text,
        vote_invalidated=vote_invalidated,
        invalidated_by_commit=invalidated_by_commit,
        is_container=is_container,
        voted_for_ids=voted_for_ids,
        reviewer_id=reviewer_id,
    )


def deduplicate_team_containers(
    vote_statuses: list[VoteStatus],
) -> list[VoteStatus]:
    """Remove team containers already represented by individual voters.

    When an individual votes on a PR, ADO also marks their team container
    as having voted.  This function removes the container entries to avoid
    double-counting approvals.

    Algorithm:

    1. Collect all ``voted_for_ids`` from non-container voters.
    2. Remove any container whose ``reviewer_id`` is in that set.

    Returns a new list — does not mutate the input.
    """
    # Build set of team IDs satisfied by individual votes
    satisfied_team_ids: set[str] = set()
    for vs in vote_statuses:
        if not vs.is_container:
            satisfied_team_ids.update(vs.voted_for_ids)

    # Filter out containers whose ID is in the satisfied set
    return [
        vs
        for vs in vote_statuses
        if not (vs.is_container and vs.reviewer_id in satisfied_team_ids)
    ]

"""PR review helpers and orchestrator.

Provides :func:`fetch_required_approvals` (policy-based minimum reviewer
count), :func:`fetch_vote_timestamps` (undocumented thread-property
extraction for per-reviewer vote datetimes), and :func:`get_review_status`
(end-to-end PR review status computation).

All functions interact with the Azure DevOps SDK via :class:`~client.AdoClient`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from actionable_errors import ActionableError

from ado_workflows.models import ApprovalStatus, ReviewStatus
from ado_workflows.votes import deduplicate_team_containers, determine_vote_status

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient


def fetch_required_approvals(
    client: AdoClient,
    project: str,
    pr_id: int,
    *,
    default_required_approvals: int = 2,
) -> int:
    """Fetch the minimum required approvals from branch policy evaluations.

    Uses ``PolicyClient.get_policy_evaluations()`` with the PR artifact ID.
    Looks for a *Minimum number of reviewers* policy type and extracts
    ``configuration.settings["minimumApproverCount"]``.

    Returns *default_required_approvals* when no matching policy is found.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        project: Azure DevOps project name or GUID.
        pr_id: Pull request ID.
        default_required_approvals: Fallback count when no policy is
            found.  Default ``2``.

    Returns:
        The minimum required reviewer count.

    Raises:
        ActionableError: When the policy API call fails.  The caller
            decides whether to use the default and surface a warning.
    """
    artifact_id = f"vstfs:///CodeReview/CodeReviewId/{project}/{pr_id}"

    try:
        evaluations: list[Any] = client.policy.get_policy_evaluations(
            project, artifact_id,
        )
    except Exception as exc:
        raise ActionableError.internal(
            service="AzureDevOps",
            operation=f"fetch_required_approvals(PR {pr_id})",
            raw_error=str(exc),
            suggestion=(
                f"Policy API unavailable for PR {pr_id}; "
                f"caller should fall back to default of "
                f"{default_required_approvals} approvals."
            ),
        ) from exc

    for evaluation in evaluations:
        display_name: str = evaluation.configuration.type.display_name
        if "Minimum number of reviewers" in display_name:
            count: int = evaluation.configuration.settings["minimumApproverCount"]
            return count

    return default_required_approvals


def fetch_vote_timestamps(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> dict[str, datetime]:
    """Extract per-reviewer vote timestamps from PR thread properties.

    Scans threads for the undocumented ``CodeReviewVotedByIdentity``
    property to correlate reviewer GUIDs with when they voted.  The
    property value is a thread-local identity reference number (e.g.
    ``"1"``) which must be resolved via ``thread.identities`` to obtain
    the actual reviewer GUID.

    Uses ``thread.published_date`` as the vote timestamp.

    When the same reviewer appears in multiple threads, only the
    **latest** timestamp is kept.

    .. warning::

       Relies on an undocumented ADO property.  May break without notice.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        project: Azure DevOps project name or GUID.

    Returns:
        Mapping of ``{reviewer_guid: vote_datetime}``.
    """
    threads: list[Any] = client.git.get_threads(repository, pr_id, project=project)

    vote_timestamps: dict[str, datetime] = {}

    for thread in threads:
        props = thread.properties
        if not props:
            continue

        vote_prop = props.get("CodeReviewVotedByIdentity")
        if vote_prop is None:
            continue

        identity_ref: str = vote_prop.get("$value", "")
        if not identity_ref:
            continue

        # Resolve thread-local identity ref to actual GUID
        identities: dict[str, Any] = thread.identities or {}
        identity = identities.get(identity_ref)
        if identity is None:
            continue

        reviewer_id: str = getattr(identity, "id", "") or ""
        if not reviewer_id:
            continue

        published: datetime | None = thread.published_date
        if published is None:
            continue

        # Keep the latest timestamp for each reviewer
        existing = vote_timestamps.get(reviewer_id)
        if existing is None or published > existing:
            vote_timestamps[reviewer_id] = published

    return vote_timestamps


def get_review_status(
    client: AdoClient,
    pr_id: int,
    project: str,
    repository: str,
    *,
    default_required_approvals: int = 2,
) -> ReviewStatus:
    """Compute the full review status for a pull request.

    Orchestrates:

    1. Fetch PR details (``git.get_pull_request_by_id``)
    2. Fetch commit history (``git.get_pull_request_commits``)
    3. Fetch PR properties for stale voter IDs
       (``git.get_pull_request_properties``)
    4. Fetch vote timestamps from threads (:func:`fetch_vote_timestamps`)
    5. Classify each reviewer's vote (:func:`~votes.determine_vote_status`)
    6. Deduplicate team containers
       (:func:`~votes.deduplicate_team_containers`)
    7. Fetch required approvals (:func:`fetch_required_approvals`)
    8. Compute approval status and build summary

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        pr_id: Pull request ID.
        project: Azure DevOps project name or GUID.
        repository: Repository name or GUID.
        default_required_approvals: Fallback reviewer count when no
            branch policy is configured.  Default ``2``.

    Returns:
        A fully populated :class:`~models.ReviewStatus`.

    Raises:
        ActionableError: When the PR cannot be fetched (not found,
            authentication failure, permission error).
    """
    # Step 1 — Fetch PR details
    try:
        pr: Any = client.git.get_pull_request_by_id(pr_id)
    except Exception as exc:
        raise ActionableError.not_found(
            service="AzureDevOps",
            resource_type="PullRequest",
            resource_id=str(pr_id),
            raw_error=str(exc),
            suggestion=(
                f"Verify PR {pr_id} exists and you have read access to "
                f"project '{project}' in repository '{repository}'."
            ),
        ) from exc

    # Step 2 — Fetch commits (latest commit date for staleness detection)
    commits: list[Any] = client.git.get_pull_request_commits(
        repository, pr_id, project=project,
    )
    last_commit_date: datetime | None = None
    if commits:
        last_commit_date = max(c.author.date for c in commits)

    # Step 3 — Extract stale voter IDs from PR properties
    warnings: list[ActionableError] = []
    stale_voter_ids: set[str] = set()
    try:
        properties: dict[str, Any] = client.git.get_pull_request_properties(
            repository, pr_id, project=project,
        )
        prop_value: dict[str, Any] = properties.get("value") or {}
        one_review = prop_value.get("OneReviewPolicyPilot")
        if one_review:
            raw_json: str = one_review.get("$value", "")
            if raw_json:
                parsed = json.loads(raw_json)
                stale_voter_ids = set(parsed.get("staleBecauseOfPush", []))
    except Exception as exc:
        warnings.append(ActionableError.internal(
            service="AzureDevOps",
            operation=f"get_pull_request_properties(PR {pr_id})",
            raw_error=str(exc),
            suggestion=(
                "PR properties unavailable; tier-1 staleness detection "
                "(OneReviewPolicyPilot) skipped. Tier-2 (vote timestamp "
                "comparison) still active."
            ),
        ))

    # Step 4 — Fetch vote timestamps from thread properties
    vote_timestamps = fetch_vote_timestamps(client, repository, pr_id, project)

    # Step 5 — Classify each reviewer's vote
    reviewers: list[Any] = pr.reviewers or []
    vote_statuses = [
        determine_vote_status(
            reviewer,
            stale_voter_ids=stale_voter_ids or None,
            vote_timestamps=vote_timestamps or None,
            latest_commit_date=last_commit_date,
        )
        for reviewer in reviewers
    ]

    # Step 6 — Deduplicate team containers
    vote_statuses = deduplicate_team_containers(vote_statuses)

    # Step 7 — Fetch required approvals
    try:
        required = fetch_required_approvals(
            client, project, pr_id,
            default_required_approvals=default_required_approvals,
        )
    except ActionableError as warning:
        warnings.append(warning)
        required = default_required_approvals

    # Step 8 — Compute approval status
    valid_approvers = [
        vs for vs in vote_statuses
        if vs.vote in (10, 5) and not vs.vote_invalidated
    ]
    invalidated_approvers = [
        vs for vs in vote_statuses
        if vs.vote in (10, 5) and vs.vote_invalidated
    ]
    rejecting_reviewers = [
        vs for vs in vote_statuses if vs.vote == -10
    ]
    waiting_reviewers = [
        vs for vs in vote_statuses if vs.vote == -5
    ]
    pending_reviewers = [
        vs for vs in vote_statuses if vs.vote == 0
    ]

    has_rejection = len(rejecting_reviewers) > 0
    valid_count = len(valid_approvers)
    needs = max(0, required - valid_count)
    is_approved = valid_count >= required and not has_rejection

    approval_status = ApprovalStatus(
        is_approved=is_approved,
        needs_approvals_count=needs,
        has_rejection=has_rejection,
        valid_approvers=valid_approvers,
        invalidated_approvers=invalidated_approvers,
        rejecting_reviewers=rejecting_reviewers,
        waiting_reviewers=waiting_reviewers,
        pending_reviewers=pending_reviewers,
    )

    # Build human-readable summary
    summary = _build_summary(approval_status, invalidated_approvers, waiting_reviewers)

    # Compute days_open
    creation_date: datetime = pr.creation_date
    now = datetime.now(tz=UTC)
    days_open = (now - creation_date).days

    return ReviewStatus(
        pr_id=pr_id,
        title=pr.title,
        author=pr.created_by.unique_name,
        url=pr.url,
        days_open=days_open,
        last_commit_date=last_commit_date,
        approval_status=approval_status,
        summary=summary,
        warnings=warnings,
    )


def _build_summary(
    approval: ApprovalStatus,
    invalidated: list[Any],
    waiting: list[Any],
) -> str:
    """Build a human-readable summary string for the review status."""
    parts: list[str] = []

    if approval.has_rejection:
        names = ", ".join(r.name for r in approval.rejecting_reviewers)
        parts.append(f"BLOCKED: Rejected by {names}")

    if invalidated:
        names = ", ".join(v.name for v in invalidated)
        parts.append(f"Invalidated approvals: {names}")

    if waiting:
        names = ", ".join(w.name for w in waiting)
        parts.append(f"Waiting for author: {names}")

    if approval.is_approved:
        parts.append("Ready to merge")
    elif approval.needs_approvals_count > 0 and not approval.has_rejection:
        parts.append(f"Needs {approval.needs_approvals_count} approval(s)")

    return ". ".join(parts) if parts else "No reviewers assigned"

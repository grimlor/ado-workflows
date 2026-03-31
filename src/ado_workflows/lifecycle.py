"""
PR lifecycle operations — create, get, update, complete, reviewers, labels.

Provides functions for the full PR lifecycle: creation, metadata retrieval
and update, status transitions (abandon, complete), reviewer management,
label management, and work-item-ref reads.  All operations delegate to the
Azure DevOps SDK through :class:`~client.AdoClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from actionable_errors import ActionableError

from ado_workflows.models import (
    VOTE_TEXT,
    CreatedPR,
    LabelDetail,
    MergeStrategy,
    PullRequestDetail,
    ReviewerDetail,
    WorkItemRef,
)

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient

# SDK model imports — runtime, not type-only.
from azure.devops.v7_1.git.models import (
    GitPullRequest,
    GitPullRequestCompletionOptions,
    IdentityRefWithVote,
    ResourceRef,
    WebApiCreateTagRequestData,
    WebApiTagDefinition,
)

_BRANCH_PREFIX = "refs/heads/"


def _normalize_branch(name: str) -> str:
    """Ensure *name* has the ``refs/heads/`` prefix."""
    if name.startswith(_BRANCH_PREFIX):
        return name
    return f"{_BRANCH_PREFIX}{name}"


def _map_reviewer(sdk_reviewer: IdentityRefWithVote) -> ReviewerDetail:
    """Map an SDK IdentityRefWithVote to a ReviewerDetail."""
    vote = sdk_reviewer.vote or 0
    return ReviewerDetail(
        id=sdk_reviewer.id or "",
        display_name=sdk_reviewer.display_name or "",
        unique_name=sdk_reviewer.unique_name or "",
        vote=vote,
        vote_text=VOTE_TEXT.get(vote, f"Unknown ({vote})"),
        is_required=sdk_reviewer.is_required or False,
        is_container=sdk_reviewer.is_container or False,
    )


def _map_label(sdk_label: WebApiTagDefinition) -> LabelDetail:
    """Map an SDK WebApiTagDefinition to a LabelDetail."""
    return LabelDetail(
        id=sdk_label.id,
        name=sdk_label.name,
    )


def _map_work_item_ref(sdk_ref: ResourceRef) -> WorkItemRef:
    """Map an SDK ResourceRef to a WorkItemRef."""
    return WorkItemRef(
        id=sdk_ref.id,
        url=sdk_ref.url,
    )


def _map_pr_detail(response: GitPullRequest) -> PullRequestDetail:
    """Map an SDK GitPullRequest response to a PullRequestDetail."""
    reviewers = [_map_reviewer(r) for r in (response.reviewers or [])]
    labels = [_map_label(lbl) for lbl in (response.labels or [])]
    work_item_refs = [_map_work_item_ref(w) for w in (response.work_item_refs or [])]
    created_by = response.created_by
    return PullRequestDetail(
        pr_id=response.pull_request_id,
        url=response.url,
        title=response.title,
        description=response.description,
        source_branch=response.source_ref_name,
        target_branch=response.target_ref_name,
        status=response.status or "",
        is_draft=response.is_draft,
        created_by=created_by.display_name if created_by else "",
        creation_date=str(response.creation_date or ""),
        merge_status=str(response.merge_status or ""),
        reviewers=reviewers,
        labels=labels,
        work_item_refs=work_item_refs,
    )


def create_pull_request(
    client: AdoClient,
    repository: str,
    source_branch: str,
    target_branch: str,
    project: str,
    *,
    title: str | None = None,
    description: str | None = None,
    is_draft: bool = False,
) -> CreatedPR:
    """
    Create a pull request via the Azure DevOps SDK.

    Branch names are normalised to include ``refs/heads/`` if missing.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        source_branch: Source branch (with or without ``refs/heads/``).
        target_branch: Target branch (with or without ``refs/heads/``).
        project: Azure DevOps project name or GUID.
        title: Optional PR title.
        description: Optional PR description.
        is_draft: Whether to create as a draft PR.

    Returns:
        A :class:`~models.CreatedPR` with the new PR's metadata.

    Raises:
        ActionableError: When the SDK call fails.

    """
    pr_model = GitPullRequest(
        source_ref_name=_normalize_branch(source_branch),
        target_ref_name=_normalize_branch(target_branch),
        title=title,
        description=description,
        is_draft=is_draft,
    )

    try:
        response = client.git.create_pull_request(pr_model, repository, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests",
            raw_error=str(exc),
            suggestion=(
                f"Verify repository '{repository}' exists in project "
                f"'{project}' and you have create-PR permissions."
            ),
        ) from exc

    return CreatedPR(
        pr_id=response.pull_request_id,
        url=response.url,
        title=response.title,
        source_branch=response.source_ref_name,
        target_branch=response.target_ref_name,
        is_draft=response.is_draft,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_pull_request(
    client: AdoClient,
    pr_id: int,
    project: str,
) -> PullRequestDetail:
    """
    Retrieve full PR metadata including reviewers, labels, and work items.

    Uses ``get_pull_request_by_id`` (org-scoped) — no repository ID needed.

    Raises:
        ActionableError: When the PR cannot be fetched.

    """
    try:
        response = client.git.get_pull_request_by_id(pr_id, project=project)
    except Exception as exc:
        raise ActionableError.not_found(
            service="AzureDevOps",
            resource_type="pull_request",
            resource_id=str(pr_id),
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists in project '{project}' and you have read access.",
        ) from exc

    return _map_pr_detail(response)


# ---------------------------------------------------------------------------
# Update metadata
# ---------------------------------------------------------------------------


def update_pull_request(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> PullRequestDetail:
    """
    Update title and/or description of an existing PR.

    Raises:
        ActionableError: When neither field is provided or the SDK call fails.

    """
    if title is None and description is None:
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="title/description",
            reason="At least one of title or description must be provided.",
            suggestion="Pass title=... and/or description=... to update.",
        )

    pr_model = GitPullRequest(title=title, description=description)

    try:
        response = client.git.update_pull_request(pr_model, repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have edit permissions.",
        ) from exc

    return _map_pr_detail(response)


def retarget_pull_request(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    target_branch: str,
) -> PullRequestDetail:
    """
    Change the target branch of an existing PR.

    Branch names are normalised to include ``refs/heads/`` if missing.

    Raises:
        ActionableError: When the SDK call fails.

    """
    pr_model = GitPullRequest(target_ref_name=_normalize_branch(target_branch))

    try:
        response = client.git.update_pull_request(pr_model, repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=(
                f"Verify branch '{target_branch}' exists in repository "
                f"'{repository}' and you have edit permissions."
            ),
        ) from exc

    return _map_pr_detail(response)


def set_draft_status(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    is_draft: bool,
) -> PullRequestDetail:
    """
    Toggle a PR between draft and published state.

    Raises:
        ActionableError: When the SDK call fails.

    """
    pr_model = GitPullRequest(is_draft=is_draft)

    try:
        response = client.git.update_pull_request(pr_model, repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have edit permissions.",
        ) from exc

    return _map_pr_detail(response)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def abandon_pull_request(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> PullRequestDetail:
    """
    Abandon (close without merging) an existing PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    pr_model = GitPullRequest(status="abandoned")

    try:
        response = client.git.update_pull_request(pr_model, repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have permissions to abandon it.",
        ) from exc

    return _map_pr_detail(response)


def complete_pull_request(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    merge_strategy: MergeStrategy = MergeStrategy.SQUASH,
    delete_source_branch: bool = True,
    transition_work_items: bool = True,
    merge_commit_message: str | None = None,
    bypass_policy: bool = False,
    bypass_reason: str | None = None,
) -> PullRequestDetail:
    """
    Complete (merge) a PR with configurable merge strategy.

    Raises:
        ActionableError: When bypass_policy is True without a reason,
            when merge conflicts exist, or on any other SDK failure.

    """
    if bypass_policy and not bypass_reason:
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="bypass_reason",
            reason="bypass_reason is required when bypass_policy is True.",
            suggestion="Provide a bypass_reason explaining why policies are being bypassed.",
        )

    completion_options = GitPullRequestCompletionOptions(
        merge_strategy=merge_strategy.value,
        delete_source_branch=delete_source_branch,
        transition_work_items=transition_work_items,
        merge_commit_message=merge_commit_message,
        bypass_policy=bypass_policy,
        bypass_reason=bypass_reason,
    )
    pr_model = GitPullRequest(status="completed", completion_options=completion_options)

    try:
        response = client.git.update_pull_request(pr_model, repository, pr_id, project)
    except Exception as exc:
        error_str = str(exc).lower()
        if "conflict" in error_str:
            raise ActionableError.connection(
                service="AzureDevOps",
                url=f"{repository}/pullrequests/{pr_id}",
                raw_error=str(exc),
                suggestion=(
                    f"PR {pr_id} has merge conflicts. Resolve conflicts in the "
                    f"source branch before completing."
                ),
            ) from exc
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} is approved and you have complete permissions.",
        ) from exc

    return _map_pr_detail(response)


# ---------------------------------------------------------------------------
# Reviewers
# ---------------------------------------------------------------------------


def add_reviewer(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    reviewer_id: str,
    is_required: bool = False,
) -> ReviewerDetail:
    """
    Add a reviewer to a PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    reviewer_model = IdentityRefWithVote(
        id=reviewer_id,
        vote=0,
        is_required=is_required,
    )

    try:
        response = client.git.create_pull_request_reviewer(
            reviewer_model, repository, pr_id, reviewer_id, project
        )
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/reviewers/{reviewer_id}",
            raw_error=str(exc),
            suggestion=f"Verify reviewer '{reviewer_id}' is a valid identity.",
        ) from exc

    return _map_reviewer(response)


def remove_reviewer(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    reviewer_id: str,
) -> None:
    """
    Remove a reviewer from a PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        client.git.delete_pull_request_reviewer(repository, pr_id, reviewer_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/reviewers/{reviewer_id}",
            raw_error=str(exc),
            suggestion=f"Verify reviewer '{reviewer_id}' exists on PR {pr_id}.",
        ) from exc


def list_reviewers(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> list[ReviewerDetail]:
    """
    List all reviewers on a PR with vote details.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        sdk_reviewers = client.git.get_pull_request_reviewers(repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/reviewers",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have read access.",
        ) from exc

    return [_map_reviewer(r) for r in sdk_reviewers]


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def add_label(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    name: str,
) -> LabelDetail:
    """
    Add a label/tag to a PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    label_model = WebApiCreateTagRequestData(name=name)

    try:
        response = client.git.create_pull_request_label(label_model, repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/labels",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have label permissions.",
        ) from exc

    return _map_label(response)


def remove_label(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    label_name: str,
) -> None:
    """
    Remove a label from a PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        client.git.delete_pull_request_labels(repository, pr_id, label_name, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/labels/{label_name}",
            raw_error=str(exc),
            suggestion=f"Verify label '{label_name}' exists on PR {pr_id}.",
        ) from exc


def list_labels(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> list[LabelDetail]:
    """
    List all labels on a PR.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        sdk_labels = client.git.get_pull_request_labels(repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/labels",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have read access.",
        ) from exc

    return [_map_label(lbl) for lbl in sdk_labels]


# ---------------------------------------------------------------------------
# Work item refs (read-only)
# ---------------------------------------------------------------------------


def get_pr_work_item_refs(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> list[WorkItemRef]:
    """
    List work items linked to a PR (read-only).

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        sdk_refs = client.git.get_pull_request_work_item_refs(repository, pr_id, project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/workitems",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and you have read access.",
        ) from exc

    return [_map_work_item_ref(w) for w in sdk_refs]

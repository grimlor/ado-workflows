"""
PR lifecycle operations — create, (future: update, complete).

Provides :func:`create_pull_request` which constructs a
:class:`~azure.devops.v7_1.git.models.GitPullRequest` and delegates to
the SDK.  Returns a typed :class:`~models.CreatedPR`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from actionable_errors import ActionableError

from ado_workflows.models import CreatedPR

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient

# SDK model import — deferred to avoid import-time dependency on azure-devops
# when only type-checking is active.
from azure.devops.v7_1.git.models import GitPullRequest

_BRANCH_PREFIX = "refs/heads/"


def _normalize_branch(name: str) -> str:
    """Ensure *name* has the ``refs/heads/`` prefix."""
    if name.startswith(_BRANCH_PREFIX):
        return name
    return f"{_BRANCH_PREFIX}{name}"


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

"""PR iteration tracking and change context resolution.

Provides iteration metadata and per-file change tracking IDs needed for
anchoring comment threads to the correct PR iteration.

Public API:
    get_pr_iterations(client, repository, pr_id, project) -> list[IterationInfo]
    get_iteration_changes(client, repository, pr_id, iteration_id, project) -> list[FileChange]
    get_latest_iteration_context(client, repository, pr_id, project) -> IterationContext
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from actionable_errors import ActionableError

from ado_workflows.models import FileChange, IterationContext, IterationInfo

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient


def get_pr_iterations(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> list[IterationInfo]:
    """Return all iterations for a PR, ordered by creation date.

    Raises:
        ActionableError: When the SDK call fails (connection factory).
    """
    try:
        raw_iterations = client.git.get_pull_request_iterations(repository, pr_id, project=project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/iterations",
            raw_error=str(exc),
            suggestion=(
                f"Verify the PR {pr_id} exists in repository '{repository}' "
                f"and that you have read access."
            ),
        ) from exc

    return [
        IterationInfo(
            id=it.id,
            created_date=it.created_date,
            description=it.description,
        )
        for it in raw_iterations
    ]


def get_iteration_changes(
    client: AdoClient,
    repository: str,
    pr_id: int,
    iteration_id: int,
    project: str,
) -> list[FileChange]:
    """Return file changes for a specific iteration with changeTrackingId.

    Uses ``compare_to=0`` (default) which returns all files changed in the
    PR relative to the merge base, not just files changed in this specific
    iteration push.

    Raises:
        ActionableError: When the SDK call fails (connection factory).
    """
    try:
        raw_changes = client.git.get_pull_request_iteration_changes(
            repository, pr_id, iteration_id, project=project
        )
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/iterations/{iteration_id}/changes",
            raw_error=str(exc),
            suggestion=(
                f"Verify PR {pr_id} and iteration {iteration_id} exist. "
                f"Check network connectivity to Azure DevOps."
            ),
        ) from exc

    result: list[FileChange] = []
    for entry in raw_changes.change_entries:
        additional: dict[str, Any] = entry.additional_properties
        item = additional.get("item", {})
        path = item.get("path", "")
        if not path:
            continue
        result.append(
            FileChange(
                path=path.lstrip("/"),
                change_type=additional.get("changeType", "unknown"),
                change_tracking_id=entry.change_tracking_id,
            )
        )
    return result


def get_latest_iteration_context(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
) -> IterationContext:
    """Convenience: latest iteration ID + file path to FileChange map.

    Calls :func:`get_pr_iterations` then :func:`get_iteration_changes`
    for the latest iteration. The returned :class:`IterationContext`
    maps file paths (no leading slash) to their :class:`FileChange`
    including ``change_tracking_id``.

    Raises:
        ActionableError: When the PR has no iterations (validation) or
            when SDK calls fail (connection).
    """
    iterations = get_pr_iterations(client, repository, pr_id, project)
    if not iterations:
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="iterations",
            reason=f"PR {pr_id} has no iterations. The PR may not have any pushes yet.",
            suggestion=(
                "Ensure the PR has at least one push to the source branch. "
                "A PR with no iterations cannot have file-level comments."
            ),
        )

    latest = iterations[-1]
    changes = get_iteration_changes(client, repository, pr_id, latest.id, project)
    file_map = {fc.path: fc for fc in changes}

    return IterationContext(iteration_id=latest.id, file_changes=file_map)

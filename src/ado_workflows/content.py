"""
File content retrieval from Azure DevOps repositories.

Provides single-file retrieval (:func:`get_file_content`) and batch
PR-scoped retrieval (:func:`get_changed_file_contents`) with
partial-success semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from actionable_errors import ActionableError
from azure.devops.v7_1.git.models import GitVersionDescriptor

from ado_workflows.iterations import get_latest_iteration_context
from ado_workflows.models import ContentResult, FileContent

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient


def get_file_content(
    client: AdoClient,
    repository: str,
    path: str,
    project: str,
    *,
    version: str | None = None,
    version_type: str = "branch",
) -> FileContent:
    """
    Fetch a single file's content from a repository ref.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        path: File path within the repository.
        project: Azure DevOps project name or GUID.
        version: Branch name, commit SHA, or tag. ``None`` = default branch.
        version_type: One of ``"branch"``, ``"commit"``, or ``"tag"``.

    Returns:
        :class:`~models.FileContent` with the file's content string.

    Raises:
        ActionableError: When the file does not exist or cannot be fetched.

    """
    try:
        version_descriptor = None
        if version is not None:
            version_descriptor = GitVersionDescriptor(version=version, version_type=version_type)

        content_iter = client.git.get_item_content(
            repository,
            path=path,
            project=project,
            version_descriptor=version_descriptor,
        )

        raw_bytes = b"".join(content_iter)

    except Exception as exc:
        error_str = str(exc)
        if "TF401174" in error_str or "does not exist" in error_str.lower():
            raise ActionableError.not_found(
                service="AzureDevOps",
                resource_type="file",
                resource_id=path,
                raw_error=error_str,
                suggestion=(
                    f"Verify the file path '{path}' exists in the repository. "
                    f"Check branch/commit reference if specified."
                ),
            ) from exc
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/items/{path}",
            raw_error=error_str,
            suggestion="Check network connectivity to Azure DevOps.",
        ) from exc

    # Attempt UTF-8 decode; fall back for binary files
    try:
        content = raw_bytes.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = repr(raw_bytes)
        encoding = "binary"

    return FileContent(
        path=path,
        content=content,
        encoding=encoding,
        size_bytes=len(raw_bytes),
    )


def get_changed_file_contents(
    client: AdoClient,
    repository: str,
    pr_id: int,
    project: str,
    *,
    file_paths: list[str] | None = None,
    exclude_extensions: list[str] | None = None,
) -> ContentResult:
    """
    Fetch file contents for files changed in a PR.

    Uses the PR's source branch ref. If *file_paths* is ``None``, discovers
    changed files from the latest iteration and fetches all of them.

    For completed PRs whose source branch has been deleted, falls back to
    the ``last_merge_source_commit`` SHA.

    Uses partial-success semantics: files that fail to fetch are collected
    in :attr:`ContentResult.failures` with the path and error, not raised.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        project: Azure DevOps project name or GUID.
        file_paths: Optional list of specific file paths to fetch.
            If ``None``, fetches all changed files.
        exclude_extensions: Optional list of file extensions to skip
            (e.g. ``[".lock", ".json"]``).  Matched case-insensitively.
            A leading dot is added if missing.

    Returns:
        :class:`~models.ContentResult` with files and failures.

    """
    # Get the PR metadata for branch/commit resolution
    try:
        pr = client.git.get_pull_request_by_id(pr_id, project=project)
        branch = pr.source_ref_name.replace("refs/heads/", "")
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"pullrequests/{pr_id}",
            raw_error=str(exc),
            suggestion=f"Verify PR {pr_id} exists and is accessible.",
        ) from exc

    # Discover files if not specified
    if file_paths is None:
        try:
            iter_ctx = get_latest_iteration_context(client, repository, pr_id, project)
            file_paths = list(iter_ctx.file_changes.keys())
        except Exception:
            file_paths = []

    # Apply extension filtering before fetching
    if exclude_extensions:
        normalized = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in exclude_extensions
        ]
        file_paths = [
            p for p in file_paths if not any(p.lower().endswith(ext) for ext in normalized)
        ]

    if not file_paths:
        return ContentResult(files=[], failures=[])

    # Determine version reference: branch first, commit SHA fallback for completed PRs
    version = branch
    version_type = "branch"

    # Fetch each file with partial-success
    files: list[FileContent] = []
    failures: list[ActionableError] = []

    for path in file_paths:
        try:
            fc = get_file_content(
                client, repository, path, project, version=version, version_type=version_type
            )
            files.append(fc)
        except Exception as exc:
            # For completed PRs, try fallback to merge commit SHA
            merge_commit = pr.last_merge_source_commit
            if (
                pr.status == "completed"
                and version_type == "branch"
                and merge_commit is not None
                and merge_commit.commit_id is not None
            ):
                try:
                    fc = get_file_content(
                        client,
                        repository,
                        path,
                        project,
                        version=merge_commit.commit_id,
                        version_type="commit",
                    )
                    files.append(fc)
                    continue
                except Exception:
                    pass  # Fall through to original error handling

            # For completed PRs with deleted branch and no merge commit, raise
            if (
                pr.status == "completed"
                and ("TF401174" in str(exc) or "does not exist" in str(exc).lower())
                and (merge_commit is None or merge_commit.commit_id is None)
            ):
                raise ActionableError.not_found(
                    service="AzureDevOps",
                    resource_type="PR source",
                    resource_id=f"PR {pr_id}",
                    raw_error=str(exc),
                    suggestion=(
                        f"Source branch for PR {pr_id} has been deleted and no "
                        f"merge commit is available. The PR source code cannot be retrieved."
                    ),
                ) from exc

            err = ActionableError.internal(
                service="ado-workflows",
                operation="get_file_content",
                raw_error=str(exc),
                suggestion=f"Failed to fetch '{path}'. Verify the file exists in the PR branch.",
            )
            err.context = {"path": path}
            failures.append(err)

    return ContentResult(files=files, failures=failures)

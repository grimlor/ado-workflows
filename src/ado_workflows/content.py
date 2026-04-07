"""
File and directory content retrieval from Azure DevOps repositories.

Provides single-file retrieval (:func:`get_file_content`), batch
PR-scoped retrieval (:func:`get_changed_file_contents`) with
partial-success semantics, and directory listing
(:func:`list_repo_items`) for any branch, commit, or tag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from azure.devops.v7_1.git.models import GitVersionDescriptor

from ado_workflows.errors import classify_ado_error
from ado_workflows.iterations import get_latest_iteration_context
from ado_workflows.models import ContentResult, FileContent, RepoItem

if TYPE_CHECKING:
    from actionable_errors import ActionableError

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
        raise classify_ado_error(
            exc, operation=f"get file content '{path}'", context_hint=path
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
        raise classify_ado_error(
            exc, operation=f"get PR {pr_id} for file content", context_hint=str(pr_id)
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
                raise classify_ado_error(
                    exc,
                    operation=f"get source for completed PR {pr_id}",
                    context_hint=f"PR {pr_id}",
                ) from exc

            err = classify_ado_error(
                exc,
                operation=f"get file content '{path}' from PR {pr_id}",
                context_hint=path,
            )
            err.context = {"path": path}
            failures.append(err)

    return ContentResult(files=files, failures=failures)


def list_repo_items(
    client: AdoClient,
    repository: str,
    project: str,
    *,
    path: str = "/",
    ref: str | None = None,
    recursion: str = "oneLevel",
) -> list[RepoItem]:
    """
    List files and folders at a path on any branch, commit, or tag.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        project: Azure DevOps project name or GUID.
        path: Directory path to list. Defaults to ``"/"``.
        ref: Branch name, commit SHA, or tag. ``None`` = default branch.
        recursion: Recursion level — ``"none"``, ``"oneLevel"`` (default),
            ``"oneLevelPlusNestedEmptyFolders"``, or ``"full"``.

    Returns:
        A list of :class:`~models.RepoItem` for each file/folder at the path.

    Raises:
        ActionableError: Classified by :func:`~errors.classify_ado_error`.

    """
    try:
        version_descriptor = None
        if ref is not None:
            version_descriptor = GitVersionDescriptor(version=ref, version_type="branch")

        sdk_items = client.git.get_items(
            repository,
            project=project,
            scope_path=path,
            recursion_level=recursion,
            version_descriptor=version_descriptor,
        )

    except Exception as exc:
        raise classify_ado_error(
            exc, operation=f"list items at '{path}'", context_hint=path
        ) from exc

    return [
        RepoItem(
            path=item.path or "",
            is_folder=bool(item.is_folder),
            git_object_type=item.git_object_type or "",
            object_id=item.object_id or "",
            commit_id=item.commit_id or "",
            url=item.url,
        )
        for item in (sdk_items or [])
    ]

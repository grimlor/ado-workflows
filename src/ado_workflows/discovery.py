"""Layer 1 — Git repository discovery primitives for Azure DevOps.

Pure functions (except for subprocess calls to ``git``), no state, no SDK
dependency.  Designed for single- and multi-repo workspaces.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from ado_workflows.parsing import parse_ado_url


def inspect_git_repository(repo_path: str) -> dict[str, Any] | None:
    """Extract Azure DevOps metadata from a local git repository.

    Runs ``git config --get remote.origin.url`` and parses the result.

    Args:
        repo_path: Absolute path to a directory containing a ``.git`` folder.

    Returns:
        A dict with keys ``path``, ``name``, ``organization``, ``project``,
        ``remote_url``, ``org_url``, and ``workspace_context`` — or ``None``
        if the directory is not a valid Azure DevOps git repo.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_path,
        )

        if result.returncode != 0:
            return None

        remote_url = result.stdout.strip()
        org, project, repository, _ = parse_ado_url(remote_url)

        if not all([org, project, repository]):
            return None

        # Construct org_url based on the original URL format
        if ".visualstudio.com" in remote_url:
            org_url = f"https://{org}.visualstudio.com"
        else:
            org_url = f"https://dev.azure.com/{org}"

        # Workspace context — detect multi-repo workspaces
        try:
            is_multi_repo = len(os.listdir(os.path.dirname(repo_path))) > 1
        except (OSError, PermissionError):
            is_multi_repo = False

        workspace_context = {
            "is_multi_repo_workspace": is_multi_repo,
            "workspace_root": os.path.dirname(repo_path),
            "repository_relative_path": os.path.basename(repo_path),
        }

        return {
            "path": repo_path,
            "name": repository,
            "organization": org,
            "project": project,
            "remote_url": remote_url,
            "org_url": org_url,
            "workspace_context": workspace_context,
        }

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None


def discover_repositories(search_root: str) -> list[dict[str, Any]]:
    """Find all Azure DevOps git repositories under *search_root*.

    If *search_root* itself is a git repository, returns a single-element
    list.  Otherwise scans its immediate children for ``.git`` folders.

    Args:
        search_root: Directory to search from.

    Returns:
        A (possibly empty) list of repository info dicts.
    """
    repositories: list[dict[str, Any]] = []

    # Check if search_root itself is a git repo
    if os.path.exists(os.path.join(search_root, ".git")):
        repo_info = inspect_git_repository(search_root)
        if repo_info:
            repositories.append(repo_info)
        return repositories

    # Scan immediate children
    try:
        for item in os.listdir(search_root):
            item_path = os.path.join(search_root, item)
            if os.path.isdir(item_path) and os.path.exists(
                os.path.join(item_path, ".git")
            ):
                repo_info = inspect_git_repository(item_path)
                if repo_info:
                    repositories.append(repo_info)
    except (OSError, PermissionError):
        pass

    return repositories


def infer_target_repository(
    repositories: list[dict[str, Any]],
    working_directory: str | None = None,
) -> dict[str, Any] | None:
    """Select the most likely target repository from a list.

    Selection strategy (first match wins):

    1. If the list is empty, return ``None``.
    2. If the list has one element, return it.
    3. If *working_directory* is inside one repo's ``path``, return that repo.
    4. If ``os.getcwd()`` is inside one repo's ``path``, return that repo.
    5. Otherwise return ``None`` (ambiguous).

    Args:
        repositories: Candidate repos (output of :func:`discover_repositories`).
        working_directory: Optional hint — an absolute path the user is
            working in.

    Returns:
        The best-guess repository, or ``None`` when the choice is ambiguous.
    """
    if not repositories:
        return None

    if len(repositories) == 1:
        return repositories[0]

    # Prefer the repo whose path contains the working directory
    if working_directory:
        for repo in repositories:
            if working_directory.startswith(repo["path"]):
                return repo

    # Fallback: cwd
    current_dir = os.getcwd()
    for repo in repositories:
        if current_dir.startswith(repo["path"]):
            return repo

    return None

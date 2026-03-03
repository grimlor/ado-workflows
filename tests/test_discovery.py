"""
BDD tests for ado_workflows.discovery — git repository discovery primitives.

Covers:
- TestInspectGitRepository: single-repo extraction, non-ADO repos, subprocess failures
- TestDiscoverRepositories: single-repo root, multi-repo scanning, empty workspace
- TestInferTargetRepository: working directory match, single repo, ambiguous selection

Public API surface (from src/ado_workflows/discovery.py):
    inspect_git_repository(repo_path: str) -> dict[str, Any] | None
    discover_repositories(search_root: str) -> list[dict[str, Any]]
    infer_target_repository(
        repositories: list[dict[str, Any]],
        working_directory: str | None = None,
    ) -> dict[str, Any] | None
"""

from __future__ import annotations

import subprocess as sp
from typing import Any
from unittest.mock import Mock, patch

from ado_workflows.discovery import (
    discover_repositories,
    infer_target_repository,
    inspect_git_repository,
)


class TestInspectGitRepository:
    """
    REQUIREMENT: A single git repository is inspected to extract Azure DevOps metadata.

    WHO: Any consumer needing org/project/repo from a local git checkout.
    WHAT: Running `git config --get remote.origin.url` on a valid Azure DevOps
          repo returns a dict with path, name, organization, project, remote_url,
          and org_url; non-Azure DevOps repos return None; subprocess failures
          return None.
    WHY: Repository metadata is the input to every Layer 2/3 operation —
         inspect is the single source of truth for what repo the user is in.

    MOCK BOUNDARY:
        Mock:  subprocess.run (git CLI — the only I/O boundary),
               os.path.exists, os.listdir, os.path.dirname, os.path.basename
        Real:  inspect_git_repository function, parse_ado_url (called internally)
        Never: construct the return dict directly — always obtain via inspect_git_repository()
    """

    def test_valid_dev_azure_com_repo_returns_metadata(self) -> None:
        """
        Given a git repo with a dev.azure.com remote
        When inspect_git_repository is called
        Then a dict with org, project, name, and org_url is returned
        """
        # Given: a git repo with dev.azure.com remote
        with (
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo\n",
            )
            mock_listdir.return_value = ["src", "tests"]
            mock_dirname.return_value = "/workspace"
            mock_basename.return_value = "MyRepo"

            # When: the repository is inspected
            result = inspect_git_repository("/workspace/MyRepo")

        # Then: metadata is correct
        assert result is not None, (
            "Expected dict for valid ADO repo, got None"
        )
        assert result["organization"] == "ExampleOrg", (
            f"Expected org 'ExampleOrg', got '{result['organization']}'"
        )
        assert result["project"] == "MyProject", (
            f"Expected project 'MyProject', got '{result['project']}'"
        )
        assert result["name"] == "MyRepo", (
            f"Expected name 'MyRepo', got '{result['name']}'"
        )
        assert result["org_url"] == "https://dev.azure.com/ExampleOrg", (
            f"Expected org_url 'https://dev.azure.com/ExampleOrg', got '{result['org_url']}'"
        )
        assert result["path"] == "/workspace/MyRepo", (
            f"Expected path '/workspace/MyRepo', got '{result['path']}'"
        )

    def test_valid_visualstudio_com_repo_returns_legacy_org_url(self) -> None:
        """
        Given a git repo with a visualstudio.com remote
        When inspect_git_repository is called
        Then org_url uses the visualstudio.com format
        """
        # Given: a git repo with visualstudio.com remote
        with (
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://example.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo\n",
            )
            mock_listdir.return_value = ["src"]
            mock_dirname.return_value = "/workspace"
            mock_basename.return_value = "MyRepo"

            # When: the repository is inspected
            result = inspect_git_repository("/workspace/MyRepo")

        # Then: org_url uses visualstudio.com format
        assert result is not None, (
            "Expected dict for valid visualstudio.com repo, got None"
        )
        assert result["org_url"] == "https://example.visualstudio.com", (
            f"Expected visualstudio.com org_url, got '{result['org_url']}'"
        )

    def test_non_azure_devops_remote_returns_none(self) -> None:
        """
        Given a git repo with a GitHub remote
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: a git repo with a GitHub remote
        with patch("ado_workflows.discovery.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://github.com/example/some-repo.git\n",
            )

            # When: the repository is inspected
            result = inspect_git_repository("/workspace/some-repo")

        # Then: None is returned for non-ADO repos
        assert result is None, (
            f"Expected None for GitHub repo, got {result}"
        )

    def test_git_command_failure_returns_none(self) -> None:
        """
        Given a directory where git config fails
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: git command fails
        with patch("ado_workflows.discovery.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stderr="fatal: not a git repository",
            )

            # When: the repository is inspected
            result = inspect_git_repository("/not/a/repo")

        # Then: None is returned
        assert result is None, (
            f"Expected None for failed git command, got {result}"
        )

    def test_subprocess_timeout_returns_none(self) -> None:
        """
        Given a directory where git config times out
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: git command times out
        with patch("ado_workflows.discovery.subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired(cmd="git", timeout=10)

            # When: the repository is inspected
            result = inspect_git_repository("/workspace/slow-repo")

        # Then: None is returned gracefully
        assert result is None, (
            f"Expected None for subprocess timeout, got {result}"
        )

    def test_workspace_context_included_in_result(self) -> None:
        """
        Given a valid Azure DevOps git repo in a multi-repo workspace
        When inspect_git_repository is called
        Then the result includes workspace_context metadata
        """
        # Given: a repo inside a workspace with siblings
        with (
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo\n",
            )
            mock_listdir.return_value = ["MyRepo", "OtherRepo", "ThirdRepo"]
            mock_dirname.return_value = "/workspace"
            mock_basename.return_value = "MyRepo"

            # When: the repository is inspected
            result = inspect_git_repository("/workspace/MyRepo")

        # Then: workspace_context is present and indicates multi-repo
        assert result is not None, "Expected dict, got None"
        assert "workspace_context" in result, (
            f"Expected workspace_context in result, got keys: {list(result.keys())}"
        )
        ctx = result["workspace_context"]
        assert ctx["is_multi_repo_workspace"] is True, (
            f"Expected multi-repo workspace, got {ctx['is_multi_repo_workspace']}"
        )
        assert ctx["workspace_root"] == "/workspace", (
            f"Expected workspace_root '/workspace', got '{ctx['workspace_root']}'"
        )

    def test_workspace_context_graceful_on_parent_permission_error(self) -> None:
        """
        Given a valid ADO repo whose parent directory cannot be listed
        When inspect_git_repository is called
        Then workspace_context defaults to single-repo (is_multi_repo_workspace=False)
        """
        # Given: parent directory listing raises PermissionError
        with (
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo\n",
            )
            mock_listdir.side_effect = PermissionError("Permission denied")
            mock_dirname.return_value = "/restricted"
            mock_basename.return_value = "MyRepo"

            # When: the repository is inspected
            result = inspect_git_repository("/restricted/MyRepo")

        # Then: graceful degradation — assumes single-repo
        assert result is not None, "Expected dict, got None"
        ctx = result["workspace_context"]
        assert ctx["is_multi_repo_workspace"] is False, (
            f"Expected single-repo fallback on PermissionError, got {ctx['is_multi_repo_workspace']}"
        )


class TestDiscoverRepositories:
    """
    REQUIREMENT: All Azure DevOps git repositories under a root are discovered.

    WHO: Any consumer needing to enumerate repos in a workspace.
    WHAT: If search_root itself is a git repo, returns a single-element list;
          otherwise scans immediate children for git repos; non-git directories
          and non-ADO repos are excluded; permission errors are handled gracefully.
    WHY: Multi-repo workspaces are common in enterprise environments — correct
         enumeration is the prerequisite for intelligent repo selection.

    MOCK BOUNDARY:
        Mock:  subprocess.run (git CLI), os.path.exists, os.listdir,
               os.path.isdir, os.path.dirname, os.path.basename
        Real:  discover_repositories function, inspect_git_repository (called internally)
        Never: construct repo dicts directly — always obtain via discover_repositories()
    """

    def test_search_root_is_git_repo_returns_single_element(self) -> None:
        """
        Given search_root is itself a git repository
        When discover_repositories is called
        Then a single-element list is returned
        """
        # Given: search_root is a git repo
        with (
            patch("ado_workflows.discovery.os.path.exists") as mock_exists,
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            mock_exists.side_effect = lambda p: p == "/workspace/MyRepo/.git"
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo\n",
            )
            mock_listdir.return_value = ["src", "tests"]
            mock_dirname.return_value = "/workspace"
            mock_basename.return_value = "MyRepo"

            # When: repositories are discovered
            repos = discover_repositories("/workspace/MyRepo")

        # Then: exactly one repository is returned
        assert len(repos) == 1, (
            f"Expected 1 repo when search_root is a git repo, got {len(repos)}"
        )
        assert repos[0]["name"] == "MyRepo", (
            f"Expected repo name 'MyRepo', got '{repos[0]['name']}'"
        )

    def test_multi_repo_workspace_discovers_all_git_children(self) -> None:
        """
        Given search_root contains multiple git repository subdirectories
        When discover_repositories is called
        Then all Azure DevOps repos are included in the result
        """
        # Given: workspace root with multiple repos
        with (
            patch("ado_workflows.discovery.os.path.exists") as mock_exists,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.isdir") as mock_isdir,
            patch("ado_workflows.discovery.subprocess.run") as mock_run,
            patch("ado_workflows.discovery.os.path.dirname") as mock_dirname,
            patch("ado_workflows.discovery.os.path.basename") as mock_basename,
        ):
            # Root is not a git repo, but children are
            mock_exists.side_effect = lambda p: p in {
                "/workspace/RepoA/.git",
                "/workspace/RepoB/.git",
            }
            mock_listdir.side_effect = [
                # First call: listing workspace root children
                ["RepoA", "RepoB", "not-a-repo"],
                # Subsequent calls: listing each repo's parent for workspace_context
                ["RepoA", "RepoB", "not-a-repo"],
                ["RepoA", "RepoB", "not-a-repo"],
            ]
            mock_isdir.side_effect = lambda p: p in {
                "/workspace/RepoA",
                "/workspace/RepoB",
                "/workspace/not-a-repo",
            }

            def git_response(cmd: list[str], **kwargs: Any) -> Mock:
                cwd = kwargs.get("cwd", "")
                if "RepoA" in str(cwd):
                    return Mock(
                        returncode=0,
                        stdout="https://dev.azure.com/ExampleOrg/ProjectA/_git/RepoA\n",
                    )
                return Mock(
                    returncode=0,
                    stdout="https://dev.azure.com/ExampleOrg/ProjectB/_git/RepoB\n",
                )

            mock_run.side_effect = git_response
            mock_dirname.return_value = "/workspace"
            mock_basename.side_effect = lambda p: p.rsplit("/", 1)[-1]

            # When: repositories are discovered
            repos = discover_repositories("/workspace")

        # Then: both ADO repos are found
        names = [r["name"] for r in repos]
        assert len(repos) == 2, (
            f"Expected 2 repos, got {len(repos)}: {names}"
        )
        assert "RepoA" in names, f"Expected RepoA in {names}"
        assert "RepoB" in names, f"Expected RepoB in {names}"

    def test_empty_workspace_returns_empty_list(self) -> None:
        """
        Given search_root has no git repositories
        When discover_repositories is called
        Then an empty list is returned
        """
        # Given: workspace with no git repos
        with (
            patch("ado_workflows.discovery.os.path.exists") as mock_exists,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
            patch("ado_workflows.discovery.os.path.isdir") as mock_isdir,
        ):
            mock_exists.return_value = False
            mock_listdir.return_value = ["docs", "scripts"]
            mock_isdir.return_value = True

            # When: repositories are discovered
            repos = discover_repositories("/workspace")

        # Then: empty list is returned
        assert repos == [], (
            f"Expected empty list for workspace with no git repos, got {repos}"
        )

    def test_permission_error_during_scan_returns_empty_list(self) -> None:
        """
        Given search_root listing raises PermissionError
        When discover_repositories is called
        Then an empty list is returned gracefully
        """
        # Given: permission error when listing
        with (
            patch("ado_workflows.discovery.os.path.exists") as mock_exists,
            patch("ado_workflows.discovery.os.listdir") as mock_listdir,
        ):
            mock_exists.return_value = False  # not a git repo itself
            mock_listdir.side_effect = PermissionError("Permission denied")

            # When: repositories are discovered
            repos = discover_repositories("/restricted")

        # Then: empty list rather than exception
        assert repos == [], (
            f"Expected empty list on PermissionError, got {repos}"
        )


class TestInferTargetRepository:
    """
    REQUIREMENT: The most likely target repository is selected from a list.

    WHO: Any consumer needing to resolve which repo the user intends to work with.
    WHAT: When working_directory is inside a repo's path, that repo is selected;
          when only one repo exists, it is selected automatically; when no match
          is found and cwd is inside a repo, that repo is selected; when truly
          ambiguous, None is returned.
    WHY: Multi-repo workspaces require intelligent default selection —
         forcing users to specify a repo for every operation is unacceptable UX.

    MOCK BOUNDARY:
        Mock:  os.getcwd (cwd fallback — the only I/O boundary)
        Real:  infer_target_repository function
        Never: construct the return value directly
    """

    def test_working_directory_inside_repo_selects_that_repo(self) -> None:
        """
        Given a list of repos and working_directory inside one of them
        When infer_target_repository is called
        Then the repo containing working_directory is returned
        """
        # Given: two repos, working dir inside the second
        repos: list[dict[str, Any]] = [
            {"path": "/workspace/RepoA", "name": "RepoA"},
            {"path": "/workspace/RepoB", "name": "RepoB"},
        ]

        # When: inference runs with working_directory hint
        result = infer_target_repository(repos, "/workspace/RepoB/src/main.py")

        # Then: RepoB is selected
        assert result is not None, (
            "Expected repo selection for matching working_directory"
        )
        assert result["name"] == "RepoB", (
            f"Expected 'RepoB', got '{result['name']}'"
        )

    def test_single_repo_is_selected_automatically(self) -> None:
        """
        Given a list with exactly one repo
        When infer_target_repository is called
        Then that repo is returned regardless of working_directory
        """
        # Given: single repo
        repos: list[dict[str, Any]] = [
            {"path": "/workspace/OnlyRepo", "name": "OnlyRepo"},
        ]

        # When: inference runs
        result = infer_target_repository(repos)

        # Then: the single repo is returned
        assert result is not None, "Expected single repo to be selected"
        assert result["name"] == "OnlyRepo", (
            f"Expected 'OnlyRepo', got '{result['name']}'"
        )

    def test_empty_list_returns_none(self) -> None:
        """
        Given an empty repository list
        When infer_target_repository is called
        Then None is returned
        """
        # Given: no repos
        repos: list[dict[str, Any]] = []

        # When: inference runs
        result = infer_target_repository(repos)

        # Then: None
        assert result is None, f"Expected None for empty list, got {result}"

    def test_ambiguous_selection_returns_none(self) -> None:
        """
        Given multiple repos and working_directory not inside any of them
        When infer_target_repository is called
        Then None is returned
        """
        # Given: multiple repos, working dir elsewhere
        repos: list[dict[str, Any]] = [
            {"path": "/workspace/RepoA", "name": "RepoA"},
            {"path": "/workspace/RepoB", "name": "RepoB"},
            {"path": "/workspace/RepoC", "name": "RepoC"},
        ]

        # When: inference runs with unrelated working directory
        result = infer_target_repository(repos, "/different/path")

        # Then: None (ambiguous)
        assert result is None, (
            f"Expected None for ambiguous selection, got {result}"
        )

    def test_cwd_fallback_selects_matching_repo(self) -> None:
        """
        Given multiple repos and no working_directory hint
        When infer_target_repository is called and cwd is inside one repo
        Then the repo containing cwd is returned
        """
        # Given: multiple repos, cwd inside one
        repos: list[dict[str, Any]] = [
            {"path": "/workspace/RepoA", "name": "RepoA"},
            {"path": "/workspace/RepoB", "name": "RepoB"},
        ]

        with patch("ado_workflows.discovery.os.getcwd") as mock_cwd:
            mock_cwd.return_value = "/workspace/RepoA/src"

            # When: inference runs without working_directory
            result = infer_target_repository(repos)

        # Then: RepoA is selected via cwd fallback
        assert result is not None, (
            "Expected repo selection via cwd fallback"
        )
        assert result["name"] == "RepoA", (
            f"Expected 'RepoA' via cwd, got '{result['name']}'"
        )

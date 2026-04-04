"""
BDD tests for ado_workflows.discovery — git repository discovery primitives.

Covers:
    TestInspectGitRepository — single-repo extraction, non-ADO repos,
        GitPython errors
    TestDiscoverRepositories — single-repo root, multi-repo scanning,
        empty workspace
    TestInferTargetRepository — working directory match, single repo,
        ambiguous selection

Public API surface (from src/ado_workflows/discovery.py):
    inspect_git_repository(repo_path: str) -> dict[str, Any] | None
    discover_repositories(search_root: str) -> list[dict[str, Any]]
    infer_target_repository(
        repositories: list[dict[str, Any]],
        working_directory: str | None = None,
    ) -> dict[str, Any] | None
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError
from git import InvalidGitRepositoryError, NoSuchPathError

if TYPE_CHECKING:
    from pathlib import Path

from ado_workflows.discovery import (
    discover_repositories,
    infer_target_repository,
    inspect_git_repository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADO_REMOTE = "https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo"
_VSO_REMOTE = "https://example.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo"
_GITHUB_REMOTE = "https://github.com/example/some-repo.git"
_REPO_PATCH = "ado_workflows.discovery.Repo"


def _mock_repo(remote_url: str = _ADO_REMOTE) -> MagicMock:
    """Return a mock GitPython Repo with an origin remote."""
    repo = MagicMock()
    repo.remotes.origin.url = remote_url

    def _bool(_self: object) -> bool:
        return True

    def _len(_self: object) -> int:
        return 1

    repo.remotes.__bool__ = _bool
    repo.remotes.__len__ = _len
    return repo


def _make_git_repo(workspace: Path, name: str) -> Path:
    """Create a directory with a ``.git`` marker folder inside *workspace*."""
    repo = workspace / name
    (repo / ".git").mkdir(parents=True)
    return repo


# ---------------------------------------------------------------------------
# TestInspectGitRepository
# ---------------------------------------------------------------------------


class TestInspectGitRepository:
    """
    REQUIREMENT: A single git repository is inspected to extract Azure DevOps
    metadata.

    WHO: Any consumer needing org/project/repo from a local git checkout.
    WHAT: (1) a valid dev.azure.com repo returns a dict with path, name,
              organization, project, remote_url, and org_url
          (2) a valid visualstudio.com repo returns the legacy org_url format
          (3) a non-Azure DevOps remote returns None
          (4) an invalid git repository returns None
          (5) a path that does not exist returns None
          (6) workspace_context is included with multi-repo detection
          (7) parent permission errors degrade gracefully to single-repo
          (8) unexpected remote errors raise ActionableError
          (9) a repo with no remotes configured returns None
          (10) a ValueError or AttributeError from remote access returns None
    WHY: Repository metadata is the input to every Layer 2/3 operation —
         inspect is the single source of truth for what repo the user is in.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  inspect_git_repository function, parse_ado_url (called
               internally), filesystem (tmp_path)
        Never: construct the return dict directly — always obtain via
               inspect_git_repository()
    """

    def test_valid_dev_azure_com_repo_returns_metadata(self, tmp_path: Path) -> None:
        """
        Given a git repo with a dev.azure.com remote
        When inspect_git_repository is called
        Then a dict with org, project, name, and org_url is returned
        """
        # Given: a directory with a .git folder and an ADO remote
        repo = _make_git_repo(tmp_path, "MyRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo()):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: metadata is correct
        assert result is not None, "Expected dict for valid ADO repo, got None"
        assert result["organization"] == "ExampleOrg", (
            f"Expected org 'ExampleOrg', got '{result['organization']}'"
        )
        assert result["project"] == "MyProject", (
            f"Expected project 'MyProject', got '{result['project']}'"
        )
        assert result["name"] == "MyRepo", f"Expected name 'MyRepo', got '{result['name']}'"
        assert result["org_url"] == "https://dev.azure.com/ExampleOrg", (
            f"Expected org_url 'https://dev.azure.com/ExampleOrg', got '{result['org_url']}'"
        )
        assert result["path"] == str(repo), f"Expected path '{repo}', got '{result['path']}'"

    def test_valid_visualstudio_com_repo_returns_legacy_org_url(self, tmp_path: Path) -> None:
        """
        Given a git repo with a visualstudio.com remote
        When inspect_git_repository is called
        Then org_url uses the visualstudio.com format
        """
        # Given: a repo with a visualstudio.com remote
        repo = _make_git_repo(tmp_path, "MyRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo(_VSO_REMOTE)):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: org_url uses visualstudio.com format
        assert result is not None, "Expected dict for valid visualstudio.com repo, got None"
        assert result["org_url"] == "https://example.visualstudio.com", (
            f"Expected visualstudio.com org_url, got '{result['org_url']}'"
        )

    def test_non_azure_devops_remote_returns_none(self, tmp_path: Path) -> None:
        """
        Given a git repo with a GitHub remote
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: a repo with a GitHub remote
        repo = _make_git_repo(tmp_path, "some-repo")
        with patch(_REPO_PATCH, return_value=_mock_repo(_GITHUB_REMOTE)):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: None is returned for non-ADO repos
        assert result is None, f"Expected None for GitHub repo, got {result}"

    def test_invalid_git_repository_returns_none(self, tmp_path: Path) -> None:
        """
        Given a directory that is not a git repository
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: Repo() raises InvalidGitRepositoryError
        repo = _make_git_repo(tmp_path, "not-a-repo")
        with patch(_REPO_PATCH, side_effect=InvalidGitRepositoryError("not a repo")):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: None is returned
        assert result is None, f"Expected None for invalid git repo, got {result}"

    def test_nonexistent_path_returns_none(self, tmp_path: Path) -> None:
        """
        Given a path that does not exist
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: Repo() raises NoSuchPathError
        with patch(_REPO_PATCH, side_effect=NoSuchPathError("/nonexistent")):
            # When: the repository is inspected
            result = inspect_git_repository("/nonexistent")

        # Then: None is returned
        assert result is None, f"Expected None for nonexistent path, got {result}"

    def test_workspace_context_included_in_result(self, tmp_path: Path) -> None:
        """
        Given a valid Azure DevOps git repo in a multi-repo workspace
        When inspect_git_repository is called
        Then the result includes workspace_context metadata
        """
        # Given: a workspace with multiple sibling directories
        workspace = tmp_path
        repo = _make_git_repo(workspace, "MyRepo")
        _make_git_repo(workspace, "OtherRepo")
        (workspace / "ThirdDir").mkdir()

        with patch(_REPO_PATCH, return_value=_mock_repo()):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: workspace_context is present and indicates multi-repo
        assert result is not None, "Expected dict, got None"
        assert "workspace_context" in result, (
            f"Expected workspace_context in result, got keys: {list(result.keys())}"
        )
        ctx = result["workspace_context"]
        assert ctx["is_multi_repo_workspace"] is True, (
            f"Expected multi-repo workspace, got {ctx['is_multi_repo_workspace']}"
        )
        assert ctx["workspace_root"] == str(workspace), (
            f"Expected workspace_root '{workspace}', got '{ctx['workspace_root']}'"
        )

    def test_workspace_context_graceful_on_parent_permission_error(self, tmp_path: Path) -> None:
        """
        Given a valid ADO repo whose parent directory cannot be listed
        When inspect_git_repository is called
        Then workspace_context defaults to single-repo
        """
        # Given: a repo whose parent listing raises PermissionError
        parent = tmp_path / "restricted"
        parent.mkdir()
        repo = parent / "MyRepo"
        (repo / ".git").mkdir(parents=True)
        parent.chmod(0o000)

        try:
            with patch(_REPO_PATCH, return_value=_mock_repo()):
                # When: the repository is inspected
                result = inspect_git_repository(str(repo))

            # Then: graceful degradation — assumes single-repo
            assert result is not None, "Expected dict, got None"
            ctx = result["workspace_context"]
            assert ctx["is_multi_repo_workspace"] is False, (
                f"Expected single-repo fallback on PermissionError, "
                f"got {ctx['is_multi_repo_workspace']}"
            )
        finally:
            parent.chmod(0o755)

    def test_unexpected_remote_error_raises_actionable_error(self, tmp_path: Path) -> None:
        """
        Given a git repo where reading the remote raises an unexpected error
        When inspect_git_repository is called
        Then ActionableError is raised with diagnostic guidance
        """
        # Given: Repo is valid but accessing remotes raises unexpectedly
        repo = _make_git_repo(tmp_path, "broken-repo")
        mock_repo = MagicMock()

        def _bool(_self: object) -> bool:
            return True

        def _len(_self: object) -> int:
            return 1

        mock_repo.remotes.__bool__ = _bool
        mock_repo.remotes.__len__ = _len
        type(mock_repo.remotes).origin = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("corrupt config")),
        )
        with patch(_REPO_PATCH, return_value=mock_repo):
            # When / Then: raises ActionableError
            with pytest.raises(ActionableError) as exc_info:
                inspect_git_repository(str(repo))
            assert "corrupt config" in str(exc_info.value), (
                f"Expected error to mention cause, got {exc_info.value!r}"
            )

    def test_repo_with_no_remotes_returns_none(self, tmp_path: Path) -> None:
        """
        Given a valid git repo with no remotes configured
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: a repo whose remotes list is empty
        repo = _make_git_repo(tmp_path, "no-remotes")
        mock_repo = MagicMock()
        mock_repo.remotes = []

        with patch(_REPO_PATCH, return_value=mock_repo):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: None is returned because no remote URL can be read
        assert result is None, f"Expected None for repo with no remotes, got {result}"

    def test_value_error_from_remote_access_returns_none(self, tmp_path: Path) -> None:
        """
        Given a git repo where accessing origin raises ValueError
        When inspect_git_repository is called
        Then None is returned
        """
        # Given: Repo is valid, has remotes, but origin access raises ValueError
        repo = _make_git_repo(tmp_path, "bad-origin")
        mock_repo = MagicMock()

        def _bool(_self: object) -> bool:
            return True

        def _len(_self: object) -> int:
            return 1

        mock_repo.remotes.__bool__ = _bool
        mock_repo.remotes.__len__ = _len
        type(mock_repo.remotes).origin = property(
            lambda self: (_ for _ in ()).throw(ValueError("No remote named 'origin'")),
        )

        with patch(_REPO_PATCH, return_value=mock_repo):
            # When: the repository is inspected
            result = inspect_git_repository(str(repo))

        # Then: None is returned gracefully
        assert result is None, f"Expected None for ValueError on remote access, got {result}"


# ---------------------------------------------------------------------------
# TestDiscoverRepositories
# ---------------------------------------------------------------------------


class TestDiscoverRepositories:
    """
    REQUIREMENT: All Azure DevOps git repos under a root are discovered.

    WHO: Any consumer needing to enumerate repos in a workspace.
    WHAT: (1) if search_root itself is a git repo, returns a single-element list
          (2) a multi-repo workspace discovers all Azure DevOps children
          (3) an empty workspace returns an empty list
          (4) permission errors during scan return an empty list
          (5) a non-ADO git repo at search_root returns an empty list
          (6) a mixed workspace with non-ADO children skips them
    WHY: Multi-repo workspaces are common in enterprise environments —
         correct enumeration is the prerequisite for intelligent repo
         selection.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  discover_repositories function, inspect_git_repository
               (called internally), filesystem (tmp_path)
        Never: construct repo dicts directly — always obtain via
               discover_repositories()
    """

    def test_search_root_is_git_repo_returns_single_element(self, tmp_path: Path) -> None:
        """
        Given search_root is itself a git repository
        When discover_repositories is called
        Then a single-element list is returned
        """
        # Given: search_root has a .git folder
        repo = _make_git_repo(tmp_path, "MyRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo()):
            # When: repositories are discovered
            repos = discover_repositories(str(repo))

        # Then: exactly one repository is returned
        assert len(repos) == 1, f"Expected 1 repo when search_root is a git repo, got {len(repos)}"
        assert repos[0]["name"] == "MyRepo", (
            f"Expected repo name 'MyRepo', got '{repos[0]['name']}'"
        )

    def test_search_root_is_non_ado_git_repo_returns_empty(self, tmp_path: Path) -> None:
        """
        Given search_root is a git repo with a non-ADO remote (e.g. GitHub)
        When discover_repositories is called
        Then an empty list is returned (inspect_git_repository returns None)
        """
        # Given: search_root has a .git folder but remote is GitHub
        repo = _make_git_repo(tmp_path, "GhRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo(_GITHUB_REMOTE)):
            # When: repositories are discovered
            repos = discover_repositories(str(repo))

        # Then: empty list — non-ADO repo is not included
        assert repos == [], f"Expected empty list for non-ADO git repo at search_root, got {repos}"

    def test_multi_repo_workspace_discovers_all_git_children(self, tmp_path: Path) -> None:
        """
        Given search_root contains multiple git repository subdirectories
        When discover_repositories is called
        Then all Azure DevOps repos are included in the result
        """
        # Given: a workspace root with multiple child repos
        workspace = tmp_path
        _make_git_repo(workspace, "RepoA")
        _make_git_repo(workspace, "RepoB")
        (workspace / "not-a-repo").mkdir()  # no .git — skipped

        def repo_factory(path: str) -> MagicMock:
            if "RepoA" in path:
                return _mock_repo("https://dev.azure.com/ExampleOrg/ProjA/_git/RepoA")
            return _mock_repo("https://dev.azure.com/ExampleOrg/ProjB/_git/RepoB")

        with patch(_REPO_PATCH, side_effect=repo_factory):
            # When: repositories are discovered
            repos = discover_repositories(str(workspace))

        # Then: both ADO repos are found
        names = sorted(r["name"] for r in repos)
        assert len(repos) == 2, f"Expected 2 repos, got {len(repos)}: {names}"
        assert "RepoA" in names, f"Expected RepoA in {names}"
        assert "RepoB" in names, f"Expected RepoB in {names}"

    def test_mixed_workspace_skips_non_ado_children(self, tmp_path: Path) -> None:
        """
        Given a workspace with both ADO and non-ADO git repos
        When discover_repositories is called
        Then only the ADO repo is included; the non-ADO child is skipped
        """
        # Given: one ADO repo and one GitHub repo as children
        workspace = tmp_path
        _make_git_repo(workspace, "AdoRepo")
        _make_git_repo(workspace, "GhRepo")

        def repo_factory(path: str) -> MagicMock:
            if "AdoRepo" in path:
                return _mock_repo("https://dev.azure.com/ExampleOrg/Proj/_git/AdoRepo")
            return _mock_repo(_GITHUB_REMOTE)

        with patch(_REPO_PATCH, side_effect=repo_factory):
            # When: repositories are discovered
            repos = discover_repositories(str(workspace))

        # Then: only the ADO repo is found
        assert len(repos) == 1, f"Expected 1 ADO repo, got {len(repos)}"
        assert repos[0]["name"] == "AdoRepo", (
            f"Expected ADO repo name 'AdoRepo', got '{repos[0]['name']}'"
        )

    def test_empty_workspace_returns_empty_list(self, tmp_path: Path) -> None:
        """
        Given search_root has no git repositories
        When discover_repositories is called
        Then an empty list is returned
        """
        # Given: a workspace with only non-git directories
        workspace = tmp_path
        (workspace / "docs").mkdir()
        (workspace / "scripts").mkdir()

        # When: no .git dirs exist, discover_repositories won't call git
        repos = discover_repositories(str(workspace))

        # Then: empty list is returned
        assert repos == [], f"Expected empty list for workspace with no git repos, got {repos}"

    def test_permission_error_during_scan_returns_empty_list(self, tmp_path: Path) -> None:
        """
        Given search_root listing raises PermissionError
        When discover_repositories is called
        Then an empty list is returned gracefully
        """
        # Given: a directory we can't list
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)

        try:
            # When: repositories are discovered
            repos = discover_repositories(str(restricted))

            # Then: empty list rather than exception
            assert repos == [], f"Expected empty list on PermissionError, got {repos}"
        finally:
            restricted.chmod(0o755)


# ---------------------------------------------------------------------------
# TestInferTargetRepository
# ---------------------------------------------------------------------------


class TestInferTargetRepository:
    """
    REQUIREMENT: The most likely target repository is selected from a list.

    WHO: Any consumer needing to resolve which repo the user intends to
         work with.
    WHAT: (1) when working_directory is inside a repo's path, that repo is
              selected
          (2) when only one repo exists, it is selected automatically
          (3) an empty repository list returns None
          (4) when truly ambiguous (multiple repos, no match), None is returned
          (5) when no working_directory hint is given, os.getcwd() fallback
              selects the matching repo
    WHY: Multi-repo workspaces require intelligent default selection —
         forcing users to specify a repo for every operation is
         unacceptable UX.

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
        assert result is not None, "Expected repo selection for matching working_directory"
        assert result["name"] == "RepoB", f"Expected 'RepoB', got '{result['name']}'"

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
        assert result["name"] == "OnlyRepo", f"Expected 'OnlyRepo', got '{result['name']}'"

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
        assert result is None, f"Expected None for ambiguous selection, got {result}"

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
        assert result is not None, "Expected repo selection via cwd fallback"
        assert result["name"] == "RepoA", f"Expected 'RepoA' via cwd, got '{result['name']}'"

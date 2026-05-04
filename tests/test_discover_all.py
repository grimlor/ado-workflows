"""
BDD tests for ado_workflows.context — RepositoryContext.discover_all.

Covers:
    TestDiscoverAllRepositories — plural-aware discovery primitive

Public API surface:
    RepositoryContext.discover_all(working_directory=None)
        -> list[dict[str, Any]]
    discover_all_repositories(working_directory=None)
        -> list[dict[str, Any]]   (module-level delegate)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from ado_workflows.context import RepositoryContext, discover_all_repositories

if TYPE_CHECKING:
    from pathlib import Path


_REPO_PATCH = "ado_workflows.discovery.Repo"


def _mock_repo(remote_url: str) -> MagicMock:
    """Mock GitPython Repo with an origin remote."""
    repo = MagicMock()
    repo.remotes.origin.url = remote_url

    def _bool(_self: object) -> bool:
        return True

    def _len(_self: object) -> int:
        return 1

    repo.remotes.__bool__ = _bool
    repo.remotes.__len__ = _len
    return repo


def _make_repo_dir(workspace: Path, name: str) -> Path:
    """Create a directory containing ``.git`` under *workspace*."""
    repo = workspace / name
    (repo / ".git").mkdir(parents=True)
    return repo


class TestDiscoverAllRepositories:
    """
    REQUIREMENT: RepositoryContext.discover_all returns every Azure
    DevOps repository discovered under the supplied directory (or cwd),
    silently excludes non-ADO remotes, and always re-walks the filesystem.

    WHO: Callers that need to render or inspect every candidate repo —
        agents driving sub-prompts, status reporters, debug tooling.
    WHAT: (1) Multi-repo workspace returns a list whose length matches
              the number of ADO repos present.
          (2) GitHub-remote repos in the same workspace are silently
              omitted from the result.
          (3) Two consecutive calls each re-walk the filesystem (each
              call re-invokes discovery) — no caching.
          (4) Empty workspace returns an empty list (does not raise).
    WHY: Pluralising discovery is the foundation for the new
        ambiguity contract. Returning a list lets ambiguity be resolved
        outside the library when the agent surfaces candidates.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo.
        Real:  RepositoryContext.discover_all, discover_repositories,
              inspect_git_repository, parse_ado_url.
        Never: discover_repositories or inspect_git_repository.
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_multi_repo_workspace_returns_list_of_all_ado_repos(self, tmp_path: Path) -> None:
        """
        Given a workspace with two ADO repos in different orgs
        When discover_all is called against the workspace root
        Then both repos appear in the returned list
        """
        # Given: a workspace with two ADO repos
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo_a = _make_repo_dir(workspace, "RepoA")
        repo_b = _make_repo_dir(workspace, "RepoB")
        url_a = "https://dev.azure.com/OrgA/Proj/_git/RepoA"
        url_b = "https://dev.azure.com/OrgB/Proj/_git/RepoB"

        def _repo_for(path: str, *_args: object, **_kwargs: object) -> MagicMock:
            if path == str(repo_a):
                return _mock_repo(url_a)
            if path == str(repo_b):
                return _mock_repo(url_b)
            raise AssertionError(f"Unexpected path: {path}")

        # When: discover_all is called against the workspace root
        with patch(_REPO_PATCH, side_effect=_repo_for):
            result = RepositoryContext.discover_all(working_directory=str(workspace))

        # Then: both repos appear in the result
        assert len(result) == 2, f"Expected 2 repos, got {len(result)}: {result!r}"
        names = {r["name"] for r in result}
        assert names == {"RepoA", "RepoB"}, f"Expected {{RepoA, RepoB}}, got {names!r}"
        orgs = {r["organization"] for r in result}
        assert orgs == {"OrgA", "OrgB"}, f"Expected {{OrgA, OrgB}}, got {orgs!r}"

    def test_github_repo_in_workspace_is_silently_excluded(self, tmp_path: Path) -> None:
        """
        Given a workspace with one ADO repo and one GitHub repo
        When discover_all is called against the workspace root
        Then only the ADO repo appears in the returned list
        """
        # Given: a workspace with one ADO repo and one GitHub repo
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ado = _make_repo_dir(workspace, "AdoRepo")
        gh = _make_repo_dir(workspace, "GitHubRepo")
        ado_url = "https://dev.azure.com/Org/Proj/_git/AdoRepo"
        gh_url = "https://github.com/grimlor/ado-workflows"

        def _repo_for(path: str, *_args: object, **_kwargs: object) -> MagicMock:
            if path == str(ado):
                return _mock_repo(ado_url)
            if path == str(gh):
                return _mock_repo(gh_url)
            raise AssertionError(f"Unexpected path: {path}")

        # When: discover_all is called against the workspace root
        with patch(_REPO_PATCH, side_effect=_repo_for):
            result = RepositoryContext.discover_all(working_directory=str(workspace))

        # Then: only the ADO repo is included
        assert len(result) == 1, f"Expected only the ADO repo, got {len(result)}: {result!r}"
        assert result[0]["name"] == "AdoRepo", (
            f"Expected only AdoRepo, got '{result[0].get('name')}'"
        )

    def test_consecutive_calls_each_rewalk_filesystem(self, tmp_path: Path) -> None:
        """
        Given a workspace where the second call sees a different set of
            repos than the first (a new repo was added between calls)
        When discover_all is called twice
        Then the two return values differ — no cached list is reused
        """
        # Given: a workspace with a single ADO repo
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo_a = _make_repo_dir(workspace, "RepoA")
        url_a = "https://dev.azure.com/OrgA/Proj/_git/RepoA"

        def _repo_for(path: str, *_args: object, **_kwargs: object) -> MagicMock:
            if "RepoA" in path:
                return _mock_repo(url_a)
            if "RepoB" in path:
                return _mock_repo("https://dev.azure.com/OrgB/Proj/_git/RepoB")
            raise AssertionError(f"Unexpected path: {path}")

        del repo_a  # marker only

        # When: discover_all is called once on the single-repo workspace,
        # then a second time after a new repo is added
        with patch(_REPO_PATCH, side_effect=_repo_for):
            first = RepositoryContext.discover_all(working_directory=str(workspace))
            _make_repo_dir(workspace, "RepoB")
            second = RepositoryContext.discover_all(working_directory=str(workspace))

        # Then: the second call sees the new repo (proving fresh walk)
        assert len(first) == 1, f"Expected 1 repo on first call, got {len(first)}"
        assert len(second) == 2, (
            f"Expected 2 repos on second call (proving re-walk), got {len(second)}"
        )

    def test_empty_workspace_returns_empty_list(self, tmp_path: Path) -> None:
        """
        Given a directory containing no git repositories
        When discover_all is called
        Then an empty list is returned (no exception raised)
        """
        # Given: an empty directory (no .git children)
        empty = tmp_path / "empty"
        empty.mkdir()

        # When: discover_all is called
        result = RepositoryContext.discover_all(working_directory=str(empty))

        # Then: an empty list is returned
        assert result == [], f"Expected empty list, got {result!r}"

    def test_module_level_delegate_returns_same_result(self, tmp_path: Path) -> None:
        """
        Given a workspace with one ADO repo
        When discover_all_repositories (module-level) is called
        Then it returns the same shape as RepositoryContext.discover_all
        """
        # Given: one ADO repo
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = _make_repo_dir(workspace, "OnlyRepo")
        url = "https://dev.azure.com/Org/Proj/_git/OnlyRepo"
        del repo

        # When: both entry points are called
        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            via_class = RepositoryContext.discover_all(working_directory=str(workspace))
            via_module = discover_all_repositories(working_directory=str(workspace))

        # Then: they agree
        assert via_class == via_module, (
            f"Expected module-level delegate to mirror classmethod, "
            f"got class={via_class!r} module={via_module!r}"
        )

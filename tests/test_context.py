"""
BDD tests for ado_workflows.context — RepositoryContext state management.

Covers:
    TestContextSet — setting the active repository context
    TestContextGet — retrieving repository info (cached, fresh, intelligent)
    TestContextClear — clearing state
    TestContextStatus — debugging info
    TestContextThreadSafety — concurrent access
    TestContextErrorPaths — validation and discovery failures
    TestConvenienceFunctions — module-level wrappers
"""

from __future__ import annotations

from threading import Barrier, Thread
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError

if TYPE_CHECKING:
    from pathlib import Path

from ado_workflows.context import (
    RepositoryContext,
    clear_repository_context,
    get_context_status,
    get_repository_context,
    set_repository_context,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_PATCH = "ado_workflows.discovery.Repo"
_ADO_REMOTE = "https://dev.azure.com/ExampleOrg/MyProject/_git/{name}"
_ADO_REMOTE_2 = "https://dev.azure.com/ExampleOrg/OtherProject/_git/{name}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_repo(remote_url: str) -> MagicMock:
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


def _make_git_repo(
    workspace: Path,
    name: str,
    *,
    remote_url: str | None = None,
) -> tuple[Path, str]:
    """
    Create a directory with a ``.git`` marker inside *workspace*.

    Returns ``(repo_path, remote_url)`` for use with :func:`_mock_repo`.
    """
    repo = workspace / name
    (repo / ".git").mkdir(parents=True)
    url = remote_url or _ADO_REMOTE.format(name=name)
    return repo, url


def _set_context_via_public_api(
    workspace: Path,
    name: str = "my-repo",
    *,
    remote_url: str | None = None,
) -> Path:
    """
    Create a git repo dir, mock Repo at the I/O edge, then call set().

    Returns the repo directory path. All layers (discover_repositories,
    infer_target_repository, parse_ado_url) run for real.
    """
    repo_dir, url = _make_git_repo(workspace, name, remote_url=remote_url)
    with patch(_REPO_PATCH, return_value=_mock_repo(url)):
        RepositoryContext.set(str(repo_dir))
    return repo_dir


# ---------------------------------------------------------------------------
# TestContextSet
# ---------------------------------------------------------------------------


class TestContextSet:
    """
    REQUIREMENT: RepositoryContext.set validates and caches repository info.

    WHO: MCP tools that need a stable repository context for the session
    WHAT: (1) set() with a valid absolute directory discovers and caches the repo
          (2) set() with a relative path raises ActionableError (validation)
          (3) set() with a non-existent path raises ActionableError (not_found)
          (4) set() clears previous cache before discovering anew
          (5) set() raises ActionableError when discovery finds no ADO repos,
              and resets state so callers never operate on stale cache
          (6) when the workspace has multiple repos, the best match is cached
          (7) when infer cannot pick a best match, the first discovered repo
              is used as fallback
    WHY: Without validated context, downstream tools operate on stale or
         incorrect repository information. Failures must be raised so
         consumers do not silently index into error-shaped dicts.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  RepositoryContext state machine, discover_repositories,
               infer_target_repository, parse_ado_url, os.path.isabs,
               os.path.exists, tmp_path filesystem, ActionableError
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_set_with_valid_directory_caches_repo_info(self, tmp_path: Path) -> None:
        """
        Given a valid absolute directory containing an ADO git repo
        When set() is called
        Then the result indicates success and contains repository info
        """
        # Given: a directory with a .git folder and ADO remote
        repo_dir, url = _make_git_repo(tmp_path, "my-repo")

        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            # When: context is set
            result = RepositoryContext.set(str(repo_dir))

        # Then: success with repo info from real discovery
        assert result["success"] is True, f"Expected success, got: {result}"
        assert "repository_info" in result, f"Missing repository_info: {result}"
        assert result["repository_info"]["name"] == "my-repo", (
            f"Expected repo name 'my-repo', got: {result['repository_info'].get('name')}"
        )
        assert result["repository_info"]["organization"] == "ExampleOrg", (
            f"Expected org 'ExampleOrg', got: {result['repository_info'].get('organization')}"
        )
        assert result["repository_info"]["project"] == "MyProject", (
            f"Expected project 'MyProject', got: {result['repository_info'].get('project')}"
        )

    def test_set_with_relative_path_raises_validation_error(self) -> None:
        """
        Given a relative path string
        When set() is called
        Then ActionableError is raised with error_type='validation'
            and the error message names the working_directory field
        """
        # Given/When: relative path (os.path.isabs naturally returns False)
        with pytest.raises(ActionableError) as exc_info:
            RepositoryContext.set("relative/path")

        # Then: validation error naming working_directory
        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation', got: {err.error_type!r}"
        )
        assert "working_directory" in err.error, (
            f"Expected error to name 'working_directory' field, got: {err.error!r}"
        )

    def test_set_with_nonexistent_directory_raises_not_found(self, tmp_path: Path) -> None:
        """
        Given an absolute path that does not exist
        When set() is called
        Then ActionableError is raised with error_type='not_found'
        """
        # Given: an absolute path that does not exist on disk
        missing = tmp_path / "nonexistent"

        # When/Then: set is called and raises
        with pytest.raises(ActionableError) as exc_info:
            RepositoryContext.set(str(missing))

        # Then: not-found error
        assert exc_info.value.error_type == "not_found", (
            f"Expected error_type='not_found', got: {exc_info.value.error_type!r}"
        )

    def test_set_clears_previous_cache(self, tmp_path: Path) -> None:
        """
        Given a previously cached context for one repo
        When set() is called with a second repo directory
        Then the old cache is replaced with the new repo info
        """
        # Given: first repo context cached
        first_dir, first_url = _make_git_repo(tmp_path, "first-repo")
        second_dir, second_url = _make_git_repo(tmp_path, "second-repo")

        with patch(_REPO_PATCH, return_value=_mock_repo(first_url)):
            RepositoryContext.set(str(first_dir))

        # When: set with a different directory
        with patch(_REPO_PATCH, return_value=_mock_repo(second_url)):
            result = RepositoryContext.set(str(second_dir))

        # Then: new repo info cached
        assert result["repository_info"]["name"] == "second-repo", (
            f"Expected second-repo, got: {result['repository_info'].get('name')}"
        )

    def test_set_resets_on_empty_discovery(self, tmp_path: Path) -> None:
        """
        Given a directory with no git repos (or only non-ADO repos)
        When set() is called
        Then ActionableError is raised and the working directory is reset
        """
        # Given: a real directory with no .git children
        empty_dir = tmp_path / "no-repos"
        empty_dir.mkdir()

        # When/Then: set is called — real discover_repositories finds nothing
        with pytest.raises(ActionableError) as exc_info:
            RepositoryContext.set(str(empty_dir))

        # Then: not-found and state is clean (cache reset for next call)
        assert exc_info.value.error_type == "not_found", (
            f"Expected error_type='not_found', got: {exc_info.value.error_type!r}"
        )
        status = RepositoryContext.status()
        assert status["context_set"] is False, (
            f"Expected context_set=False after failed discovery, got: {status}"
        )

    def test_set_with_multi_repo_workspace_selects_best_match(self, tmp_path: Path) -> None:
        """
        Given a workspace with multiple ADO repos
        When set() is called with one repo's path
        Then that repo is cached (search_root itself is a git repo)
        """
        # Given: two repos in a workspace
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo_a, url_a = _make_git_repo(workspace, "repo-a")
        _repo_b, url_b = _make_git_repo(workspace, "repo-b")

        def repo_factory(path: str) -> MagicMock:
            if "repo-a" in path:
                return _mock_repo(url_a)
            return _mock_repo(url_b)

        # When: set from repo-a (search_root itself has .git → single result)
        with patch(_REPO_PATCH, side_effect=repo_factory):
            result = RepositoryContext.set(str(repo_a))

        # Then: repo-a selected
        assert result["success"] is True, f"Expected success, got: {result}"
        assert result["repository_info"]["name"] == "repo-a", (
            f"Expected repo-a as best match, got: {result['repository_info'].get('name')}"
        )

    def test_set_with_ambiguous_workspace_raises_actionable_error(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Given a workspace with multiple ADO repos and neither matches cwd
        When set() is called with the workspace root
        Then ActionableError.validation is raised; the cache is cleared;
            the error carries the candidate list in error.context
        """
        # Given: a workspace with two repos, cwd outside both
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _repo_a, url_a = _make_git_repo(workspace, "repo-a")
        _repo_b, url_b = _make_git_repo(workspace, "repo-b")

        def repo_factory(path: str) -> MagicMock:
            if "repo-a" in path:
                return _mock_repo(url_a)
            return _mock_repo(url_b)

        # When: set from the workspace root (not inside either repo)
        with (
            patch(_REPO_PATCH, side_effect=repo_factory),
            patch("os.getcwd", return_value="/unrelated"),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.set(str(workspace))

        # Then: validation error with candidate metadata in error.context
        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation' on ambiguity, got {err.error_type!r}"
        )
        assert err.context is not None, (
            "Expected err.context to carry candidate metadata, got None"
        )
        candidates = err.context.get("candidate_repositories")
        assert candidates is not None and len(candidates) == 2, (
            f"Expected 2 candidate repositories, got {candidates!r}"
        )
        # And: the cache is cleared (not poisoned by the failed set)
        status = RepositoryContext.status()
        assert status["context_set"] is False, (
            f"Expected context_set=False after failed set, got {status!r}"
        )


# ---------------------------------------------------------------------------
# TestContextGet
# ---------------------------------------------------------------------------


class TestContextGet:
    """
    REQUIREMENT: RepositoryContext.get returns cached or fresh repository info.

    WHO: MCP tool functions requesting the current repository context
    WHAT: (1) get() without arguments returns cached info when available
          (2) get() with an explicit directory performs fresh discovery
              without updating the primary cache
          (3) get() without arguments and no cache returns an intelligent
              discovery result using the current working directory
          (4) get() raises ActionableError when discovery cannot resolve a
              repository (no cache + no working_directory + cwd has no
              ADO repo, OR working_directory has no ADO repo)
          (5) get() with an override does not update the primary cache
          (6) get() uses os.getcwd() as the search root for intelligent discovery
    WHY: Caching avoids redundant git subprocess calls; explicit overrides
         enable multi-repo workflows. Failures must be raised so consumers
         do not silently index into error-shaped dicts.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary),
               os.getcwd (process state I/O — only when testing cwd fallback)
        Real:  RepositoryContext caching logic, metadata enrichment,
               discover_repositories, infer_target_repository, parse_ado_url,
               os.path.isabs, os.path.exists, tmp_path filesystem,
               ActionableError
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_get_returns_cached_info_when_context_set(self, tmp_path: Path) -> None:
        """
        Given context has been set via a valid ADO repo
        When get() is called without arguments
        Then the cached repository info is returned with source=cached
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: get without arguments (no mock needed — cached path)
        result = RepositoryContext.get()

        # Then: cached result with metadata
        assert result["name"] == "my-repo", f"Expected my-repo, got: {result.get('name')}"
        assert result.get("_context_source") == "cached", (
            f"Expected source=cached, got: {result.get('_context_source')}"
        )
        assert "_context_working_directory" in result, (
            f"Expected _context_working_directory in cached result: {list(result.keys())}"
        )

    def test_get_with_override_performs_fresh_discovery(self, tmp_path: Path) -> None:
        """
        Given cached context exists for one repo
        When get() is called with a different override directory
        Then fresh discovery is performed for the override directory
        """
        # Given: initial context for my-repo
        _set_context_via_public_api(tmp_path, "my-repo")

        # And: a second repo exists in the workspace
        override_dir, override_url = _make_git_repo(
            tmp_path,
            "other-repo",
            remote_url=_ADO_REMOTE_2.format(name="other-repo"),
        )

        # When: get with override — real discovery of override dir
        with patch(_REPO_PATCH, return_value=_mock_repo(override_url)):
            result = RepositoryContext.get(working_directory=str(override_dir))

        # Then: fresh result from override directory
        assert result["name"] == "other-repo", f"Expected other-repo, got: {result.get('name')}"
        assert result.get("_context_source") == "fresh_discovery", (
            f"Expected source=fresh_discovery, got: {result.get('_context_source')}"
        )

    def test_get_without_context_attempts_intelligent_discovery(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Given no context has been set
        When get() is called without arguments
        Then intelligent discovery is attempted using os.getcwd()
        """
        # Given: a repo on disk and cwd pointing to its parent
        repo_dir, url = _make_git_repo(tmp_path, "my-repo")

        with (
            patch("os.getcwd", return_value=str(repo_dir)),
            patch(_REPO_PATCH, return_value=_mock_repo(url)),
        ):
            # When: get without arguments — real discovery from cwd
            result = RepositoryContext.get()

        # Then: intelligent discovery result
        assert result["name"] == "my-repo", (
            f"Expected intelligent discovery result, got: {result.get('name')}"
        )
        assert result.get("_context_source") == "intelligent_discovery", (
            f"Expected source=intelligent_discovery, got: {result.get('_context_source')}"
        )

    def test_get_without_context_raises_when_discovery_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Given no context has been set and intelligent discovery finds no repos
        When get() is called
        Then ActionableError is raised with discovery failure detail in the
            error message
        """
        # Given: an empty directory with no .git — real discovery returns []
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with (
            patch("os.getcwd", return_value=str(empty_dir)),
            pytest.raises(ActionableError) as exc_info,
        ):
            # When/Then: get without context — real discovery finds nothing
            RepositoryContext.get()

        # Then: error_type is validation (caller can fix by supplying input)
        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation', got: {err.error_type!r}"
        )
        assert "No Azure DevOps repositories" in err.error, (
            f"Expected discovery failure detail in error message, got: {err.error!r}"
        )

    def test_get_override_does_not_update_cache(self, tmp_path: Path) -> None:
        """
        Given a cached context for one repo
        When get() is called with a different override directory
        Then the primary cache is not updated
        """
        # Given: initial context for my-repo
        _set_context_via_public_api(tmp_path, "my-repo")

        # And: a second repo exists in the workspace
        override_dir, override_url = _make_git_repo(
            tmp_path,
            "other-repo",
            remote_url=_ADO_REMOTE_2.format(name="other-repo"),
        )

        # When: get with override
        with patch(_REPO_PATCH, return_value=_mock_repo(override_url)):
            RepositoryContext.get(working_directory=str(override_dir))

        # Then: cache still holds original
        status = RepositoryContext.status()
        assert status["cached_repository"] == "my-repo", (
            f"Expected cache unchanged, got: {status.get('cached_repository')}"
        )

    def test_get_uses_cwd_for_intelligent_discovery(self, tmp_path: Path) -> None:
        """
        Given no context and no override directory
        When get() is called
        Then os.getcwd() is used as the search root for discovery
        """
        # Given: a repo on disk and cwd pointing to it
        repo_dir, url = _make_git_repo(tmp_path, "cwd-repo")

        with (
            patch("os.getcwd", return_value=str(repo_dir)),
            patch(_REPO_PATCH, return_value=_mock_repo(url)),
        ):
            # When: get is called
            result = RepositoryContext.get()

        # Then: discovery used the cwd — result is from cwd-repo
        assert result["name"] == "cwd-repo", (
            f"Expected cwd-repo from cwd-based discovery, got: {result.get('name')}"
        )


# ---------------------------------------------------------------------------
# TestContextClear
# ---------------------------------------------------------------------------


class TestContextClear:
    """
    REQUIREMENT: RepositoryContext.clear removes all cached state.

    WHO: Callers switching between repositories or resetting session state
    WHAT: (1) clear() removes the working directory, cached info, and timestamp
          (2) clear() returns the previous state for confirmation
          (3) clearing empty state succeeds gracefully
    WHY: Stale context leads to operations against the wrong repository

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary, for setup only)
        Real:  RepositoryContext.clear logic, discover_repositories,
               infer_target_repository, parse_ado_url, tmp_path filesystem
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_clear_removes_all_state(self, tmp_path: Path) -> None:
        """
        Given a cached context
        When clear() is called
        Then all state is removed
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: clear
        result = RepositoryContext.clear()

        # Then: state removed
        assert result["success"] is True, f"Expected success, got: {result}"
        status = RepositoryContext.status()
        assert status["context_set"] is False, f"Expected context_set=False, got: {status}"
        assert status["cache_available"] is False, f"Expected cache_available=False, got: {status}"

    def test_clear_returns_previous_state(self, tmp_path: Path) -> None:
        """
        Given a cached context
        When clear() is called
        Then the previous directory is returned
        """
        # Given: context set via real discovery
        repo_dir = _set_context_via_public_api(tmp_path, "my-repo")

        # When: clear
        result = RepositoryContext.clear()

        # Then: previous state in result
        assert result["previous_directory"] == str(repo_dir), (
            f"Expected previous directory '{repo_dir}', got: {result.get('previous_directory')}"
        )

    def test_clear_on_empty_state_succeeds(self) -> None:
        """
        Given no context has been set
        When clear() is called
        Then it succeeds with None as previous state
        """
        # Given: no context

        # When: clear
        result = RepositoryContext.clear()

        # Then: success with None previous
        assert result["success"] is True, f"Expected success, got: {result}"
        assert result["previous_directory"] is None, (
            f"Expected None previous_directory, got: {result.get('previous_directory')}"
        )


# ---------------------------------------------------------------------------
# TestContextStatus
# ---------------------------------------------------------------------------


class TestContextStatus:
    """
    REQUIREMENT: RepositoryContext.status provides debugging info.

    WHO: Developers and AI agents diagnosing context issues
    WHAT: (1) status() with no context reports no context and no cache
          (2) status() with active context reports the current directory
              and cached repo info
    WHY: Opaque state makes debugging multi-repo issues impossible

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary, for setup only)
        Real:  RepositoryContext.status logic, discover_repositories,
               infer_target_repository, parse_ado_url, tmp_path filesystem
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_status_when_no_context_set(self) -> None:
        """
        Given no context has been set
        When status() is called
        Then it reports no context and no cache
        """
        # Given: no context

        # When: status
        result = RepositoryContext.status()

        # Then: empty state
        assert result["context_set"] is False, f"Expected context_set=False, got: {result}"
        assert result["cache_available"] is False, f"Expected cache_available=False, got: {result}"
        assert result["current_working_directory"] is None, (
            f"Expected None directory, got: {result.get('current_working_directory')}"
        )

    def test_status_with_active_context(self, tmp_path: Path) -> None:
        """
        Given an active context
        When status() is called
        Then it reports the current directory and cached repo info
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: status
        result = RepositoryContext.status()

        # Then: active state with details
        assert result["context_set"] is True, f"Expected context_set=True, got: {result}"
        assert result["cache_available"] is True, f"Expected cache_available=True, got: {result}"
        assert result["cached_repository"] == "my-repo", (
            f"Expected cached_repository=my-repo, got: {result.get('cached_repository')}"
        )
        assert result["cached_organization"] == "ExampleOrg", (
            f"Expected cached_organization=ExampleOrg, got: {result.get('cached_organization')}"
        )
        assert result["cache_timestamp"] is not None, (
            f"Expected non-None timestamp, got: {result.get('cache_timestamp')}"
        )


# ---------------------------------------------------------------------------
# TestContextThreadSafety
# ---------------------------------------------------------------------------


class TestContextThreadSafety:
    """
    REQUIREMENT: RepositoryContext is safe for concurrent access.

    WHO: MCP servers handling concurrent tool calls
    WHAT: (1) concurrent set/get/clear operations do not corrupt state
    WHY: MCP servers may receive multiple tool calls simultaneously

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  RepositoryContext locking and state management, threading,
               discover_repositories, infer_target_repository, parse_ado_url,
               os.path.isabs, os.path.exists, tmp_path filesystem
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_concurrent_set_and_get_do_not_corrupt_state(self, tmp_path: Path) -> None:
        """
        Given multiple threads setting and getting context simultaneously
        When all threads complete
        Then the final state is consistent (no partial writes or corruption)
        """
        # Given: real directories with .git markers and synchronized start
        num_threads = 10
        barrier = Barrier(num_threads)
        results: list[dict[str, Any]] = []

        urls: dict[str, str] = {}
        for i in range(num_threads):
            repo_dir, url = _make_git_repo(tmp_path, f"repo-{i}")
            urls[str(repo_dir)] = url

        def repo_factory(path: str) -> MagicMock:
            """Return a mock Repo whose remote matches the repo directory."""
            for repo_path, url in urls.items():
                if repo_path in path or path in repo_path:
                    return _mock_repo(url)
            return _mock_repo(_ADO_REMOTE.format(name="fallback"))

        def worker(repo_dir: str) -> None:
            barrier.wait()
            RepositoryContext.set(repo_dir)
            result = RepositoryContext.get()
            results.append(result)

        # When: concurrent access with git.Repo mocked at the I/O edge
        with patch(_REPO_PATCH, side_effect=repo_factory):
            threads = [
                Thread(
                    target=worker,
                    args=(str(tmp_path / f"repo-{i}"),),
                )
                for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Then: all operations completed, final state is consistent
        assert len(results) == num_threads, f"Expected {num_threads} results, got {len(results)}"
        final_status = RepositoryContext.status()
        assert final_status["context_set"] is True, (
            f"Expected context_set=True, got: {final_status}"
        )
        assert final_status["cache_available"] is True, (
            f"Expected cache_available=True, got: {final_status}"
        )


# ---------------------------------------------------------------------------
# TestContextErrorPaths
# ---------------------------------------------------------------------------


class TestContextErrorPaths:
    """
    REQUIREMENT: RepositoryContext raises ActionableError on discovery failure.

    WHO: Callers that need structured error information; downstream code
         that catches ActionableError to produce user-facing diagnostics
    WHAT: (1) discovery exceptions are wrapped and raised as ActionableError
              (error_type='internal') with the original message preserved
          (2) OSError from discovery is wrapped with the original message
              preserved
          (3) empty repository lists raise ActionableError (error_type='not_found')
    WHY: Unstructured exceptions break MCP tool response contracts; raising
         ActionableError lets every consumer rely on a uniform failure shape
         with actionable guidance.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  RepositoryContext error wrapping logic, discover_repositories,
               infer_target_repository, parse_ado_url, os.path.isabs,
               os.path.exists, tmp_path filesystem, ActionableError
        Never: mock discover_repositories, infer_target_repository, or any
               of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_discovery_exception_is_wrapped(self, tmp_path: Path) -> None:
        """
        Given git.Repo raises an unexpected RuntimeError
        When set() is called
        Then ActionableError is raised with error_type='internal' and the
            original message preserved
        """
        # Given: a real directory with a .git marker, but Repo raises
        repo_dir = tmp_path / "broken-repo"
        (repo_dir / ".git").mkdir(parents=True)

        with (
            patch(_REPO_PATCH, side_effect=RuntimeError("git crashed")),
            pytest.raises(ActionableError) as exc_info,
        ):
            # When/Then: set is called — real discover_repositories calls Repo()
            RepositoryContext.set(str(repo_dir))

        # Then: structured error wrapping the underlying exception
        err = exc_info.value
        assert err.error_type == "internal", (
            f"Expected error_type='internal' for unexpected exception, got: {err.error_type!r}"
        )
        assert "git crashed" in err.error, (
            f"Expected original error message in err.error, got: {err.error!r}"
        )

    def test_discovery_os_error_is_wrapped(self, tmp_path: Path) -> None:
        """
        Given git.Repo raises an OSError
        When set() is called
        Then ActionableError is raised with the original message preserved
        """
        # Given: a real directory with a .git marker, but Repo raises OSError
        repo_dir = tmp_path / "locked-repo"
        (repo_dir / ".git").mkdir(parents=True)

        with (
            patch(_REPO_PATCH, side_effect=OSError("permission denied")),
            pytest.raises(ActionableError) as exc_info,
        ):
            # When/Then: set is called
            RepositoryContext.set(str(repo_dir))

        # Then: error includes original message
        assert "permission denied" in exc_info.value.error, (
            f"Expected error detail, got: {exc_info.value.error!r}"
        )

    def test_no_repos_found_raises_not_found(self, tmp_path: Path) -> None:
        """
        Given a directory with no git repositories
        When set() is called
        Then ActionableError is raised with error_type='not_found'
        """
        # Given: a real empty directory — no .git children
        empty_dir = tmp_path / "empty-workspace"
        empty_dir.mkdir()

        # When/Then: set is called — real discover_repositories returns []
        with pytest.raises(ActionableError) as exc_info:
            RepositoryContext.set(str(empty_dir))

        # Then: structured not-found error
        assert exc_info.value.error_type == "not_found", (
            f"Expected error_type='not_found', got: {exc_info.value.error_type!r}"
        )


# ---------------------------------------------------------------------------
# TestDiscoveryFailureGuidance
# ---------------------------------------------------------------------------


class TestDiscoveryFailureGuidance:
    """
    REQUIREMENT: ActionableError raised by get()/set() on discovery failure
    carries ai_guidance that names the agent-executable remedies and the
    inputs the agent can validate before retrying.

    WHO: AI agents consuming RepositoryContext via the MCP layer. The
         original buggy guidance ("verify branches and az login") sent
         agents into retry loops with the wrong remedy. This test pins
         the new contract so future regressions break loudly.
    WHAT: (1) ai_guidance is non-None on every discovery failure
          (2) action_required names BOTH agent-executable remedies:
              the working_directory parameter AND set_repository_context
          (3) at least one of checks/steps is non-empty (an agent must
              have something to validate or sequence before retrying)
          (4) guidance does NOT instruct the agent to run interactive
              human-only commands like 'az login' (these must be phrased
              as 'ask the user to ...' if needed; absence is fine here
              because authentication is not the cause of context-discovery
              failures).
    WHY: ai_guidance is a structured cue for the next automated step.
         Wrong guidance is worse than no guidance \u2014 it leads agents
         to take incorrect corrective actions and never converge on
         the real fix.

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython \u2014 the only I/O boundary),
               os.getcwd (process state I/O \u2014 only when testing cwd fallback)
        Real:  RepositoryContext, discover_repositories,
               infer_target_repository, parse_ado_url, ActionableError,
               AIGuidance, tmp_path filesystem
        Never: mock any function in ado_workflows.*; never construct
               an ActionableError directly in test code (assertions are
               on what RepositoryContext raises)
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_get_failure_carries_ai_guidance(self, tmp_path: Path) -> None:
        """
        Given no cache and cwd has no ADO repository
        When get() is called
        Then the raised ActionableError has a non-None ai_guidance
        """
        # Given: an empty directory as cwd
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with (
            patch("os.getcwd", return_value=str(empty_dir)),
            pytest.raises(ActionableError) as exc_info,
        ):
            # When/Then: get() raises with guidance
            RepositoryContext.get()

        # Then: ai_guidance is present
        assert exc_info.value.ai_guidance is not None, (
            f"Expected ai_guidance to be present on context-discovery failure, "
            f"got None. Error: {exc_info.value.error!r}"
        )

    def test_get_failure_action_required_names_both_remedies(self, tmp_path: Path) -> None:
        """
        Given a discovery failure from get()
        When the raised ActionableError is inspected
        Then ai_guidance.action_required mentions both 'working_directory'
            and 'set_repository_context' (the two agent-executable remedies)
        """
        # Given: an empty directory as cwd
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        # When: get() raises
        with (
            patch("os.getcwd", return_value=str(empty_dir)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        # Then: both remedies named in action_required
        guidance = exc_info.value.ai_guidance
        assert guidance is not None, "ai_guidance must be present"
        action = guidance.action_required
        assert "working_directory" in action, (
            f"Expected action_required to name 'working_directory' remedy, got: {action!r}"
        )
        assert "set_repository_context" in action, (
            f"Expected action_required to name 'set_repository_context' remedy, got: {action!r}"
        )

    def test_get_failure_provides_actionable_checks_or_steps(self, tmp_path: Path) -> None:
        """
        Given a discovery failure from get()
        When the raised ActionableError is inspected
        Then at least one of ai_guidance.checks / ai_guidance.steps is
            non-empty so the agent has something to validate or sequence
            before retrying
        """
        # Given: an empty directory as cwd
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        # When: get() raises
        with (
            patch("os.getcwd", return_value=str(empty_dir)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        # Then: checks or steps populated
        guidance = exc_info.value.ai_guidance
        assert guidance is not None, "ai_guidance must be present"
        checks = guidance.checks or []
        steps = guidance.steps or []
        assert checks or steps, (
            f"Expected at least one of checks/steps to be non-empty so "
            f"the agent has actionable next steps. Got checks={checks!r}, "
            f"steps={steps!r}"
        )

    def test_get_failure_does_not_instruct_agent_to_run_interactive_commands(
        self, tmp_path: Path
    ) -> None:
        """
        Given a discovery failure from get()
        When the raised ActionableError is inspected
        Then guidance does NOT directly instruct the agent to run human-only
            interactive commands (the original bug surfaced 'az login' as a
            direct instruction, which agents cannot complete; authentication
            is not the cause of context-discovery failures)
        """
        # Given: an empty directory as cwd
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        # When: get() raises
        with (
            patch("os.getcwd", return_value=str(empty_dir)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        # Then: no direct 'az login' instruction
        guidance = exc_info.value.ai_guidance
        assert guidance is not None, "ai_guidance must be present"

        # Concatenate the entire guidance surface
        all_text_parts: list[str] = [guidance.action_required]
        all_text_parts.extend(guidance.checks or [])
        all_text_parts.extend(guidance.steps or [])
        all_text = " ".join(all_text_parts).lower()

        # The original bug wrote 'az login' as a direct directive. If 'az login'
        # appears at all, it must be qualified by 'ask the user' to phrase it
        # as a human handoff. Bare 'az login' is forbidden.
        if "az login" in all_text:
            assert "ask the user" in all_text, (
                f"If guidance mentions 'az login' it must be phrased as "
                f"'ask the user' (human handoff), not as a direct agent "
                f"instruction. Got: {all_text!r}"
            )

    def test_set_with_no_ado_repos_carries_ai_guidance(self, tmp_path: Path) -> None:
        """
        Given an existing directory containing no ADO repositories
        When set() is called and raises
        Then the raised ActionableError carries non-None ai_guidance with
            a non-empty action_required
        """
        # Given: a real directory with no .git children
        empty_dir = tmp_path / "no-repos"
        empty_dir.mkdir()

        # When/Then: set() raises with guidance
        with pytest.raises(ActionableError) as exc_info:
            RepositoryContext.set(str(empty_dir))

        # Then: guidance is present and substantive
        guidance = exc_info.value.ai_guidance
        assert guidance is not None, (
            f"Expected ai_guidance on set() not_found failure, got None. "
            f"Error: {exc_info.value.error!r}"
        )
        assert guidance.action_required, (
            f"Expected non-empty action_required, got: {guidance.action_required!r}"
        )


# ---------------------------------------------------------------------------
# TestConvenienceFunctions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """
    REQUIREMENT: Module-level convenience functions delegate to RepositoryContext.

    WHO: Callers preferring a functional API over classmethods
    WHAT: (1) set_repository_context produces the same result as RepositoryContext.set
          (2) get_repository_context produces the same result as RepositoryContext.get
          (3) get_context_status produces the same result as RepositoryContext.status
          (4) clear_repository_context produces the same result as RepositoryContext.clear
    WHY: Code often uses import-and-call style; convenience functions must be
         behaviorally identical to the classmethods they wrap

    MOCK BOUNDARY:
        Mock:  git.Repo (GitPython — the only I/O boundary)
        Real:  All convenience functions, RepositoryContext, discover_repositories,
               infer_target_repository, parse_ado_url, tmp_path filesystem
        Never: mock RepositoryContext classmethods, discover_repositories,
               infer_target_repository, or any of our own functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_set_repository_context_sets_context(self, tmp_path: Path) -> None:
        """
        Given a valid repo directory
        When set_repository_context() is called
        Then context is set with repository info matching the directory
        """
        # Given: a directory with a .git folder and ADO remote
        repo_dir, url = _make_git_repo(tmp_path, "my-repo")

        # When: call the convenience function
        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            result = set_repository_context(str(repo_dir))

        # Then: context is set
        assert result["success"] is True, f"Expected success, got: {result}"
        assert result["repository_info"]["name"] == "my-repo", (
            f"Expected my-repo, got: {result['repository_info'].get('name')}"
        )

    def test_get_repository_context_returns_cached(self, tmp_path: Path) -> None:
        """
        Given context has been set
        When get_repository_context() is called
        Then the cached repository info is returned
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: call the convenience function
        result = get_repository_context()

        # Then: cached result
        assert result["name"] == "my-repo", f"Expected my-repo, got: {result.get('name')}"
        assert result.get("_context_source") == "cached", (
            f"Expected source=cached, got: {result.get('_context_source')}"
        )

    def test_get_context_status_reports_state(self, tmp_path: Path) -> None:
        """
        Given context has been set
        When get_context_status() is called
        Then the status reflects the active context
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: call the convenience function
        result = get_context_status()

        # Then: status reflects active context
        assert result["context_set"] is True, f"Expected context_set=True, got: {result}"
        assert result["cached_repository"] == "my-repo", (
            f"Expected cached_repository=my-repo, got: {result.get('cached_repository')}"
        )

    def test_clear_repository_context_clears_state(self, tmp_path: Path) -> None:
        """
        Given context has been set
        When clear_repository_context() is called
        Then all state is cleared
        """
        # Given: context set via real discovery
        _set_context_via_public_api(tmp_path, "my-repo")

        # When: call the convenience function
        result = clear_repository_context()

        # Then: state cleared
        assert result["success"] is True, f"Expected success, got: {result}"
        status = RepositoryContext.status()
        assert status["context_set"] is False, f"Expected context_set=False, got: {status}"

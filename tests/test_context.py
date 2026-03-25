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

from pathlib import Path
from threading import Barrier, Thread
from typing import Any
from unittest.mock import patch

from ado_workflows.context import (
    RepositoryContext,
    clear_repository_context,
    get_context_status,
    get_repository_context,
    set_repository_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_info(
    path: str = "/workspace/my-repo",
    name: str = "my-repo",
    organization: str = "ExampleOrg",
    project: str = "MyProject",
) -> dict[str, Any]:
    """Build a realistic repo-info dict for test fixtures."""
    return {
        "path": path,
        "name": name,
        "organization": organization,
        "project": project,
        "remote_url": (f"https://dev.azure.com/{organization}/{project}/_git/{name}"),
        "org_url": f"https://dev.azure.com/{organization}",
        "workspace_context": {
            "is_multi_repo_workspace": False,
            "workspace_root": str(Path(path).parent),
            "repository_relative_path": name,
        },
    }


# Pre-built fixtures for the most common scenarios
_SAMPLE = _repo_info()
_SECOND = _repo_info(
    path="/workspace/other-repo",
    name="other-repo",
    project="OtherProject",
)


# ---------------------------------------------------------------------------
# TestContextSet
# ---------------------------------------------------------------------------


class TestContextSet:
    """
    REQUIREMENT: RepositoryContext.set validates and caches repository info.

    WHO: MCP tools that need a stable repository context for the session
    WHAT: (1) set() with a valid absolute directory discovers and caches the repo
          (2) set() with a relative path returns a validation error
          (3) set() with a non-existent path returns a not-found error
          (4) set() clears previous cache before discovering anew
          (5) set() resets state when discovery fails
          (6) when infer_target_repository returns None the first discovered
              repo is used as fallback
    WHY: Without validated context, downstream tools operate on stale or
         incorrect repository information

    MOCK BOUNDARY:
        Mock:  discover_repositories, infer_target_repository (Layer 1 I/O)
        Real:  RepositoryContext state machine, os.path.isabs, os.path.exists,
               ActionableError construction, tmp_path filesystem
        Never: Make real git subprocess calls or mock os.path pure functions
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
        # Given: a real directory on disk
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()

        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            # When: context is set
            result = RepositoryContext.set(str(repo_dir))

        # Then: success with repo info
        assert result["success"] is True, f"Expected success, got: {result}"
        assert "repository_info" in result, f"Missing repository_info: {result}"
        assert result["repository_info"]["name"] == "my-repo", (
            f"Expected repo name 'my-repo', got: {result['repository_info'].get('name')}"
        )

    def test_set_with_relative_path_returns_validation_error(self) -> None:
        """
        Given a relative path
        When set() is called
        Then a validation error is returned
        """
        # Given/When: relative path (os.path.isabs naturally returns False)
        result = RepositoryContext.set("relative/path")

        # Then: validation error
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert result["error_type"] == "validation", (
            f"Expected validation error, got: {result.get('error_type')}"
        )

    def test_set_with_nonexistent_directory_returns_not_found(self, tmp_path: Path) -> None:
        """
        Given an absolute path that does not exist
        When set() is called
        Then a not-found error is returned
        """
        # Given: an absolute path that does not exist on disk
        missing = tmp_path / "nonexistent"

        # When: set is called
        result = RepositoryContext.set(str(missing))

        # Then: not-found error
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert result["error_type"] == "not_found", (
            f"Expected not_found error, got: {result.get('error_type')}"
        )

    def test_set_clears_previous_cache(self, tmp_path: Path) -> None:
        """
        Given a previously cached context
        When set() is called with a new directory
        Then the old cache is replaced
        """
        # Given: existing cached context
        first_dir = tmp_path / "my-repo"
        first_dir.mkdir()
        second_dir = tmp_path / "other-repo"
        second_dir.mkdir()

        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            RepositoryContext.set(str(first_dir))

        # When: new context set
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SECOND],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SECOND,
            ),
        ):
            result = RepositoryContext.set(str(second_dir))

        # Then: new repo info cached
        assert result["repository_info"]["name"] == "other-repo", (
            f"Expected other-repo, got: {result['repository_info'].get('name')}"
        )

    def test_set_resets_on_discovery_failure(self, tmp_path: Path) -> None:
        """
        Given a directory where discovery returns a failure dict
        When set() is called
        Then the working directory is reset to None
        """
        # Given: a real directory but discovery returns failure
        repo_dir = tmp_path / "bad-repo"
        repo_dir.mkdir()
        failure: dict[str, Any] = {
            "success": False,
            "error": "no remote",
            "error_type": "not_found",
        }

        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[failure],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=failure,
            ),
        ):
            # When: set is called
            result = RepositoryContext.set(str(repo_dir))

        # Then: error returned and state is clean
        assert result["success"] is False, f"Expected failure, got: {result}"
        status = RepositoryContext.status()
        assert status["context_set"] is False, (
            f"Expected context_set=False after failed discovery, got: {status}"
        )

    def test_set_uses_first_repo_when_infer_returns_none(self, tmp_path: Path) -> None:
        """
        Given discover_repositories returns repos but infer returns None
        When set() is called
        Then the first discovered repo is used as fallback
        """
        # Given: a real directory, discovery returns repos, infer returns None
        repo_dir = tmp_path / "workspace"
        repo_dir.mkdir()

        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE, _SECOND],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=None,
            ),
        ):
            # When: set is called
            result = RepositoryContext.set(str(repo_dir))

        # Then: first repo used as fallback
        assert result["success"] is True, f"Expected success, got: {result}"
        assert result["repository_info"]["name"] == "my-repo", (
            f"Expected first repo 'my-repo' as fallback, "
            f"got: {result['repository_info'].get('name')}"
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
          (4) get() without context returns an error when discovery fails
          (5) get() with an override does not update the primary cache
          (6) get() populates the cache on a primary-context miss
          (7) get() uses os.getcwd() as the search root for intelligent discovery
    WHY: Caching avoids redundant git subprocess calls; explicit overrides
         enable multi-repo workflows

    MOCK BOUNDARY:
        Mock:  discover_repositories, infer_target_repository (Layer 1 I/O),
               os.getcwd (process state I/O — only when testing cwd fallback)
        Real:  RepositoryContext caching logic, metadata enrichment,
               os.path.isabs, os.path.exists, tmp_path filesystem
        Never: Make real git subprocess calls or mock os.path pure functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def _set_context(self, directory: str) -> None:
        """Set context via public API with standard Layer 1 mocks."""
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            RepositoryContext.set(directory)

    def test_get_returns_cached_info_when_context_set(self, tmp_path: Path) -> None:
        """
        Given context has been set
        When get() is called without arguments
        Then the cached repository info is returned with source=cached
        """
        # Given: context set via a real directory
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

        # When: get without arguments (no Layer 1 mock needed — cached)
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
        Given cached context exists
        When get() is called with an explicit directory
        Then fresh discovery is performed for the override directory
        """
        # Given: initial context
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

        # When: get with override — fresh discovery for override dir
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SECOND],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SECOND,
            ),
        ):
            result = RepositoryContext.get(working_directory="/workspace/other-repo")

        # Then: fresh result from override directory
        assert result["name"] == "other-repo", f"Expected other-repo, got: {result.get('name')}"
        assert result.get("_context_source") == "fresh_discovery", (
            f"Expected source=fresh_discovery, got: {result.get('_context_source')}"
        )

    def test_get_without_context_attempts_intelligent_discovery(
        self,
    ) -> None:
        """
        Given no context has been set
        When get() is called without arguments
        Then intelligent discovery is attempted using os.getcwd()
        """
        # Given: no context set, mock cwd and Layer 1
        with (
            patch("os.getcwd", return_value="/home/user/workspace"),
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            # When: get without arguments
            result = RepositoryContext.get()

        # Then: intelligent discovery result
        assert result["name"] == "my-repo", (
            f"Expected intelligent discovery result, got: {result.get('name')}"
        )
        assert result.get("_context_source") == "intelligent_discovery", (
            f"Expected source=intelligent_discovery, got: {result.get('_context_source')}"
        )

    def test_get_without_context_returns_error_when_discovery_fails(
        self,
    ) -> None:
        """
        Given no context has been set and intelligent discovery finds no repos
        When get() is called
        Then a validation error is returned with discovery failure detail
        """
        # Given: discovery returns no repos
        with (
            patch("os.getcwd", return_value="/empty"),
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[],
            ),
        ):
            # When: get without context
            result = RepositoryContext.get()

        # Then: validation error with underlying cause
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert "No Azure DevOps repositories" in result.get("error", ""), (
            f"Expected discovery failure detail in error, got: {result.get('error')}"
        )

    def test_get_override_does_not_update_cache(self, tmp_path: Path) -> None:
        """
        Given a cached context for one repo
        When get() is called with a different override directory
        Then the primary cache is not updated
        """
        # Given: initial context
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

        # When: get with override
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SECOND],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SECOND,
            ),
        ):
            RepositoryContext.get(working_directory="/workspace/other-repo")

        # Then: cache still holds original
        status = RepositoryContext.status()
        assert status["cached_repository"] == "my-repo", (
            f"Expected cache unchanged, got: {status.get('cached_repository')}"
        )

    def test_get_populates_cache_on_primary_miss(self, tmp_path: Path) -> None:
        """
        Given the working directory is set but cached_info is None
        When get() is called without arguments
        Then fresh discovery runs and the cache is populated

        Note: This state (_working_directory set, _cached_info None)
        is not reachable through the public API alone. This test covers
        the defensive cache-population path in get().
        """
        # Given: working directory set but no cached info (defensive path)
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        # Test the defensive cache-population path; no public API to set
        # working_directory without populating the cache.
        RepositoryContext._working_directory = str(repo_dir)  # pyright: ignore[reportPrivateUsage]

        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            # When: get without arguments
            result = RepositoryContext.get()

        # Then: fresh discovery result
        assert result["name"] == "my-repo", f"Expected my-repo, got: {result.get('name')}"
        assert result.get("_context_source") == "fresh_discovery", (
            f"Expected fresh_discovery, got: {result.get('_context_source')}"
        )
        # And cache is now populated
        status = RepositoryContext.status()
        assert status["cache_available"] is True, (
            f"Expected cache_available=True after miss, got: {status}"
        )
        assert status["cache_timestamp"] is not None, (
            f"Expected cache_timestamp set, got: {status.get('cache_timestamp')}"
        )

    def test_get_uses_cwd_for_intelligent_discovery(self) -> None:
        """
        Given no context and no override directory
        When get() is called
        Then os.getcwd() is used as the search root for discover_repositories
        """
        # Given: no context, cwd is a known value
        with (
            patch("os.getcwd", return_value="/home/user/projects"),
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ) as mock_discover,
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            # When: get is called
            RepositoryContext.get()

        # Then: discover_repositories was called with the cwd
        mock_discover.assert_called_once_with("/home/user/projects")


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
        Mock:  discover_repositories, infer_target_repository (for setup only)
        Real:  RepositoryContext.clear logic, tmp_path filesystem
        Never: N/A
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def _set_context(self, directory: str) -> None:
        """Set context via public API with standard Layer 1 mocks."""
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            RepositoryContext.set(directory)

    def test_clear_removes_all_state(self, tmp_path: Path) -> None:
        """
        Given a cached context
        When clear() is called
        Then all state is removed
        """
        # Given: context set
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

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
        # Given: context set
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

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
        Mock:  discover_repositories, infer_target_repository (for setup only)
        Real:  RepositoryContext.status logic, tmp_path filesystem
        Never: N/A
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
        # Given: context set
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                return_value=[_SAMPLE],
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                return_value=_SAMPLE,
            ),
        ):
            RepositoryContext.set(str(repo_dir))

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
        Mock:  discover_repositories, infer_target_repository (Layer 1 I/O)
        Real:  RepositoryContext locking and state management, threading,
               os.path.isabs, os.path.exists, tmp_path filesystem
        Never: Make real git subprocess calls
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
        # Given: real directories and synchronized start
        num_threads = 10
        barrier = Barrier(num_threads)
        results: list[dict[str, Any]] = []

        for i in range(num_threads):
            (tmp_path / f"repo-{i}").mkdir()

        def _mock_discover(search_root: str) -> list[dict[str, Any]]:
            name = Path(search_root).name
            return [_repo_info(path=search_root, name=name)]

        def _mock_infer(
            repos: list[dict[str, Any]],
            working_directory: str | None = None,
        ) -> dict[str, Any] | None:
            return repos[0] if repos else None

        def worker(repo_dir: str) -> None:
            barrier.wait()
            RepositoryContext.set(repo_dir)
            result = RepositoryContext.get()
            results.append(result)

        # When: concurrent access with shared Layer 1 mocks
        with (
            patch(
                "ado_workflows.context.discover_repositories",
                side_effect=_mock_discover,
            ),
            patch(
                "ado_workflows.context.infer_target_repository",
                side_effect=_mock_infer,
            ),
        ):
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
    REQUIREMENT: RepositoryContext returns ActionableError dicts on failure.

    WHO: Callers that need structured error information
    WHAT: (1) discovery exceptions are wrapped in ActionableError dicts
          (2) OSError from discovery is wrapped with the original message
          (3) empty repository lists produce structured not-found errors
    WHY: Unstructured exceptions break MCP tool response contracts

    MOCK BOUNDARY:
        Mock:  discover_repositories (Layer 1 I/O)
        Real:  RepositoryContext error wrapping logic, os.path.isabs,
               os.path.exists, tmp_path filesystem
        Never: Mock os.path pure functions
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_discovery_exception_is_wrapped(self, tmp_path: Path) -> None:
        """
        Given discover_repositories raises an unexpected RuntimeError
        When set() is called
        Then the error is wrapped in an ActionableError dict
        """
        # Given: a real directory but discovery raises
        repo_dir = tmp_path / "broken-repo"
        repo_dir.mkdir()

        with patch(
            "ado_workflows.context.discover_repositories",
            side_effect=RuntimeError("git crashed"),
        ):
            # When: set is called
            result = RepositoryContext.set(str(repo_dir))

        # Then: structured error
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert "git crashed" in result.get("error", ""), (
            f"Expected original error message, got: {result.get('error')}"
        )

    def test_discovery_os_error_is_wrapped(self, tmp_path: Path) -> None:
        """
        Given discover_repositories raises an OSError
        When set() is called
        Then the error is wrapped with the original message preserved
        """
        # Given: a real directory but discovery raises OSError
        repo_dir = tmp_path / "locked-repo"
        repo_dir.mkdir()

        with patch(
            "ado_workflows.context.discover_repositories",
            side_effect=OSError("permission denied"),
        ):
            # When: set is called
            result = RepositoryContext.set(str(repo_dir))

        # Then: error includes original message
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert "permission denied" in result.get("error", ""), (
            f"Expected error detail, got: {result.get('error')}"
        )

    def test_no_repos_found_returns_structured_error(self, tmp_path: Path) -> None:
        """
        Given discover_repositories returns an empty list
        When set() is called
        Then a not-found error is returned naming the search root
        """
        # Given: a real directory but no repos found
        repo_dir = tmp_path / "empty-workspace"
        repo_dir.mkdir()

        with patch(
            "ado_workflows.context.discover_repositories",
            return_value=[],
        ):
            # When: set is called
            result = RepositoryContext.set(str(repo_dir))

        # Then: structured not-found error
        assert result["success"] is False, f"Expected failure, got: {result}"
        assert result["error_type"] == "not_found", (
            f"Expected not_found error, got: {result.get('error_type')}"
        )


# ---------------------------------------------------------------------------
# TestConvenienceFunctions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """
    REQUIREMENT: Module-level convenience functions delegate to RepositoryContext.

    WHO: Callers preferring a functional API over classmethods
    WHAT: (1) set_repository_context delegates to RepositoryContext.set
          (2) get_repository_context delegates to RepositoryContext.get
          (3) get_context_status delegates to RepositoryContext.status
          (4) clear_repository_context delegates to RepositoryContext.clear
    WHY: Code often uses import-and-call style; convenience functions match
         the existing public API

    MOCK BOUNDARY:
        Mock:  RepositoryContext classmethods (public API boundary)
        Real:  Convenience function delegation
        Never: N/A
    """

    def setup_method(self) -> None:
        """Reset global state via the public API."""
        RepositoryContext.clear()

    def test_set_repository_context_delegates(self) -> None:
        """
        When set_repository_context() is called
        Then it delegates to RepositoryContext.set()
        """
        # Given/When: call the convenience function
        with patch.object(RepositoryContext, "set", return_value={"success": True}) as mock_set:
            result = set_repository_context("/workspace/repo")

        # Then: delegation verified
        mock_set.assert_called_once_with("/workspace/repo")
        assert result["success"] is True, f"Expected delegation result, got: {result}"

    def test_get_repository_context_delegates(self) -> None:
        """
        When get_repository_context() is called
        Then it delegates to RepositoryContext.get()
        """
        # Given/When: call the convenience function
        with patch.object(RepositoryContext, "get", return_value={"name": "r"}) as mock_get:
            result = get_repository_context("/override")

        # Then: delegation verified
        mock_get.assert_called_once_with("/override")
        assert result["name"] == "r", f"Expected delegation result, got: {result}"

    def test_get_context_status_delegates(self) -> None:
        """
        When get_context_status() is called
        Then it delegates to RepositoryContext.status()
        """
        # Given/When: call the convenience function
        expected: dict[str, Any] = {"context_set": False}
        with patch.object(RepositoryContext, "status", return_value=expected) as mock_status:
            result = get_context_status()

        # Then: delegation verified
        mock_status.assert_called_once()
        assert result == expected, f"Expected delegation result, got: {result}"

    def test_clear_repository_context_delegates(self) -> None:
        """
        When clear_repository_context() is called
        Then it delegates to RepositoryContext.clear()
        """
        # Given/When: call the convenience function
        expected: dict[str, Any] = {"success": True, "message": "cleared"}
        with patch.object(RepositoryContext, "clear", return_value=expected) as mock_clear:
            result = clear_repository_context()

        # Then: delegation verified
        mock_clear.assert_called_once()
        assert result == expected, f"Expected delegation result, got: {result}"

"""Layer 2 — Repository context management with caching and thread-safety.

Provides session-level repository context so that multiple tool calls within
a single MCP session share the same discovered repository information without
redundant ``git`` subprocess calls.

Typical usage::

    from ado_workflows.context import RepositoryContext

    result = RepositoryContext.set("/workspace/my-repo")
    info   = RepositoryContext.get()          # cached
    status = RepositoryContext.status()       # debug info
    RepositoryContext.clear()                 # reset
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from actionable_errors import ActionableError, from_exception

from ado_workflows.discovery import discover_repositories, infer_target_repository

_SERVICE = "Azure DevOps"


class RepositoryContext:
    """Thread-safe, session-level repository context manager.

    All public methods are classmethods operating on class-level state
    (effectively a singleton).  Every method acquires ``_lock`` before
    reading or writing state.

    State consists of three class variables:

    * ``_working_directory`` — the absolute path last passed to :meth:`set`
    * ``_cached_info`` — the full repo-info dict from discovery
    * ``_cache_timestamp`` — ISO-8601 string of when the cache was populated
    """

    _working_directory: str | None = None
    _cached_info: dict[str, Any] | None = None
    _cache_timestamp: str | None = None
    _lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def set(cls, working_directory: str) -> dict[str, Any]:
        """Set the active repository context for subsequent tool operations.

        Validates the path, runs discovery, and caches the result.  On
        failure the previous context is cleared so callers never operate
        against stale data.
        """
        with cls._lock:
            # Validate: must be absolute
            if not os.path.isabs(working_directory):
                return ActionableError.validation(
                    service=_SERVICE,
                    field_name="working_directory",
                    reason=f"Must be an absolute path, got: {working_directory}",
                    suggestion="Provide the full absolute path to the repository.",
                ).to_dict()

            # Validate: must exist
            if not os.path.exists(working_directory):
                return ActionableError.not_found(
                    service="File System",
                    resource_type="Directory",
                    resource_id=working_directory,
                    raw_error="Directory does not exist",
                ).to_dict()

            # Clear previous cache
            cls._working_directory = working_directory
            cls._cached_info = None
            cls._cache_timestamp = None

            # Discover
            try:
                repo_info = cls._discover(working_directory)
            except Exception as exc:
                cls._working_directory = None
                return from_exception(
                    exc,
                    service=_SERVICE,
                    operation="repository_discovery",
                    suggestion="Verify git repository and remote configuration.",
                ).to_dict()

            if not repo_info.get("success", True):
                # Discovery returned an error dict
                cls._working_directory = None
                return ActionableError.not_found(
                    service=_SERVICE,
                    resource_type="Repository",
                    resource_id=working_directory,
                    raw_error=repo_info.get("error", "Unknown discovery error"),
                ).to_dict()

            cls._cached_info = repo_info
            cls._cache_timestamp = datetime.now(tz=UTC).isoformat()

            return {
                "success": True,
                "message": f"Repository context set to: {working_directory}",
                "repository_info": repo_info,
                "context_timestamp": cls._cache_timestamp,
            }

    @classmethod
    def get(cls, working_directory: str | None = None) -> dict[str, Any]:
        """Get repository info — cached, overridden, or via intelligent discovery.

        * No args + cache → return cached (source ``"cached"``)
        * No args + no cache → attempt intelligent discovery (source ``"intelligent_discovery"``)
        * Explicit *working_directory* → fresh discovery, **does not** update the primary cache
        """
        with cls._lock:
            target = working_directory or cls._working_directory

            # No context + no override → intelligent discovery
            if target is None:
                repo_info = cls._discover(None)
                if repo_info.get("success", True) and "name" in repo_info:
                    return cls._add_metadata(repo_info, "intelligent_discovery")
                underlying = repo_info.get("error", "Unknown error")
                return ActionableError.validation(
                    service=_SERVICE,
                    field_name="repository_context",
                    reason=(
                        f"No repository context set and intelligent discovery failed: {underlying}"
                    ),
                    suggestion="Call set_repository_context() first.",
                ).to_dict()

            # Cached + no override → return cached
            if working_directory is None and cls._cached_info is not None:
                return cls._add_metadata(cls._cached_info, "cached")

            # Override or cache miss → fresh discovery
            repo_info = cls._discover(target)

            # Update cache only for the primary context (not overrides)
            if (
                working_directory is None
                and repo_info.get("success", True)
                and "name" in repo_info
            ):
                cls._cached_info = repo_info
                cls._cache_timestamp = datetime.now(tz=UTC).isoformat()

            return cls._add_metadata(repo_info, "fresh_discovery")

    @classmethod
    def clear(cls) -> dict[str, Any]:
        """Clear the working directory, cached info, and timestamp."""
        with cls._lock:
            previous = cls._working_directory
            had_cache = cls._cached_info is not None

            cls._working_directory = None
            cls._cached_info = None
            cls._cache_timestamp = None

            return {
                "success": True,
                "message": "Repository context cleared",
                "previous_directory": previous,
                "previous_cache_available": had_cache,
                "cleared_at": datetime.now(tz=UTC).isoformat(),
            }

    @classmethod
    def status(cls) -> dict[str, Any]:
        """Snapshot of current context state for debugging."""
        with cls._lock:
            return {
                "context_set": cls._working_directory is not None,
                "current_working_directory": cls._working_directory,
                "cache_available": cls._cached_info is not None,
                "cache_timestamp": cls._cache_timestamp,
                "cached_repository": (cls._cached_info.get("name") if cls._cached_info else None),
                "cached_organization": (
                    cls._cached_info.get("organization") if cls._cached_info else None
                ),
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _discover(cls, working_directory: str | None) -> dict[str, Any]:
        """Run git discovery via Layer 1 primitives.

        Uses :func:`discover_repositories` + :func:`infer_target_repository`
        to find and select a repository.  If *working_directory* is ``None``,
        falls back to :data:`os.getcwd()`.
        """
        search_root = working_directory or os.getcwd()
        repos = discover_repositories(search_root)

        if not repos:
            return {
                "success": False,
                "error": f"No Azure DevOps repositories found under {search_root}",
                "error_type": "not_found",
            }

        best = infer_target_repository(repos, working_directory=working_directory)
        if best is None:
            return repos[0]

        return best

    @classmethod
    def _add_metadata(cls, info: dict[str, Any], source: str) -> dict[str, Any]:
        """Attach ``_context_source`` / ``_context_timestamp`` / ``_context_working_directory``."""
        info["_context_source"] = source
        info["_context_timestamp"] = datetime.now(tz=UTC).isoformat()
        if cls._working_directory:
            info["_context_working_directory"] = cls._working_directory
        return info


# ------------------------------------------------------------------
# Module-level convenience functions
# ------------------------------------------------------------------


def set_repository_context(working_directory: str) -> dict[str, Any]:
    """Convenience wrapper for :meth:`RepositoryContext.set`."""
    return RepositoryContext.set(working_directory)


def get_repository_context(working_directory: str | None = None) -> dict[str, Any]:
    """Convenience wrapper for :meth:`RepositoryContext.get`."""
    return RepositoryContext.get(working_directory)


def get_context_status() -> dict[str, Any]:
    """Convenience wrapper for :meth:`RepositoryContext.status`."""
    return RepositoryContext.status()


def clear_repository_context() -> dict[str, Any]:
    """Convenience wrapper for :meth:`RepositoryContext.clear`."""
    return RepositoryContext.clear()

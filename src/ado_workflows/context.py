"""
Layer 2 — Repository context management with caching and thread-safety.

Provides session-level repository context so that multiple tool calls within
a single MCP session share the same discovered repository information without
redundant ``git`` subprocess calls.

Typical usage::

    from ado_workflows.context import RepositoryContext

    result = RepositoryContext.set("/workspace/my-repo")  # raises on failure
    info   = RepositoryContext.get()                      # cached; raises on failure
    status = RepositoryContext.status()                   # debug info
    RepositoryContext.clear()                             # reset

Failure contract: :meth:`RepositoryContext.set` and
:meth:`RepositoryContext.get` raise :class:`ActionableError` on failure.
They never return error-shaped dicts. See ``ai_guidance`` for the two
agent-executable remedies (``working_directory`` parameter and
``set_repository_context`` function).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from actionable_errors import ActionableError, AIGuidance

from ado_workflows.discovery import discover_repositories, infer_target_repository

_SERVICE = "Azure DevOps"


def _no_session_context_guidance() -> AIGuidance:
    """
    AIGuidance for ``get()`` when no session context is set and intelligent discovery from cwd finds no Azure DevOps repository.

    This is the only site where both agent-executable remedies apply: the
    caller has neither a per-call override nor a session-cached context,
    so either remedy resolves the failure.
    """
    return AIGuidance(
        action_required=(
            "Pass `working_directory` (absolute path to an Azure DevOps "
            "repository) when calling this function, or call "
            "`set_repository_context(working_directory=...)` once at the "
            "start of the session so subsequent calls inherit the context."
        ),
        checks=[
            "Is the path you intend to use an absolute path containing a `.git/` folder?",
            "Does that repository's `origin` remote URL point to Azure "
            "DevOps (`dev.azure.com` or `*.visualstudio.com`)?",
            "Has `set_repository_context` been called in this session?",
        ],
        steps=[
            "Try the per-call override first: pass `working_directory` "
            "(absolute path) on the next call -- cheapest to retry.",
            "If multiple calls will use the same repository, call "
            "`set_repository_context(working_directory=...)` once so the "
            "context is cached for the rest of the session.",
        ],
    )


def _invalid_path_guidance() -> AIGuidance:
    """
    AIGuidance for path-shape failures (non-absolute path, missing directory).

    The remedy is to fix the input the caller already passed.
    """
    return AIGuidance(
        action_required=(
            "Resolve the path to an absolute path that exists on disk "
            "(e.g. `os.path.abspath(...)` after verifying the directory "
            "is present), then retry with the corrected `working_directory`."
        ),
        checks=[
            "Is the path absolute? Relative paths are rejected.",
            "Does the directory exist on disk? Check for typos and that "
            "it has not been moved or deleted.",
        ],
    )


def _no_ado_repo_guidance() -> AIGuidance:
    """
    AIGuidance for the case where the target directory exists but contains no Azure DevOps repository.

    The remedy is to point at a different directory whose `origin` remote is on Azure DevOps.
    """
    return AIGuidance(
        action_required=(
            "Retry with a `working_directory` that contains an Azure "
            "DevOps repository -- a directory with a `.git/` folder whose "
            "`origin` remote URL is on `dev.azure.com` or "
            "`*.visualstudio.com`."
        ),
        checks=[
            "Does the directory contain a `.git/` folder?",
            "Does `git -C <path> remote get-url origin` point to "
            "`dev.azure.com` or `*.visualstudio.com`? If it points to "
            "GitHub or another host, this is not the right repository.",
            "If you intended a parent workspace, point at the specific "
            "repository sub-directory rather than the workspace root.",
        ],
    )


def _discovery_internal_guidance() -> AIGuidance:
    """
    AIGuidance for unexpected GitPython failures during discovery.

    The remedy is to inspect the repository state -- the agent cannot
    auto-recover from a corrupted `.git` or invalid remote configuration.
    """
    return AIGuidance(
        action_required=(
            "GitPython failed unexpectedly while inspecting the "
            "repository. Read the wrapped exception for the cause; if "
            "the repository state is corrupted or its remote "
            "configuration is invalid, ask the user to repair it before "
            "retrying."
        ),
        checks=[
            "Does `git -C <path> status` succeed when run by the user?",
            "Does `git -C <path> remote get-url origin` return a valid URL?",
        ],
    )


class RepositoryContext:
    """
    Thread-safe, session-level repository context manager.

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
        """
        Set the active repository context for subsequent tool operations.

        Validates the path, runs discovery, and caches the result.  On
        failure the previous context is cleared so callers never operate
        against stale data, and an :class:`ActionableError` is raised.

        Raises:
            ActionableError: ``error_type='validation'`` for a non-absolute
                path; ``error_type='not_found'`` for a missing directory
                or a directory containing no Azure DevOps repository;
                ``error_type='internal'`` for unexpected GitPython errors.
                ``ai_guidance`` names both agent-executable remedies
                (``working_directory`` parameter and
                ``set_repository_context`` function).

        """
        with cls._lock:
            # Validate: must be absolute
            if not os.path.isabs(working_directory):
                raise ActionableError.validation(
                    service=_SERVICE,
                    field_name="working_directory",
                    reason=f"Must be an absolute path, got: {working_directory}",
                    suggestion="Provide the full absolute path to the repository.",
                    ai_guidance=_invalid_path_guidance(),
                )

            # Validate: must exist
            if not os.path.exists(working_directory):
                raise ActionableError.not_found(
                    service="File System",
                    resource_type="Directory",
                    resource_id=working_directory,
                    raw_error="Directory does not exist",
                    ai_guidance=_invalid_path_guidance(),
                )

            # Clear previous cache before running discovery
            cls._working_directory = working_directory
            cls._cached_info = None
            cls._cache_timestamp = None

            # Discover
            try:
                repo_info = cls._discover(working_directory)
            except Exception as exc:
                cls._working_directory = None
                raise ActionableError.internal(
                    service=_SERVICE,
                    operation="repository_discovery",
                    raw_error=str(exc),
                    suggestion="Verify git repository and remote configuration.",
                    ai_guidance=_discovery_internal_guidance(),
                ) from exc

            if repo_info is None:
                # Discovery found no Azure DevOps repository
                cls._working_directory = None
                raise ActionableError.not_found(
                    service=_SERVICE,
                    resource_type="Repository",
                    resource_id=working_directory,
                    raw_error=(f"No Azure DevOps repositories found under {working_directory}"),
                    ai_guidance=_no_ado_repo_guidance(),
                )

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
        """
        Get repository info — cached, overridden, or via intelligent discovery.

        * No args + cache → return cached (source ``"cached"``)
        * No args + no cache → attempt intelligent discovery (source ``"intelligent_discovery"``)
        * Explicit *working_directory* → fresh discovery, **does not** update the primary cache

        Raises:
            ActionableError: ``error_type='validation'`` when no context is
                set and intelligent discovery (cwd) finds no Azure DevOps
                repository; ``error_type='not_found'`` when an explicit
                ``working_directory`` is provided but no Azure DevOps
                repository is discovered there. ``ai_guidance`` names both
                agent-executable remedies.

        """
        with cls._lock:
            target = working_directory or cls._working_directory

            # No context + no override → intelligent discovery from cwd
            if target is None:
                repo_info = cls._discover(None)
                if repo_info is not None:
                    return cls._add_metadata(repo_info, "intelligent_discovery")
                raise ActionableError.validation(
                    service=_SERVICE,
                    field_name="repository_context",
                    reason=(
                        f"No Azure DevOps repositories found under {os.getcwd()} "
                        f"and no repository context has been set."
                    ),
                    suggestion=("Pass working_directory or call set_repository_context() first."),
                    ai_guidance=_no_session_context_guidance(),
                )

            # Cached + no override → return cached
            if working_directory is None and cls._cached_info is not None:
                return cls._add_metadata(cls._cached_info, "cached")

            # Override or cache miss → fresh discovery
            repo_info = cls._discover(target)
            if repo_info is None:
                raise ActionableError.not_found(
                    service=_SERVICE,
                    resource_type="Repository",
                    resource_id=target,
                    raw_error=f"No Azure DevOps repositories found under {target}",
                    ai_guidance=_no_ado_repo_guidance(),
                )

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
    def _discover(cls, working_directory: str | None) -> dict[str, Any] | None:
        """
        Run git discovery via Layer 1 primitives.

        Uses :func:`discover_repositories` + :func:`infer_target_repository`
        to find and select an Azure DevOps repository.  If
        *working_directory* is ``None``, falls back to :data:`os.getcwd()`.

        Returns:
            The selected repository info dict, or ``None`` when no Azure
            DevOps repositories are discovered. Callers translate ``None``
            into the appropriate :class:`ActionableError`.

        """
        search_root = working_directory or os.getcwd()
        repos = discover_repositories(search_root)

        if not repos:
            return None

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
    """Delegate to :meth:`RepositoryContext.set`."""
    return RepositoryContext.set(working_directory)


def get_repository_context(working_directory: str | None = None) -> dict[str, Any]:
    """Delegate to :meth:`RepositoryContext.get`."""
    return RepositoryContext.get(working_directory)


def get_context_status() -> dict[str, Any]:
    """Delegate to :meth:`RepositoryContext.status`."""
    return RepositoryContext.status()


def clear_repository_context() -> dict[str, Any]:
    """Delegate to :meth:`RepositoryContext.clear`."""
    return RepositoryContext.clear()

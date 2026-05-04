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
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from actionable_errors import ActionableError, AIGuidance

from ado_workflows.discovery import discover_repositories, infer_target_repository

_SERVICE = "Azure DevOps"


@dataclass(frozen=True)
class _NoMatch:
    """Discovery found zero Azure DevOps repositories."""


@dataclass(frozen=True)
class _SingleMatch:
    """Discovery resolved to exactly one Azure DevOps repository."""

    repo: dict[str, Any]


@dataclass(frozen=True)
class _AmbiguousMatch:
    """
    Discovery found more than one Azure DevOps repository.

    Returned when no ``working_directory`` hint was sufficient to pick
    one of the discovered candidates.
    """

    candidates: list[dict[str, Any]]


_Resolution = _NoMatch | _SingleMatch | _AmbiguousMatch


def _candidate_summary(repo: dict[str, Any]) -> dict[str, Any]:
    """Extract the user-facing identification fields from a discovered repo dict."""
    return {
        "name": repo["name"],
        "organization": repo["organization"],
        "project": repo["project"],
        "path": repo["path"],
    }


def _ambiguous_repos_guidance() -> AIGuidance:
    """
    AIGuidance for the multi-repo ambiguity branch.

    Direct the agent to surface the ``candidate_repositories`` list in
    ``error.context`` as a sub-prompt and ask the user which repository
    to target. Explicitly call out that the work board may live in a
    different organization from any of the code repos.
    """
    return AIGuidance(
        action_required=(
            "Multiple Azure DevOps repositories were discovered. Surface "
            "the `candidate_repositories` list in `error.context` to the "
            "user as a sub-prompt and ask them to pick one, then retry "
            "with `working_directory` set to the chosen repo's path. If "
            "the operation targets a work item, prefer asking the user "
            "for the full work item URL — work boards may live in a "
            "different organization from any of the code repos."
        ),
        checks=[
            "Has the user already indicated which repository they want?",
            "Is the operation a work item lookup? If so, the work board "
            "may live in a different org — a URL is more reliable than "
            "any code repo's `working_directory`.",
            "Are all candidates Azure DevOps repos, or does one belong "
            "to a different VCS host (GitHub, GitLab, etc.)?",
        ],
        steps=[
            "Render the candidate list (`error.context['candidate_repositories']`) "
            "as a numbered choice prompt for the user.",
            "Wait for the user's selection — do not pick one programmatically.",
            "Retry the call with `working_directory` set to the selected "
            "repo's `path`, or with the work item URL the user supplies.",
        ],
    )


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
                resolution = cls._discover(working_directory)
            except Exception as exc:
                cls._working_directory = None
                raise ActionableError.internal(
                    service=_SERVICE,
                    operation="repository_discovery",
                    raw_error=str(exc),
                    suggestion="Verify git repository and remote configuration.",
                    ai_guidance=_discovery_internal_guidance(),
                ) from exc

            if isinstance(resolution, _NoMatch):
                cls._working_directory = None
                raise ActionableError.not_found(
                    service=_SERVICE,
                    resource_type="Repository",
                    resource_id=working_directory,
                    raw_error=(f"No Azure DevOps repositories found under {working_directory}"),
                    ai_guidance=_no_ado_repo_guidance(),
                )

            if isinstance(resolution, _AmbiguousMatch):
                cls._working_directory = None
                err = ActionableError.validation(
                    service=_SERVICE,
                    field_name="working_directory",
                    reason=(
                        f"Multiple Azure DevOps repositories found under "
                        f"{working_directory}. Refusing to pick one silently."
                    ),
                    suggestion=(
                        "Pass working_directory pointing at the specific "
                        "repository sub-directory, or a full URL of the "
                        "target resource."
                    ),
                    ai_guidance=_ambiguous_repos_guidance(),
                )
                err.context = {
                    "candidate_repositories": [
                        _candidate_summary(c) for c in resolution.candidates
                    ]
                }
                raise err

            # _SingleMatch — narrowed by elimination
            repo_info = resolution.repo
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
                resolution = cls._discover(None)
                if isinstance(resolution, _SingleMatch):
                    return cls._add_metadata(resolution.repo, "intelligent_discovery")
                if isinstance(resolution, _AmbiguousMatch):
                    err = ActionableError.validation(
                        service=_SERVICE,
                        field_name="repository_context",
                        reason=(
                            f"Multiple Azure DevOps repositories found under "
                            f"{os.getcwd()}. Refusing to pick one silently."
                        ),
                        suggestion=(
                            "Pass working_directory pointing at the specific "
                            "repository sub-directory, or a full URL of the "
                            "target resource."
                        ),
                        ai_guidance=_ambiguous_repos_guidance(),
                    )
                    err.context = {
                        "candidate_repositories": [
                            _candidate_summary(c) for c in resolution.candidates
                        ]
                    }
                    raise err
                # _NoMatch
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
            resolution = cls._discover(target)
            if isinstance(resolution, _SingleMatch):
                return cls._add_metadata(resolution.repo, "fresh_discovery")
            if isinstance(resolution, _AmbiguousMatch):
                err = ActionableError.validation(
                    service=_SERVICE,
                    field_name="working_directory",
                    reason=(
                        f"Multiple Azure DevOps repositories found under "
                        f"{target}. Refusing to pick one silently."
                    ),
                    suggestion=(
                        "Pass working_directory pointing at the specific "
                        "repository sub-directory, or a full URL of the "
                        "target resource."
                    ),
                    ai_guidance=_ambiguous_repos_guidance(),
                )
                err.context = {
                    "candidate_repositories": [
                        _candidate_summary(c) for c in resolution.candidates
                    ]
                }
                raise err
            # _NoMatch
            raise ActionableError.not_found(
                service=_SERVICE,
                resource_type="Repository",
                resource_id=target,
                raw_error=f"No Azure DevOps repositories found under {target}",
                ai_guidance=_no_ado_repo_guidance(),
            )

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
            cached_info = cls._cached_info
            payload = {
                "context_set": cls._working_directory is not None,
                "current_working_directory": cls._working_directory,
                "cache_available": cached_info is not None,
                "cache_timestamp": cls._cache_timestamp,
                "cached_repository": (cached_info.get("name") if cached_info else None),
                "cached_organization": (cached_info.get("organization") if cached_info else None),
            }
        # discover_all does not acquire ``_lock`` (no class-state access),
        # so call it after releasing the lock to keep status() ordering
        # simple.
        payload["discovered_repositories"] = cls.discover_all()
        return payload

    @classmethod
    def discover_all(cls, working_directory: str | None = None) -> list[dict[str, Any]]:
        """
        Return every Azure DevOps repository discovered under *working_directory* (or cwd).

        Always re-walks the filesystem — no caching. Non-Azure-DevOps
        remotes (GitHub, etc.) are silently excluded by the underlying
        :func:`inspect_git_repository`.

        Args:
            working_directory: Directory to walk. Defaults to
                :data:`os.getcwd()` when ``None``.

        Returns:
            A (possibly empty) list of repository info dicts in the same
            shape as :func:`discover_repositories`.

        """
        search_root = working_directory or os.getcwd()
        return discover_repositories(search_root)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _discover(cls, working_directory: str | None) -> _Resolution:
        """
        Resolve the target Azure DevOps repository to a :class:`_Resolution`.

        Walks the filesystem via :func:`discover_repositories`, then
        applies :func:`infer_target_repository` to disambiguate. Never
        falls back to ``repos[0]`` — multi-repo workspaces with no
        disambiguating hint return a :class:`_AmbiguousMatch`.

        Returns:
            * :class:`_NoMatch` — no Azure DevOps repos found.
            * :class:`_SingleMatch` — exactly one candidate, or one
              selected via cwd / working_directory hint.
            * :class:`_AmbiguousMatch` — multiple repos found and the
              hint did not pick one.

        """
        search_root = working_directory or os.getcwd()
        repos = discover_repositories(search_root)

        if not repos:
            return _NoMatch()

        if len(repos) == 1:
            return _SingleMatch(repo=repos[0])

        best = infer_target_repository(repos, working_directory=working_directory)
        if best is not None:
            return _SingleMatch(repo=best)

        return _AmbiguousMatch(candidates=repos)

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


def discover_all_repositories(
    working_directory: str | None = None,
) -> list[dict[str, Any]]:
    """Delegate to :meth:`RepositoryContext.discover_all`."""
    return RepositoryContext.discover_all(working_directory)


def get_context_status() -> dict[str, Any]:
    """Delegate to :meth:`RepositoryContext.status`."""
    return RepositoryContext.status()


def clear_repository_context() -> dict[str, Any]:
    """Delegate to :meth:`RepositoryContext.clear`."""
    return RepositoryContext.clear()

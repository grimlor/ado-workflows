"""
BDD tests for ado_workflows.context — ambiguity contract on get/status.

Covers:
    TestRepositoryContextGetAmbiguity        — multi-repo workspace raises
    TestRepositoryContextStatusIncludesDiscovered — status payload extension

Public API surface affected:
    RepositoryContext.get(working_directory=None)  — ambiguity behavior
    RepositoryContext.status()                     — gains
        "discovered_repositories"
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext

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


def _two_repo_workspace(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str, str]:
    """
    Build a workspace with two ADO repos in different orgs.

    Returns ``(workspace, repo_a, repo_b, url_a, url_b)``.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_a = _make_repo_dir(workspace, "RepoA")
    repo_b = _make_repo_dir(workspace, "RepoB")
    url_a = "https://dev.azure.com/OrgA/Proj/_git/RepoA"
    url_b = "https://dev.azure.com/OrgB/Proj/_git/RepoB"
    return workspace, repo_a, repo_b, url_a, url_b


def _repo_dispatcher(repo_a: Path, url_a: str, repo_b: Path, url_b: str) -> object:
    """Return a side_effect that maps Repo(path) to the matching mock."""

    def _dispatch(path: str, *_args: object, **_kwargs: object) -> MagicMock:
        if path == str(repo_a):
            return _mock_repo(url_a)
        if path == str(repo_b):
            return _mock_repo(url_b)
        raise AssertionError(f"Unexpected Repo path: {path}")

    return _dispatch


# ---------------------------------------------------------------------------
# TestRepositoryContextGetAmbiguity
# ---------------------------------------------------------------------------


class TestRepositoryContextGetAmbiguity:
    """
    REQUIREMENT: RepositoryContext.get raises ActionableError.validation
    with sub-prompt-driving ai_guidance when the workspace contains more
    than one Azure DevOps repository and the caller has not disambiguated
    via working_directory or a previously cached context.

    WHO: The dispatch boundary for every Layer-3 caller that uses bare
        IDs — establish_pr_context.from_pr_id, from_work_item_id (work
        items), any consumer that relies on cached context.
    WHAT: (1) Multi-repo workspace + no working_directory + no cache →
              ActionableError.validation is raised; no repo dict is
              returned.
          (2) The raised error's ai_guidance.action_required tells the
              agent to surface the candidates as a sub-prompt and ask
              the user to pick one.
          (3) The raised error's ``context`` dict includes a
              ``"candidate_repositories"`` key whose value is a list
              of dicts with at least name, organization, project,
              and path for each ADO repo discovered.
          (4) Multi-repo workspace + working_directory pointing inside
              one of the repos → that repo is returned (no error).
          (5) Multi-repo workspace + a cached context from a previous
              set() → the cached repo is returned (no error).
          (6) Multi-repo workspace + working_directory pointing at the
              workspace root + cwd inside one of the repos → the
              cwd-hint disambiguates and that repo is returned (no
              error). The override branch must consult cwd before
              declaring ambiguity.
          (7) Multi-repo workspace + working_directory pointing at the
              workspace root + cwd outside both repos →
              ActionableError.validation is raised with
              candidate_repositories metadata. The override branch
              must refuse to silently pick repos[0] just like the
              no-args branch.
    WHY: Eliminates the silent first-match misroute that lets work-board
        and code-repo orgs be confused. Ambiguity being raised
        explicitly forces a human-in-the-loop decision instead of a
        silent guess.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo (the GitPython I/O edge).
        Real:  RepositoryContext (state and cache),
              discover_repositories, infer_target_repository.
        Never: discover_repositories, infer_target_repository,
              _discover.
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_ambiguous_workspace_raises_actionable_error(self, tmp_path: Path) -> None:
        """
        Given a workspace with two ADO repos, no working_directory, and
            no cached context
        When RepositoryContext.get() is called with no arguments
        Then ActionableError.validation is raised
        """
        # Given: an ambiguous two-repo workspace
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When/Then: get() with no working_directory raises
        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(workspace)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        assert exc_info.value.error_type == "validation", (
            f"Expected error_type='validation' on ambiguity, got {exc_info.value.error_type!r}"
        )

    def test_ai_guidance_drives_sub_prompt_for_candidate_selection(self, tmp_path: Path) -> None:
        """
        Given the ambiguity error
        When ai_guidance.action_required is read
        Then it instructs the agent to surface the candidates as a
            sub-prompt and ask the user to pick one
        """
        # Given: an ambiguous workspace
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When: get() raises
        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(workspace)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        # Then: ai_guidance is set and instructs the agent to ask the user
        guidance = exc_info.value.ai_guidance
        assert guidance is not None, "Expected ai_guidance on ambiguity error, got None"
        action = guidance.action_required.lower()
        # The instruction must direct the agent to involve the user, not
        # silently pick one
        assert "ask" in action or "user" in action or "prompt" in action, (
            f"Expected ai_guidance to direct the agent to ask the user, "
            f"got: {guidance.action_required!r}"
        )

    def test_context_dict_includes_candidate_repo_metadata(self, tmp_path: Path) -> None:
        """
        Given a multi-repo ambiguity error
        When err.context is inspected
        Then it contains a "candidate_repositories" list whose elements
            each include name, organization, project, and path
        """
        # Given: an ambiguous workspace
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When: get() raises
        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(workspace)),
            pytest.raises(ActionableError) as exc_info,
        ):
            RepositoryContext.get()

        err = exc_info.value
        assert err.context is not None, (
            f"Expected err.context dict on ambiguity, got None. Error: {err.error!r}"
        )
        candidates = err.context.get("candidate_repositories")
        assert candidates is not None, (
            f"Expected 'candidate_repositories' in err.context, "
            f"got keys: {list(err.context.keys())!r}"
        )
        assert len(candidates) == 2, (
            f"Expected 2 candidates, got {len(candidates)}: {candidates!r}"
        )
        for c in candidates:
            for key in ("name", "organization", "project", "path"):
                assert key in c, f"Expected '{key}' in candidate, got keys: {list(c.keys())!r}"

    def test_working_directory_inside_one_repo_disambiguates(self, tmp_path: Path) -> None:
        """
        Given a multi-repo workspace and working_directory pointing
            inside repo A
        When RepositoryContext.get(working_directory=...) is called
        Then repo A is returned, no error is raised
        """
        # Given: an ambiguous workspace
        _workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When: get() is called with working_directory inside RepoA
        with patch(_REPO_PATCH, side_effect=dispatcher):
            info = RepositoryContext.get(working_directory=str(repo_a))

        # Then: RepoA is returned
        assert info["name"] == "RepoA", f"Expected 'RepoA', got '{info.get('name')}'"
        assert info["organization"] == "OrgA", f"Expected 'OrgA', got '{info.get('organization')}'"

    def test_cached_context_disambiguates_subsequent_get(self, tmp_path: Path) -> None:
        """
        Given a multi-repo workspace where set() has been called once
            with repo A
        When RepositoryContext.get() is called with no arguments
        Then repo A's cached info is returned, no error is raised
        """
        # Given: cached context for RepoA
        _workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)
        with patch(_REPO_PATCH, side_effect=dispatcher):
            RepositoryContext.set(str(repo_a))

        # When: get() with no arguments uses the cache
        info = RepositoryContext.get()

        # Then: cached RepoA info is returned, no ambiguity raised
        assert info["name"] == "RepoA", f"Expected cached 'RepoA', got '{info.get('name')}'"
        assert info["_context_source"] == "cached", (
            f"Expected source='cached', got '{info.get('_context_source')}'"
        )

    def test_get_with_workspace_root_override_and_cwd_in_one_repo_disambiguates(
        self, tmp_path: Path
    ) -> None:
        """
        Given a multi-repo workspace, working_directory pointing at the
            workspace root (which contains repo A and repo B), and a cwd
            inside repo A
        When RepositoryContext.get(working_directory=workspace_root) is
            called
        Then repo A is returned via cwd-hint disambiguation, no error
            is raised — the override branch must consult cwd before
            declaring ambiguity
        """
        # Given: an ambiguous workspace with cwd inside RepoA
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(repo_a)),
        ):
            # When: explicit workspace-root override
            info = RepositoryContext.get(working_directory=str(workspace))

        # Then: cwd-hint picks RepoA
        assert info["name"] == "RepoA", (
            f"Expected cwd-hint to pick 'RepoA', got '{info.get('name')}'"
        )
        assert info["organization"] == "OrgA", f"Expected 'OrgA', got '{info.get('organization')}'"

    def test_get_with_workspace_root_override_and_cwd_outside_repos_raises(
        self, tmp_path: Path
    ) -> None:
        """
        Given a multi-repo workspace, working_directory pointing at the
            workspace root, and a cwd outside both repos (no
            disambiguating signal)
        When RepositoryContext.get(working_directory=workspace_root) is
            called
        Then ActionableError.validation is raised with
            candidate_repositories metadata — the override branch must
            refuse to silently pick repos[0] just like the no-args branch
        """
        # Given: an ambiguous workspace with cwd outside both repos
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(tmp_path)),
            pytest.raises(ActionableError) as exc_info,
        ):
            # When: explicit workspace-root override with no disambiguator
            RepositoryContext.get(working_directory=str(workspace))

        # Then: validation error with candidate metadata
        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation', got {err.error_type!r}"
        )
        assert err.context is not None, (
            f"Expected err.context dict, got None. Error: {err.error!r}"
        )
        candidates = err.context.get("candidate_repositories")
        assert candidates is not None and len(candidates) == 2, (
            f"Expected 2 candidate_repositories, got {candidates!r}"
        )


# ---------------------------------------------------------------------------
# TestRepositoryContextStatusIncludesDiscovered
# ---------------------------------------------------------------------------


class TestRepositoryContextStatusIncludesDiscovered:
    """
    REQUIREMENT: RepositoryContext.status returns a payload that
    includes the list of discovered Azure DevOps repositories under the
    current working directory or cwd, in addition to the existing
    cache-status keys.

    WHO: Status/debug consumers — MCP `get_repository_context_status`
        tool, troubleshooting docs, agent introspection.
    WHAT: (1) The returned dict contains a "discovered_repositories" key.
          (2) "discovered_repositories" is a list whose elements match
              what RepositoryContext.discover_all returns for the same
              cwd.
          (3) The previously-existing keys (context_set,
              current_working_directory, cache_available,
              cache_timestamp, cached_repository, cached_organization)
              are unchanged in both presence and value.
    WHY: Lets the agent render every candidate in a status report
        without a second discovery call, and lets debug tools answer
        "what is the library actually seeing right now?" definitively.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo.
        Real:  RepositoryContext.status, RepositoryContext.discover_all.
        Never: discover_all internals.
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_status_payload_contains_discovered_repositories_key(self, tmp_path: Path) -> None:
        """
        Given a workspace with at least one ADO repo
        When RepositoryContext.status() is called
        Then the returned dict contains a "discovered_repositories" key
        """
        # Given: a workspace with one ADO repo and cwd pointing at it
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = _make_repo_dir(workspace, "OnlyRepo")
        url = "https://dev.azure.com/Org/Proj/_git/OnlyRepo"
        del repo

        # When: status() is called with cwd inside the workspace
        with (
            patch(_REPO_PATCH, return_value=_mock_repo(url)),
            patch("os.getcwd", return_value=str(workspace)),
        ):
            payload = RepositoryContext.status()

        # Then: the new key is present
        assert "discovered_repositories" in payload, (
            f"Expected 'discovered_repositories' in status payload, "
            f"got keys: {list(payload.keys())!r}"
        )

    def test_discovered_repositories_matches_discover_all_output(self, tmp_path: Path) -> None:
        """
        Given a multi-repo workspace
        When status() and discover_all() are called against the same cwd
        Then status()["discovered_repositories"] equals discover_all()
        """
        # Given: a multi-repo workspace
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When: both methods are called with the same cwd
        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(workspace)),
        ):
            payload = RepositoryContext.status()
            discovered = RepositoryContext.discover_all()

        # Then: they agree
        assert payload["discovered_repositories"] == discovered, (
            f"Expected status['discovered_repositories'] to equal discover_all(), "
            f"got status={payload['discovered_repositories']!r} "
            f"discover_all={discovered!r}"
        )

    def test_existing_status_keys_unchanged(self, tmp_path: Path) -> None:
        """
        Given a freshly-cleared RepositoryContext
        When status() is called
        Then context_set, current_working_directory, cache_available,
            cache_timestamp, cached_repository, cached_organization
            are present with their pre-existing semantics
        """
        # Given: cleared state, an empty cwd so discover_all returns []
        empty = tmp_path / "empty"
        empty.mkdir()

        # When: status() is called
        with patch("os.getcwd", return_value=str(empty)):
            payload = RepositoryContext.status()

        # Then: every previously-existing key is present
        for key in (
            "context_set",
            "current_working_directory",
            "cache_available",
            "cache_timestamp",
            "cached_repository",
            "cached_organization",
        ):
            assert key in payload, (
                f"Expected pre-existing key {key!r} in status payload, "
                f"got keys: {list(payload.keys())!r}"
            )
        # And their cleared-state values are unchanged
        assert payload["context_set"] is False, (
            f"Expected context_set=False on cleared state, got {payload['context_set']!r}"
        )
        assert payload["cache_available"] is False, (
            f"Expected cache_available=False on cleared state, got {payload['cache_available']!r}"
        )

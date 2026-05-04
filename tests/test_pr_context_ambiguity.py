"""
BDD tests for ado_workflows.pr — PR context inherits ambiguity contract.

This file isolates one new behavior class from existing test_pr.py:
multi-repo workspace + bare PR ID must surface the same
ActionableError.validation that RepositoryContext.get raises.

Public API surface affected:
    AzureDevOpsPRContext.from_pr_id (no signature change; behavior changes
    transitively through RepositoryContext.get)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext
from ado_workflows.pr import AzureDevOpsPRContext

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


def _two_repo_workspace(tmp_path: Path) -> tuple[Path, Path, Path, str, str]:
    """Build a workspace with two ADO repos in different orgs."""
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


class TestPRContextFromPRIdInheritsAmbiguity:
    """
    REQUIREMENT: AzureDevOpsPRContext.from_pr_id inherits the new
    ambiguity contract from RepositoryContext.get without modification
    to its own signature or wrapping of the raised error.

    WHO: Bare-PR-ID callers — `establish_pr_context("42")` — which
        previously got a silent first-match dispatch.
    WHAT: (1) Multi-repo workspace + no working_directory →
              ActionableError.validation propagates from from_pr_id
              unchanged (same error class, same ai_guidance).
          (2) Multi-repo workspace + working_directory inside one repo
              → that repo's org/project are used to construct the PR
              URL; no error.
    WHY: Closing this hole removes the only remaining path through
        which a bare numeric ID could silently target the wrong org.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo.
        Real:  AzureDevOpsPRContext.from_pr_id, RepositoryContext.get.
        Never: RepositoryContext.get.
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_ambiguity_propagates_from_repository_context_get(self, tmp_path: Path) -> None:
        """
        Given a multi-repo workspace and no working_directory
        When AzureDevOpsPRContext.from_pr_id(42) is called
        Then the ActionableError.validation raised by
            RepositoryContext.get propagates unchanged
        """
        # Given: an ambiguous workspace
        workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When/Then: from_pr_id raises with the same error type as
        # RepositoryContext.get (validation, not not_found or internal)
        with (
            patch(_REPO_PATCH, side_effect=dispatcher),
            patch("os.getcwd", return_value=str(workspace)),
            pytest.raises(ActionableError) as exc_info,
        ):
            AzureDevOpsPRContext.from_pr_id(42)

        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation' (ambiguity), got {err.error_type!r}"
        )
        # ai_guidance must survive propagation
        assert err.ai_guidance is not None, "Expected ai_guidance to propagate intact, got None"

    def test_working_directory_disambiguates_pr_id_lookup(self, tmp_path: Path) -> None:
        """
        Given a multi-repo workspace and working_directory inside repo A
        When AzureDevOpsPRContext.from_pr_id(42, working_directory=...)
            is called
        Then the returned context's organization/project/repository
            match repo A and pr_id=42
        """
        # Given: an ambiguous workspace
        _workspace, repo_a, repo_b, url_a, url_b = _two_repo_workspace(tmp_path)
        dispatcher = _repo_dispatcher(repo_a, url_a, repo_b, url_b)

        # When: from_pr_id is called with working_directory inside RepoA
        with patch(_REPO_PATCH, side_effect=dispatcher):
            ctx = AzureDevOpsPRContext.from_pr_id(42, working_directory=str(repo_a))

        # Then: context resolved from RepoA, not RepoB
        assert ctx.organization == "OrgA", (
            f"Expected 'OrgA' (working_directory-disambiguated), got '{ctx.organization}'"
        )
        assert ctx.repository == "RepoA", f"Expected 'RepoA', got '{ctx.repository}'"
        assert ctx.pr_id == 42, f"Expected pr_id=42, got {ctx.pr_id}"

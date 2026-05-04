"""
BDD tests for ado_workflows.work_items — work item context resolution.

Covers:
    TestWorkItemContextFromUrl       — URL → AzureDevOpsWorkItemContext
    TestWorkItemContextFromWorkItemId — bare ID + RepositoryContext
    TestEstablishWorkItemContext     — URL-vs-ID router

Public API surface (from src/ado_workflows/work_items.py):
    AzureDevOpsWorkItemContext (dataclass)
        from_url(work_item_url) -> AzureDevOpsWorkItemContext
        from_work_item_id(work_item_id, working_directory=None)
            -> AzureDevOpsWorkItemContext
        org_url (property) -> str
        to_dict() -> dict[str, Any]
    establish_work_item_context(url_or_id, working_directory=None)
        -> AzureDevOpsWorkItemContext
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext
from ado_workflows.work_items import (
    AzureDevOpsWorkItemContext,
    establish_work_item_context,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

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


def _make_repo_dir(
    workspace: Path,
    name: str,
    *,
    remote_url: str | None = None,
) -> tuple[Path, str]:
    """Create a directory containing ``.git`` under *workspace*."""
    repo = workspace / name
    (repo / ".git").mkdir(parents=True)
    url = remote_url or f"https://dev.azure.com/ContosoOrg/Payments/_git/{name}"
    return repo, url


# ---------------------------------------------------------------------------
# TestWorkItemContextFromUrl
# ---------------------------------------------------------------------------


class TestWorkItemContextFromUrl:
    """
    REQUIREMENT: AzureDevOpsWorkItemContext.from_url constructs a fully
    resolved context from a work item URL, raising ActionableError when
    required fields cannot be extracted.

    WHO: Callers who already have a URL — typical for agents responding
        to user-pasted URLs.
    WHAT: (1) On a valid URL, returns a context with organization,
              project, and work_item_id populated and source="url".
          (2) On a URL that parse_ado_work_item_url cannot extract all
              fields from, raises ActionableError.validation naming the
              missing fields, with a suggestion containing the canonical
              URL shape.
          (3) The org_url property returns
              "https://dev.azure.com/{organization}".
          (4) to_dict returns every dataclass field plus org_url.
    WHY: Mirrors AzureDevOpsPRContext.from_url so consumers learn one
        pattern. Validation catches malformed URLs at the call boundary
        before any SDK request is constructed.

    MOCK BOUNDARY:
        Mock:  Nothing.
        Real:  parse_ado_work_item_url, AzureDevOpsWorkItemContext.
        Never: Nothing.
    """

    def test_valid_url_constructs_context_with_source_url(self) -> None:
        """
        Given a well-formed work item URL
        When from_url is called
        Then a context is returned with org/project/id populated and
            source="url"
        """
        # Given: a well-formed work item URL
        url = "https://dev.azure.com/Foo/Bar/_workitems/edit/42"

        # When: the context is constructed via the URL factory
        ctx = AzureDevOpsWorkItemContext.from_url(url)

        # Then: every field is populated from the URL
        assert ctx.organization == "Foo", f"Expected organization 'Foo', got '{ctx.organization}'"
        assert ctx.project == "Bar", f"Expected project 'Bar', got '{ctx.project}'"
        assert ctx.work_item_id == 42, f"Expected work_item_id 42, got {ctx.work_item_id}"
        assert ctx.source == "url", f"Expected source 'url', got '{ctx.source}'"
        assert ctx.work_item_url == url, (
            f"Expected work_item_url to round-trip the input, got '{ctx.work_item_url}'"
        )

    def test_unparseable_url_raises_actionable_error_naming_missing_fields(self) -> None:
        """
        Given a URL parse_ado_work_item_url cannot extract every field from
        When from_url is called
        Then ActionableError.validation is raised, listing the missing
            fields in the reason and showing the canonical URL shape in
            the suggestion
        """
        # Given: a URL that is not a work item URL at all (a PR URL)
        url = "https://dev.azure.com/Foo/Bar/_git/Baz/pullrequest/42"

        # When/Then: from_url raises a validation error
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsWorkItemContext.from_url(url)

        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected error_type='validation', got: {err.error_type!r}"
        )
        # Missing fields are mentioned somewhere in the error message
        assert "organization" in err.error or "work_item_id" in err.error, (
            f"Expected error to name a missing field, got: {err.error!r}"
        )
        # Suggestion shows the canonical URL shape
        assert err.suggestion is not None, "Expected suggestion, got None"
        assert "_workitems/edit" in err.suggestion, (
            f"Expected canonical URL shape in suggestion, got: {err.suggestion!r}"
        )

    def test_org_url_property_returns_dev_azure_com_url(self) -> None:
        """
        Given a context constructed from a URL with organization "Foo"
        When .org_url is accessed
        Then it returns "https://dev.azure.com/Foo"
        """
        # Given: a context with organization "Foo"
        ctx = AzureDevOpsWorkItemContext.from_url(
            "https://dev.azure.com/Foo/Bar/_workitems/edit/1"
        )

        # When/Then: org_url returns the canonical org base
        assert ctx.org_url == "https://dev.azure.com/Foo", (
            f"Expected 'https://dev.azure.com/Foo', got '{ctx.org_url}'"
        )

    def test_to_dict_includes_all_fields_plus_org_url(self) -> None:
        """
        Given a constructed context
        When to_dict is called
        Then the result contains every dataclass field plus org_url
        """
        # Given: a constructed context
        ctx = AzureDevOpsWorkItemContext.from_url(
            "https://dev.azure.com/Foo/Bar/_workitems/edit/7"
        )

        # When: serialized to dict
        d = ctx.to_dict()

        # Then: every dataclass field is present, plus org_url
        for key in (
            "work_item_url",
            "organization",
            "project",
            "work_item_id",
            "source",
            "org_url",
        ):
            assert key in d, f"Expected key {key!r} in to_dict result, got {d!r}"


# ---------------------------------------------------------------------------
# TestWorkItemContextFromWorkItemId
# ---------------------------------------------------------------------------


class TestWorkItemContextFromWorkItemId:
    """
    REQUIREMENT: AzureDevOpsWorkItemContext.from_work_item_id resolves
    the org/project from RepositoryContext when the caller has only a
    numeric work item ID, propagating any ActionableError raised by
    discovery unchanged.

    WHO: Callers with a bare numeric ID — typical for CLI shorthand
        usage and for agents that already have session context set.
    WHAT: (1) Returns a context with org/project from RepositoryContext
              and work_item_id from the input, with source=
              "repository_context".
          (2) The constructed work_item_url uses the canonical
              dev.azure.com path: /{org}/{project}/_workitems/edit/{id}.
          (3) When RepositoryContext.get raises (no context, ambiguous,
              or otherwise), the same ActionableError propagates from
              from_work_item_id with no wrapping or message rewriting.
    WHY: Lets bare-ID callers benefit from the same plural-aware
        context resolution as RepositoryContext.get without duplicating
        guidance text. Propagating raw means the agent sees the
        sub-prompt-driving ai_guidance verbatim.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo (the GitPython I/O edge).
        Real:  RepositoryContext (and its cache class variables),
              AzureDevOpsWorkItemContext.from_work_item_id.
        Never: RepositoryContext.get itself (must run end-to-end).
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_resolved_context_uses_repository_org_project(self, tmp_path: Path) -> None:
        """
        Given RepositoryContext can resolve a single ADO repo
        When from_work_item_id is called with id=99
        Then the returned context has organization, project from the
            discovered repo, work_item_id=99, source="repository_context"
        """
        # Given: a single ADO repo set as the cached context
        repo_dir, url = _make_repo_dir(tmp_path, "PaymentsRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            RepositoryContext.set(str(repo_dir))

        # When: from_work_item_id resolves from the cached context
        ctx = AzureDevOpsWorkItemContext.from_work_item_id(99)

        # Then: org/project come from the repo, id from the input
        assert ctx.organization == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{ctx.organization}'"
        )
        assert ctx.project == "Payments", f"Expected project 'Payments', got '{ctx.project}'"
        assert ctx.work_item_id == 99, f"Expected work_item_id=99, got {ctx.work_item_id}"
        assert ctx.source == "repository_context", (
            f"Expected source 'repository_context', got '{ctx.source}'"
        )

    def test_constructed_url_uses_canonical_dev_azure_com_path(self, tmp_path: Path) -> None:
        """
        Given a resolvable RepositoryContext
        When from_work_item_id is called with id=99
        Then context.work_item_url is
            "https://dev.azure.com/{org}/{project}/_workitems/edit/99"
        """
        # Given: a resolvable cached context
        repo_dir, url = _make_repo_dir(tmp_path, "PaymentsRepo")
        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            RepositoryContext.set(str(repo_dir))

        # When: from_work_item_id constructs a context
        ctx = AzureDevOpsWorkItemContext.from_work_item_id(99)

        # Then: the URL uses the canonical dev.azure.com work item path
        expected = "https://dev.azure.com/ContosoOrg/Payments/_workitems/edit/99"
        assert ctx.work_item_url == expected, f"Expected '{expected}', got '{ctx.work_item_url}'"

    def test_ambiguity_error_propagates_unchanged(self, tmp_path: Path) -> None:
        """
        Given a workspace with multiple ADO repos and no working_directory
            override
        When from_work_item_id is called
        Then the ActionableError.validation raised by RepositoryContext.get
            propagates unchanged, with its ai_guidance intact
        """
        # Given: a workspace with two ADO repos in different orgs
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo_a, url_a = _make_repo_dir(
            workspace,
            "RepoA",
            remote_url="https://dev.azure.com/OrgA/Proj/_git/RepoA",
        )
        repo_b, url_b = _make_repo_dir(
            workspace,
            "RepoB",
            remote_url="https://dev.azure.com/OrgB/Proj/_git/RepoB",
        )
        del repo_a, repo_b  # marker dirs only — discovery walks the workspace

        # Mock Repo to return whichever URL matches the path being inspected
        def _repo_for(path: str, *_args: object, **_kwargs: object) -> MagicMock:
            return _mock_repo(url_a if "RepoA" in path else url_b)

        # When/Then: from_work_item_id propagates the ambiguity error
        with (
            patch(_REPO_PATCH, side_effect=_repo_for),
            patch("os.getcwd", return_value=str(workspace)),
            pytest.raises(ActionableError) as exc_info,
        ):
            # No working_directory; RepositoryContext.get walks the
            # workspace via os.getcwd() — drive that with monkeypatch
            AzureDevOpsWorkItemContext.from_work_item_id(42)

        err = exc_info.value
        assert err.error_type == "validation", (
            f"Expected validation error from ambiguity, got: {err.error_type!r}"
        )
        assert err.ai_guidance is not None, (
            f"Expected ai_guidance to be set on ambiguity error, got None. Error: {err.error!r}"
        )


# ---------------------------------------------------------------------------
# TestEstablishWorkItemContext
# ---------------------------------------------------------------------------


class TestEstablishWorkItemContext:
    """
    REQUIREMENT: establish_work_item_context routes URL-shaped input to
    from_url and numeric input to from_work_item_id, rejecting empty or
    malformed input with ActionableError.

    WHO: Single entry-point for callers who don't know in advance
        whether they have a URL or an ID.
    WHAT: (1) URL-shaped input (containing "://", "dev.azure.com", or
              "visualstudio.com") routes to from_url.
          (2) Numeric input (after .strip()) routes to from_work_item_id
              with the caller's working_directory.
          (3) Empty or whitespace-only input raises ActionableError
              with a suggestion containing both URL and ID examples.
          (4) Input that is neither URL-shaped nor numeric raises
              ActionableError naming the input.
    WHY: Mirror of establish_pr_context so a single mental model
        applies to both PRs and work items.

    MOCK BOUNDARY:
        Mock:  ado_workflows.discovery.Repo (for the from_work_item_id
              branch).
        Real:  establish_work_item_context, both factory classmethods.
        Never: parse_ado_work_item_url, parse_ado_url.
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_url_shaped_input_routes_to_from_url(self) -> None:
        """
        Given a string containing "dev.azure.com" and the work item path
        When establish_work_item_context is called
        Then a context with source="url" is returned
        """
        # Given: a URL-shaped input
        url = "https://dev.azure.com/Foo/Bar/_workitems/edit/42"

        # When: the factory routes the input
        ctx = establish_work_item_context(url)

        # Then: the URL branch was taken
        assert ctx.source == "url", f"Expected source 'url', got '{ctx.source}'"
        assert ctx.work_item_id == 42, f"Expected id=42, got {ctx.work_item_id}"

    def test_numeric_input_routes_to_from_work_item_id_with_working_directory(
        self, tmp_path: Path
    ) -> None:
        """
        Given a numeric string "42" and a working_directory pointing at
            an ADO repo
        When establish_work_item_context is called
        Then a context with work_item_id=42 and source=
            "repository_context" is returned
        """
        # Given: a single ADO repo at a specific path; cache is empty
        repo_dir, url = _make_repo_dir(tmp_path, "PaymentsRepo")

        # When: the factory routes a numeric input with working_directory
        with patch(_REPO_PATCH, return_value=_mock_repo(url)):
            ctx = establish_work_item_context("42", working_directory=str(repo_dir))

        # Then: the numeric branch was taken
        assert ctx.source == "repository_context", (
            f"Expected source 'repository_context', got '{ctx.source}'"
        )
        assert ctx.work_item_id == 42, f"Expected work_item_id=42, got {ctx.work_item_id}"

    def test_empty_input_raises_actionable_error(self) -> None:
        """
        Given an empty string or whitespace-only string
        When establish_work_item_context is called
        Then ActionableError.validation is raised with both URL and ID
            examples in the suggestion
        """
        # Given/When/Then: empty input
        with pytest.raises(ActionableError) as exc_info:
            establish_work_item_context("")

        err = exc_info.value
        assert err.suggestion is not None, "Expected suggestion, got None"
        assert "_workitems/edit" in err.suggestion or "url" in err.suggestion.lower(), (
            f"Expected URL example in suggestion, got: {err.suggestion!r}"
        )

        # Given/When/Then: whitespace-only input
        with pytest.raises(ActionableError):
            establish_work_item_context("   ")

    def test_unrecognised_input_raises_actionable_error_naming_input(self) -> None:
        """
        Given a string that is neither URL-shaped nor numeric (e.g. "abc")
        When establish_work_item_context is called
        Then ActionableError.validation is raised with the offending
            input quoted in the reason
        """
        # Given: input that is not URL-shaped or numeric
        bad_input = "abc"

        # When/Then: factory raises with the input named in the message
        with pytest.raises(ActionableError) as exc_info:
            establish_work_item_context(bad_input)

        assert "abc" in str(exc_info.value), (
            f"Expected the offending input 'abc' in the error message, got: {exc_info.value}"
        )

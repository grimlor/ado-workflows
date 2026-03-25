"""
BDD tests for ado_workflows.lifecycle — PR lifecycle operations.

Covers:
- TestCreatePullRequest: SDK-based PR creation with branch normalization

Public API surface (from src/ado_workflows/lifecycle.py):
    create_pull_request(client: AdoClient, repository: str,
                        source_branch: str, target_branch: str,
                        project: str, *, title: str | None = None,
                        description: str | None = None,
                        is_draft: bool = False) -> CreatedPR
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.lifecycle import create_pull_request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(
    *,
    pr_id: int = 42,
    title: str = "Add feature X",
    url: str = "https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42",
    source_branch: str = "refs/heads/feature/x",
    target_branch: str = "refs/heads/main",
    is_draft: bool = False,
) -> Mock:
    """Return a mock AdoClient whose git.create_pull_request returns a PR."""
    client = Mock()
    response = Mock()
    response.pull_request_id = pr_id
    response.url = url
    response.title = title
    response.source_ref_name = source_branch
    response.target_ref_name = target_branch
    response.is_draft = is_draft
    client.git.create_pull_request.return_value = response
    return client


class TestCreatePullRequest:
    """
    REQUIREMENT: create_pull_request() creates a PR via the SDK and returns
    a typed result.

    WHO: MCP tools, CI integrations, any automation creating PRs.
    WHAT: (1) valid branches return a CreatedPR with correct fields from
              the SDK response
          (2) branch names without refs/heads/ prefix are normalized
          (3) branch names already with refs/heads/ prefix are not doubled
          (4) optional title and description are passed to the SDK model
          (5) is_draft=True is passed to the SDK model
          (6) an SDK exception raises ActionableError
    WHY: Replaces az repos pr create subprocess call. SDK passes objects
         directly, eliminating CLI JSON-encoding fragility.

    MOCK BOUNDARY:
        Mock:  client.git.create_pull_request
        Real:  create_pull_request, branch normalization, CreatedPR construction
        Never: N/A
    """

    def test_valid_branches_return_created_pr(self) -> None:
        """
        Given valid branch names
        When create_pull_request is called
        Then returns CreatedPR with correct fields
        """
        # Given: a mock client that returns a successful SDK response
        client = _mock_client(
            pr_id=99,
            title="My PR",
            url="https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/99",
            source_branch="refs/heads/feature/abc",
            target_branch="refs/heads/main",
        )

        # When: create_pull_request is called with valid branches
        result = create_pull_request(
            client,
            "Repo",
            "feature/abc",
            "main",
            "Proj",
            title="My PR",
        )

        # Then: CreatedPR has the correct fields from the SDK response
        assert result.pr_id == 99, f"Expected pr_id=99, got {result.pr_id}"
        assert result.url == "https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/99", (
            f"Expected correct URL, got {result.url}"
        )
        assert result.title == "My PR", f"Expected title='My PR', got {result.title!r}"
        assert result.source_branch == "refs/heads/feature/abc", (
            f"Expected source_branch='refs/heads/feature/abc', got {result.source_branch!r}"
        )
        assert result.target_branch == "refs/heads/main", (
            f"Expected target_branch='refs/heads/main', got {result.target_branch!r}"
        )
        assert result.is_draft is False, f"Expected is_draft=False, got {result.is_draft}"

    def test_branch_prefix_added_when_missing(self) -> None:
        """
        Given branches without refs/heads/ prefix
        When create_pull_request is called
        Then prefix is added automatically to the SDK model
        """
        # Given: a mock client
        client = _mock_client()

        # When: called with bare branch names
        create_pull_request(client, "Repo", "feature/x", "main", "Proj")

        # Then: the SDK model received refs/heads/ prefixed branches
        call_args = client.git.create_pull_request.call_args
        pr_model = call_args[0][0]  # first positional arg
        assert pr_model.source_ref_name == "refs/heads/feature/x", (
            f"Expected refs/heads/feature/x, got {pr_model.source_ref_name!r}"
        )
        assert pr_model.target_ref_name == "refs/heads/main", (
            f"Expected refs/heads/main, got {pr_model.target_ref_name!r}"
        )

    def test_branch_prefix_not_doubled(self) -> None:
        """
        Given branches already with refs/heads/ prefix
        When create_pull_request is called
        Then prefix is not doubled
        """
        # Given: a mock client
        client = _mock_client()

        # When: called with already-prefixed branch names
        create_pull_request(
            client,
            "Repo",
            "refs/heads/feature/x",
            "refs/heads/main",
            "Proj",
        )

        # Then: the SDK model has exactly one prefix
        call_args = client.git.create_pull_request.call_args
        pr_model = call_args[0][0]
        assert pr_model.source_ref_name == "refs/heads/feature/x", (
            f"Expected single prefix, got {pr_model.source_ref_name!r}"
        )
        assert pr_model.target_ref_name == "refs/heads/main", (
            f"Expected single prefix, got {pr_model.target_ref_name!r}"
        )

    def test_optional_title_and_description_passed_to_sdk(self) -> None:
        """
        Given optional title and description
        When create_pull_request is called
        Then they are passed to the SDK model
        """
        # Given: a mock client
        client = _mock_client(title="Custom Title")

        # When: called with title and description
        create_pull_request(
            client,
            "Repo",
            "feature/x",
            "main",
            "Proj",
            title="Custom Title",
            description="Detailed description of changes",
        )

        # Then: the SDK model includes title and description
        call_args = client.git.create_pull_request.call_args
        pr_model = call_args[0][0]
        assert pr_model.title == "Custom Title", (
            f"Expected title='Custom Title', got {pr_model.title!r}"
        )
        assert pr_model.description == "Detailed description of changes", (
            f"Expected description passed through, got {pr_model.description!r}"
        )

    def test_is_draft_passed_to_sdk(self) -> None:
        """
        Given is_draft=True
        When create_pull_request is called
        Then SDK model has is_draft=True
        """
        # Given: a mock client returning a draft PR
        client = _mock_client(is_draft=True)

        # When: called with is_draft=True
        result = create_pull_request(
            client,
            "Repo",
            "feature/x",
            "main",
            "Proj",
            is_draft=True,
        )

        # Then: the SDK model has is_draft=True and result reflects it
        call_args = client.git.create_pull_request.call_args
        pr_model = call_args[0][0]
        assert pr_model.is_draft is True, (
            f"Expected SDK model is_draft=True, got {pr_model.is_draft}"
        )
        assert result.is_draft is True, f"Expected result.is_draft=True, got {result.is_draft}"

    def test_sdk_exception_raises_actionable_error(self) -> None:
        """
        Given the SDK raises an exception
        When create_pull_request is called
        Then raises ActionableError
        """
        # Given: a client whose SDK call raises
        client = Mock()
        client.git.create_pull_request.side_effect = Exception("TF401398: Source branch not found")

        # When/Then: ActionableError is raised with context
        with pytest.raises(ActionableError) as exc_info:
            create_pull_request(client, "Repo", "bad-branch", "main", "Proj")

        error_msg = str(exc_info.value)
        assert "TF401398" in error_msg, (
            f"Expected SDK error message in ActionableError, got: {error_msg}"
        )
        assert "Repo" in error_msg, f"Expected repository name in error, got: {error_msg}"

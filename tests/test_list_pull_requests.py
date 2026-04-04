"""
BDD tests for ado_workflows pull request listing — FR3a B1 + B4.

Covers:
- TestListPullRequests: list PRs matching search criteria via SDK
- TestPullRequestSummaryWebUrl: computed web_url on PullRequestSummary

Public API surface (new in FR3a):
    From src/ado_workflows/listing.py:
        list_pull_requests(
            client: AdoClient, project: str, *,
            creator_id: str | None, reviewer_id: str | None,
            status: str, repository_id: str | None, top: int,
        ) -> list[PullRequestSummary]

    From src/ado_workflows/models.py:
        PullRequestSummary(pr_id, title, status, created_by, creation_date,
            source_branch, target_branch, repository_name, web_url,
            is_draft, merge_status)
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.listing import list_pull_requests
from ado_workflows.models import PullRequestSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sdk_pr(
    *,
    pr_id: int = 42,
    title: str = "Add feature support",
    status: str = "active",
    created_by_name: str = "Alice Smith",
    creation_date: str = "2026-03-20T10:00:00Z",
    source_branch: str = "refs/heads/feature/data-gathering",
    target_branch: str = "refs/heads/main",
    repo_name: str = "ado-workflows",
    repo_web_url: str = "https://dev.azure.com/org/project/_git/ado-workflows",
    is_draft: bool = False,
    merge_status: str = "succeeded",
) -> Mock:
    """Build a mock SDK GitPullRequest for list responses."""
    pr = Mock()
    pr.pull_request_id = pr_id
    pr.title = title
    pr.status = status
    pr.created_by.display_name = created_by_name
    pr.creation_date = creation_date
    pr.source_ref_name = source_branch
    pr.target_ref_name = target_branch
    pr.repository.name = repo_name
    pr.repository.web_url = repo_web_url
    pr.is_draft = is_draft
    pr.merge_status = merge_status
    return pr


def _mock_client(
    prs: list[Mock] | None = None,
    *,
    error: Exception | None = None,
    use_project_scope: bool = False,
) -> Mock:
    """Build a mock AdoClient with git.get_pull_requests* configured."""
    client = Mock()
    if error:
        client.git.get_pull_requests.side_effect = error
        client.git.get_pull_requests_by_project.side_effect = error
    elif use_project_scope:
        client.git.get_pull_requests_by_project.return_value = prs or []
    else:
        client.git.get_pull_requests.return_value = prs or []
    return client


# ---------------------------------------------------------------------------
# TestListPullRequests — B1
# ---------------------------------------------------------------------------


class TestListPullRequests:
    """
    REQUIREMENT: List pull requests matching search criteria using the
    Azure DevOps SDK.

    WHO: Reporting tools, PR dashboards, automation scripts
    WHAT: (1) searches PRs by creator_id and returns PullRequestSummary with web_url
          (2) empty result returns empty list (not an error)
          (3) status filter restricts results to matching status
          (4) repository_id routes to get_pull_requests() scoped to that repo
          (5) no repository_id routes to get_pull_requests_by_project()
          (6) reviewer_id is included in search criteria
          (7) SDK errors raise ActionableError
    WHY: Enables querying PRs by developer across a project for reporting,
         dashboards, and workflow automation.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_requests(), client.git.get_pull_requests_by_project()
        Real:  list_pull_requests(), model mapping, web_url construction
        Never: nothing
    """

    def test_creator_id_returns_matching_prs_as_summary_with_web_url(self) -> None:
        """
        Given a creator_id,
        When called with status="all",
        Then returns matching PRs as PullRequestSummary with web_url.
        """
        # Given: SDK returns one PR for the creator
        pr_mock = _sdk_pr(pr_id=101, title="Implement feature")
        client = _mock_client([pr_mock])

        # When: list_pull_requests is called with a creator_id
        result = list_pull_requests(
            client,
            "MyProject",
            creator_id="a1b2c3d4-guid",
            repository_id="ado-workflows",
        )

        # Then: returns PullRequestSummary with computed web_url
        assert len(result) == 1, f"Expected 1 PullRequestSummary, got {len(result)}"
        pr = result[0]
        assert isinstance(pr, PullRequestSummary), (
            f"Expected PullRequestSummary, got {type(pr).__name__}"
        )
        assert pr.pr_id == 101, f"Expected pr_id=101, got {pr.pr_id}"
        assert pr.title == "Implement feature", (
            f"Expected title='Implement feature', got {pr.title!r}"
        )
        assert pr.web_url == (
            "https://dev.azure.com/org/project/_git/ado-workflows/pullrequest/101"
        ), f"Expected constructed web_url, got {pr.web_url!r}"

    def test_no_matching_prs_returns_empty_list(self) -> None:
        """
        Given no matching PRs,
        When called,
        Then returns empty list.
        """
        # Given: SDK returns no PRs
        client = _mock_client([])

        # When: list_pull_requests is called
        result = list_pull_requests(
            client,
            "MyProject",
            repository_id="some-repo",
        )

        # Then: returns empty list
        assert result == [], f"Expected empty list, got {result}"

    def test_status_filter_restricts_results(self) -> None:
        """
        Given status="active",
        When called,
        Then search criteria uses the specified status.
        """
        # Given: SDK configured
        client = _mock_client([])

        # When: called with status="active"
        list_pull_requests(
            client,
            "MyProject",
            status="active",
            repository_id="repo",
        )

        # Then: the SDK was called with search criteria containing status="active"
        call_args = client.git.get_pull_requests.call_args
        criteria = (
            call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("search_criteria")
        )
        assert criteria.status == "active", (
            f"Expected status='active' in search criteria, got {criteria.status!r}"
        )

    def test_repository_id_routes_to_repo_scoped_method(self) -> None:
        """
        Given repository_id is provided,
        When called,
        Then uses get_pull_requests() scoped to that repository.
        """
        # Given: SDK configured for repo-scoped call
        client = _mock_client([])

        # When: called with an explicit repository_id
        list_pull_requests(
            client,
            "MyProject",
            repository_id="my-repo-guid",
        )

        # Then: get_pull_requests (repo-scoped) was called, not the project-scoped variant
        client.git.get_pull_requests.assert_called_once()
        client.git.get_pull_requests_by_project.assert_not_called()

    def test_no_repository_id_routes_to_project_scoped_method(self) -> None:
        """
        Given no repository_id,
        When called,
        Then uses get_pull_requests_by_project() for project-wide search.
        """
        # Given: SDK configured for project-scoped call
        client = _mock_client([], use_project_scope=True)

        # When: called without repository_id
        list_pull_requests(client, "MyProject")

        # Then: get_pull_requests_by_project (project-scoped) was called
        client.git.get_pull_requests_by_project.assert_called_once()
        client.git.get_pull_requests.assert_not_called()

    def test_reviewer_id_included_in_search_criteria(self) -> None:
        """
        Given reviewer_id is provided,
        When called,
        Then search criteria includes the reviewer_id.
        """
        # Given: SDK configured
        client = _mock_client([])

        # When: called with reviewer_id
        list_pull_requests(
            client,
            "MyProject",
            reviewer_id="reviewer-guid-123",
            repository_id="repo",
        )

        # Then: search criteria includes the reviewer_id
        call_args = client.git.get_pull_requests.call_args
        criteria = (
            call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("search_criteria")
        )
        assert criteria.reviewer_id == "reviewer-guid-123", (
            f"Expected reviewer_id='reviewer-guid-123', got {criteria.reviewer_id!r}"
        )

    def test_sdk_error_raises_actionable_error(self) -> None:
        """
        Given an SDK error,
        When called,
        Then raises ActionableError.
        """
        # Given: SDK raises an exception
        client = _mock_client(error=RuntimeError("connection refused"))

        # When / Then: raises ActionableError
        with pytest.raises(ActionableError) as exc_info:
            list_pull_requests(
                client,
                "MyProject",
                repository_id="repo",
            )
        assert "connection refused" in str(exc_info.value), (
            f"Expected error message to contain 'connection refused', got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# TestPullRequestSummaryWebUrl — B4
# ---------------------------------------------------------------------------


class TestPullRequestSummaryWebUrl:
    """
    REQUIREMENT: Construct a clickable PR web URL from PR data.

    WHO: Any consumer rendering PR links
    WHAT: (1) web_url is computed as {repository.web_url}/pullrequest/{pr_id}
          (2) web_url is available on every list result without additional API calls
    WHY: The REST API URL in PullRequestDetail.url is not user-facing.
         Consumers need browser-clickable PR links.

    MOCK BOUNDARY:
        Covered by B1 tests — web_url is computed during list_pull_requests() mapping.
    """

    def test_web_url_constructed_from_repo_url_and_pr_id(self) -> None:
        """
        Given a PR from repo with web_url 'https://dev.azure.com/org/proj/_git/repo',
        When mapped to PullRequestSummary,
        Then web_url equals '{repo_web_url}/pullrequest/{pr_id}'.
        """
        # Given: SDK returns a PR with known repo web_url and pr_id
        pr_mock = _sdk_pr(
            pr_id=999,
            repo_web_url="https://dev.azure.com/myorg/myproj/_git/my-repo",
        )
        client = _mock_client([pr_mock])

        # When: list_pull_requests maps the result
        result = list_pull_requests(
            client,
            "MyProject",
            repository_id="my-repo",
        )

        # Then: web_url is correctly constructed
        assert len(result) == 1, f"Expected 1 result, got {len(result)}"
        assert result[0].web_url == (
            "https://dev.azure.com/myorg/myproj/_git/my-repo/pullrequest/999"
        ), f"Expected constructed web_url, got {result[0].web_url!r}"

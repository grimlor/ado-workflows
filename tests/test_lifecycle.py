"""
BDD tests for ado_workflows.lifecycle — PR lifecycle operations.

Covers:
- TestCreatePullRequest: SDK-based PR creation with branch normalization
- TestGetPullRequest: retrieve full PR metadata via get_pull_request_by_id
- TestUpdatePullRequest: edit title and/or description
- TestRetargetPullRequest: change target branch
- TestSetDraftStatus: toggle draft/published state
- TestAbandonPullRequest: close without merging
- TestCompletePullRequest: merge with configurable strategy

Public API surface (from src/ado_workflows/lifecycle.py):
    create_pull_request(client, repository, source_branch, target_branch,
                        project, *, title, description, is_draft) -> CreatedPR
    get_pull_request(client, pr_id, project) -> PullRequestDetail
    update_pull_request(client, repository, pr_id, project, *,
                        title, description) -> PullRequestDetail
    retarget_pull_request(client, repository, pr_id, project, *,
                          target_branch) -> PullRequestDetail
    set_draft_status(client, repository, pr_id, project, *,
                     is_draft) -> PullRequestDetail
    abandon_pull_request(client, repository, pr_id, project) -> PullRequestDetail
    complete_pull_request(client, repository, pr_id, project, *,
                          merge_strategy, delete_source_branch,
                          transition_work_items, merge_commit_message,
                          bypass_policy, bypass_reason) -> PullRequestDetail

I/O boundaries:
    client.git.create_pull_request (SDK REST call)
    client.git.get_pull_request_by_id (SDK REST call)
    client.git.update_pull_request (SDK REST call)
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.lifecycle import create_pull_request
from ado_workflows.models import MergeStrategy, PullRequestDetail

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


def _fake_sdk_pr(
    *,
    pr_id: int = 42,
    title: str = "Add feature X",
    description: str | None = "PR description",
    url: str = "https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42",
    source_branch: str = "refs/heads/feature/x",
    target_branch: str = "refs/heads/main",
    status: str = "active",
    is_draft: bool = False,
    merge_status: str = "succeeded",
    created_by_name: str = "Alice Dev",
    creation_date: str = "2026-03-15T10:00:00Z",
    reviewers: list[Mock] | None = None,
    labels: list[Mock] | None = None,
    work_item_refs: list[Mock] | None = None,
) -> Mock:
    """Return a mock SDK GitPullRequest response."""
    pr = Mock()
    pr.pull_request_id = pr_id
    pr.title = title
    pr.description = description
    pr.url = url
    pr.source_ref_name = source_branch
    pr.target_ref_name = target_branch
    pr.status = status
    pr.is_draft = is_draft
    pr.merge_status = merge_status
    pr.creation_date = creation_date

    created_by = Mock()
    created_by.display_name = created_by_name
    pr.created_by = created_by

    pr.reviewers = reviewers or []
    pr.labels = labels or []
    pr.work_item_refs = work_item_refs or []
    return pr


def _fake_reviewer(
    *,
    reviewer_id: str = "guid-reviewer-1",
    display_name: str = "Bob Reviewer",
    unique_name: str = "bob@example.com",
    vote: int = 10,
    is_required: bool = False,
    is_container: bool = False,
) -> Mock:
    """Return a mock SDK IdentityRefWithVote."""
    reviewer = Mock()
    reviewer.id = reviewer_id
    reviewer.display_name = display_name
    reviewer.unique_name = unique_name
    reviewer.vote = vote
    reviewer.is_required = is_required
    reviewer.is_container = is_container
    return reviewer


def _fake_label(
    *,
    label_id: str = "label-guid-1",
    name: str = "bug-fix",
) -> Mock:
    """Return a mock SDK WebApiTagDefinition."""
    label = Mock()
    label.id = label_id
    label.name = name
    return label


def _fake_work_item_ref(
    *,
    wi_id: str = "37290513",
    url: str = "https://dev.azure.com/Org/_apis/wit/workItems/37290513",
) -> Mock:
    """Return a mock SDK ResourceRef for a work item."""
    ref = Mock()
    ref.id = wi_id
    ref.url = url
    return ref


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


# ---------------------------------------------------------------------------
# TestGetPullRequest
# ---------------------------------------------------------------------------


class TestGetPullRequest:
    """
    REQUIREMENT: Retrieve full PR metadata including reviewers, labels,
    and work items.

    WHO: Any tool or agent needing PR details (e.g., FR4b
         prescriptive_comments.py)
    WHAT: (1) an active PR with reviewers, labels, and work items returns
              a fully populated PullRequestDetail
          (2) a PR with no reviewers, labels, or work items returns
              PullRequestDetail with empty lists
          (3) an invalid PR ID raises ActionableError with guidance
    WHY: FR4b currently shells out to `az repos pr show` — this replaces it

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_by_id (SDK REST call)
        Real:  PullRequestDetail construction, reviewer/label/work-item mapping
        Never: AdoClient, ConnectionFactory
    """

    def test_active_pr_returns_fully_populated_detail(self) -> None:
        """
        Given an active PR with reviewers and labels
        When get_pull_request is called
        Then returns PullRequestDetail with all fields populated
        """
        # Given: SDK returns a PR with reviewers, labels, and work items
        reviewer = _fake_reviewer(vote=10, display_name="Bob Reviewer")
        label = _fake_label(name="bug-fix")
        wi_ref = _fake_work_item_ref(wi_id="37290513")
        sdk_pr = _fake_sdk_pr(
            pr_id=99,
            title="Fix login bug",
            reviewers=[reviewer],
            labels=[label],
            work_item_refs=[wi_ref],
        )
        client = Mock()
        client.git.get_pull_request_by_id.return_value = sdk_pr

        # When: get_pull_request is called
        from ado_workflows.lifecycle import get_pull_request

        result = get_pull_request(client, pr_id=99, project="Proj")

        # Then: PullRequestDetail has all fields populated
        assert isinstance(result, PullRequestDetail), (
            f"Expected PullRequestDetail, got {type(result).__name__}"
        )
        assert result.pr_id == 99, f"Expected pr_id=99, got {result.pr_id}"
        assert result.title == "Fix login bug", (
            f"Expected title='Fix login bug', got {result.title!r}"
        )
        assert result.status == "active", f"Expected status='active', got {result.status!r}"
        assert len(result.reviewers) == 1, f"Expected 1 reviewer, got {len(result.reviewers)}"
        assert result.reviewers[0].display_name == "Bob Reviewer", (
            f"Expected reviewer 'Bob Reviewer', got {result.reviewers[0].display_name!r}"
        )
        assert len(result.labels) == 1, f"Expected 1 label, got {len(result.labels)}"
        assert result.labels[0].name == "bug-fix", (
            f"Expected label 'bug-fix', got {result.labels[0].name!r}"
        )
        assert len(result.work_item_refs) == 1, (
            f"Expected 1 work item ref, got {len(result.work_item_refs)}"
        )
        assert result.work_item_refs[0].id == "37290513", (
            f"Expected work item ID '37290513', got {result.work_item_refs[0].id!r}"
        )

    def test_pr_with_no_reviewers_or_labels_returns_empty_lists(self) -> None:
        """
        Given a PR with no reviewers, labels, or work items
        When get_pull_request is called
        Then returns PullRequestDetail with empty lists
        """
        # Given: SDK returns a PR with empty collections
        sdk_pr = _fake_sdk_pr(pr_id=50, reviewers=[], labels=[], work_item_refs=[])
        client = Mock()
        client.git.get_pull_request_by_id.return_value = sdk_pr

        # When: get_pull_request is called
        from ado_workflows.lifecycle import get_pull_request

        result = get_pull_request(client, pr_id=50, project="Proj")

        # Then: all collection fields are empty lists
        assert result.reviewers == [], f"Expected empty reviewers, got {result.reviewers}"
        assert result.labels == [], f"Expected empty labels, got {result.labels}"
        assert result.work_item_refs == [], (
            f"Expected empty work_item_refs, got {result.work_item_refs}"
        )

    def test_invalid_pr_id_raises_actionable_error(self) -> None:
        """
        Given an invalid PR ID
        When get_pull_request is called
        Then raises ActionableError with guidance
        """
        # Given: SDK raises for a non-existent PR
        client = Mock()
        client.git.get_pull_request_by_id.side_effect = Exception(
            "TF401180: Pull request 99999 not found"
        )

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import get_pull_request

        with pytest.raises(ActionableError) as exc_info:
            get_pull_request(client, pr_id=99999, project="Proj")

        error_msg = str(exc_info.value)
        assert "99999" in error_msg, f"Expected PR ID in error message, got: {error_msg}"


# ---------------------------------------------------------------------------
# TestUpdatePullRequest
# ---------------------------------------------------------------------------


class TestUpdatePullRequest:
    """
    REQUIREMENT: Update title and/or description of an existing PR.

    WHO: Agents managing PR metadata
    WHAT: (1) updating only the title returns PullRequestDetail with the
              new title
          (2) updating only the description returns PullRequestDetail with
              the new description
          (3) updating both title and description in one call changes both
              fields
          (4) calling with neither title nor description raises
              ActionableError (no-op guard)
          (5) an SDK failure raises ActionableError
    WHY: No function exists to edit PR metadata programmatically

    MOCK BOUNDARY:
        Mock:  client.git.update_pull_request (SDK REST call)
        Real:  model construction, return mapping
        Never: AdoClient
    """

    def test_title_only_update(self) -> None:
        """
        Given an active PR
        When title is updated
        Then returns PullRequestDetail with new title
        """
        # Given: SDK returns updated PR with new title
        sdk_pr = _fake_sdk_pr(pr_id=42, title="New Title")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: update_pull_request is called with title only
        from ado_workflows.lifecycle import update_pull_request

        result = update_pull_request(client, "Repo", pr_id=42, project="Proj", title="New Title")

        # Then: result has the new title
        assert result.title == "New Title", f"Expected title='New Title', got {result.title!r}"

    def test_description_only_update(self) -> None:
        """
        Given an active PR
        When description is updated
        Then returns PullRequestDetail with new description
        """
        # Given: SDK returns updated PR with new description
        sdk_pr = _fake_sdk_pr(pr_id=42, description="Updated desc")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: update_pull_request is called with description only
        from ado_workflows.lifecycle import update_pull_request

        result = update_pull_request(
            client, "Repo", pr_id=42, project="Proj", description="Updated desc"
        )

        # Then: result has the new description
        assert result.description == "Updated desc", (
            f"Expected description='Updated desc', got {result.description!r}"
        )

    def test_title_and_description_updated_together(self) -> None:
        """
        Given both title and description
        When updated together
        Then both fields change in one call
        """
        # Given: SDK returns PR with both fields updated
        sdk_pr = _fake_sdk_pr(pr_id=42, title="New T", description="New D")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: both are provided
        from ado_workflows.lifecycle import update_pull_request

        result = update_pull_request(
            client,
            "Repo",
            pr_id=42,
            project="Proj",
            title="New T",
            description="New D",
        )

        # Then: both fields are updated
        assert result.title == "New T", f"Expected title='New T', got {result.title!r}"
        assert result.description == "New D", (
            f"Expected description='New D', got {result.description!r}"
        )

    def test_no_op_raises_actionable_error(self) -> None:
        """
        Given neither title nor description
        When called with no changes
        Then raises ActionableError (no-op guard)
        """
        # Given: a client (should not be called)
        client = Mock()

        # When/Then: ActionableError raised before SDK is called
        from ado_workflows.lifecycle import update_pull_request

        with pytest.raises(ActionableError):
            update_pull_request(client, "Repo", pr_id=42, project="Proj")

        # Then: SDK was never called
        client.git.update_pull_request.assert_not_called()

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given an SDK failure
        When update_pull_request is called
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.update_pull_request.side_effect = Exception("Permission denied")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import update_pull_request

        with pytest.raises(ActionableError) as exc_info:
            update_pull_request(
                client, "Repo", pr_id=42, project="Proj", title="New Title"
            )

        error_msg = str(exc_info.value)
        assert "Permission denied" in error_msg, (
            f"Expected SDK error in message, got: {error_msg}"
        )


# ---------------------------------------------------------------------------
# TestRetargetPullRequest
# ---------------------------------------------------------------------------


class TestRetargetPullRequest:
    """
    REQUIREMENT: Change the target branch of an existing PR.

    WHO: Agents or developers retargeting PRs during branch management
    WHAT: (1) retargeting to a new branch returns PullRequestDetail with
              the updated target
          (2) a branch name without refs/heads/ prefix is normalised
              automatically
          (3) a branch name that already has the prefix is not
              double-prefixed
          (4) an SDK failure raises ActionableError
    WHY: Common operation during branch renames or multi-stage merges

    MOCK BOUNDARY:
        Mock:  client.git.update_pull_request
        Real:  _normalize_branch(), model construction
        Never: AdoClient
    """

    def test_retarget_updates_target_branch(self) -> None:
        """
        Given an active PR targeting main
        When retargeted to develop
        Then returns detail with target_branch == "refs/heads/develop"
        """
        # Given: SDK returns PR with new target
        sdk_pr = _fake_sdk_pr(target_branch="refs/heads/develop")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: retarget_pull_request is called
        from ado_workflows.lifecycle import retarget_pull_request

        result = retarget_pull_request(
            client, "Repo", pr_id=42, project="Proj", target_branch="develop"
        )

        # Then: target branch is updated
        assert result.target_branch == "refs/heads/develop", (
            f"Expected target_branch='refs/heads/develop', got {result.target_branch!r}"
        )

    def test_branch_prefix_added_when_missing(self) -> None:
        """
        Given branch name without prefix
        When retargeted
        Then refs/heads/ is prepended
        """
        # Given: SDK returns a PR
        sdk_pr = _fake_sdk_pr(target_branch="refs/heads/develop")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: called with bare branch name
        from ado_workflows.lifecycle import retarget_pull_request

        retarget_pull_request(client, "Repo", pr_id=42, project="Proj", target_branch="develop")

        # Then: SDK model received the prefixed branch
        call_args = client.git.update_pull_request.call_args
        pr_model = call_args[0][0]
        assert pr_model.target_ref_name == "refs/heads/develop", (
            f"Expected refs/heads/develop in SDK model, got {pr_model.target_ref_name!r}"
        )

    def test_branch_prefix_not_doubled(self) -> None:
        """
        Given branch name with prefix
        When retargeted
        Then prefix is not duplicated
        """
        # Given: SDK returns a PR
        sdk_pr = _fake_sdk_pr(target_branch="refs/heads/develop")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: called with already-prefixed branch
        from ado_workflows.lifecycle import retarget_pull_request

        retarget_pull_request(
            client,
            "Repo",
            pr_id=42,
            project="Proj",
            target_branch="refs/heads/develop",
        )

        # Then: SDK model has exactly one prefix
        call_args = client.git.update_pull_request.call_args
        pr_model = call_args[0][0]
        assert pr_model.target_ref_name == "refs/heads/develop", (
            f"Expected single prefix, got {pr_model.target_ref_name!r}"
        )

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When retargeted
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.update_pull_request.side_effect = Exception("Branch not found")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import retarget_pull_request

        with pytest.raises(ActionableError) as exc_info:
            retarget_pull_request(
                client,
                "Repo",
                pr_id=42,
                project="Proj",
                target_branch="nonexistent",
            )

        error_msg = str(exc_info.value)
        assert "Branch not found" in error_msg, f"Expected SDK error in message, got: {error_msg}"


# ---------------------------------------------------------------------------
# TestSetDraftStatus
# ---------------------------------------------------------------------------


class TestSetDraftStatus:
    """
    REQUIREMENT: Toggle a PR between draft and published state.

    WHO: Agents managing PR lifecycle
    WHAT: (1) setting is_draft=False on a draft PR returns PullRequestDetail
              with is_draft=False
          (2) setting is_draft=True on an active PR returns PullRequestDetail
              with is_draft=True
          (3) an SDK failure raises ActionableError
    WHY: Draft-to-active transition is needed to publish PRs for review

    MOCK BOUNDARY:
        Mock:  client.git.update_pull_request
        Real:  model construction
        Never: AdoClient
    """

    def test_publish_draft_pr(self) -> None:
        """
        Given a draft PR
        When set_draft_status(is_draft=False)
        Then returns detail with is_draft=False
        """
        # Given: SDK returns a published PR
        sdk_pr = _fake_sdk_pr(is_draft=False)
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: draft status set to False
        from ado_workflows.lifecycle import set_draft_status

        result = set_draft_status(client, "Repo", pr_id=42, project="Proj", is_draft=False)

        # Then: result reflects published state
        assert result.is_draft is False, f"Expected is_draft=False, got {result.is_draft}"

    def test_draft_active_pr(self) -> None:
        """
        Given an active PR
        When set_draft_status(is_draft=True)
        Then returns detail with is_draft=True
        """
        # Given: SDK returns a draft PR
        sdk_pr = _fake_sdk_pr(is_draft=True)
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: draft status set to True
        from ado_workflows.lifecycle import set_draft_status

        result = set_draft_status(client, "Repo", pr_id=42, project="Proj", is_draft=True)

        # Then: result reflects draft state
        assert result.is_draft is True, f"Expected is_draft=True, got {result.is_draft}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When called
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.update_pull_request.side_effect = Exception("Permission denied")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import set_draft_status

        with pytest.raises(ActionableError):
            set_draft_status(client, "Repo", pr_id=42, project="Proj", is_draft=False)


# ---------------------------------------------------------------------------
# TestAbandonPullRequest
# ---------------------------------------------------------------------------


class TestAbandonPullRequest:
    """
    REQUIREMENT: Abandon (close without merging) an existing PR.

    WHO: Agents or developers cleaning up obsolete PRs
    WHAT: (1) abandoning an active PR returns PullRequestDetail with
              status "abandoned"
          (2) abandoning an already-abandoned PR succeeds idempotently
          (3) an SDK failure raises ActionableError
    WHY: No programmatic abandon exists in the library

    MOCK BOUNDARY:
        Mock:  client.git.update_pull_request
        Real:  model construction, status validation
        Never: AdoClient
    """

    def test_abandon_active_pr(self) -> None:
        """
        Given an active PR
        When abandoned
        Then returns detail with status == "abandoned"
        """
        # Given: SDK returns abandoned PR
        sdk_pr = _fake_sdk_pr(status="abandoned")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: abandon_pull_request is called
        from ado_workflows.lifecycle import abandon_pull_request

        result = abandon_pull_request(client, "Repo", pr_id=42, project="Proj")

        # Then: status is abandoned
        assert result.status == "abandoned", f"Expected status='abandoned', got {result.status!r}"

    def test_abandon_already_abandoned_pr_is_idempotent(self) -> None:
        """
        Given an already-abandoned PR
        When abandoned again
        Then returns detail (idempotent)
        """
        # Given: SDK returns abandoned PR (already in that state)
        sdk_pr = _fake_sdk_pr(status="abandoned")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: abandon_pull_request is called
        from ado_workflows.lifecycle import abandon_pull_request

        result = abandon_pull_request(client, "Repo", pr_id=42, project="Proj")

        # Then: still returns a valid detail
        assert result.status == "abandoned", f"Expected idempotent abandon, got {result.status!r}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When abandoned
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.update_pull_request.side_effect = Exception("PR not found")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import abandon_pull_request

        with pytest.raises(ActionableError):
            abandon_pull_request(client, "Repo", pr_id=42, project="Proj")


# ---------------------------------------------------------------------------
# TestCompletePullRequest
# ---------------------------------------------------------------------------


class TestCompletePullRequest:
    """
    REQUIREMENT: Complete (merge) a PR with configurable merge strategy.

    WHO: Agents automating merge workflows
    WHAT: (1) completing with an explicit squash strategy returns
              PullRequestDetail with status "completed"
          (2) default parameters use squash merge, delete-source-branch,
              and transition-work-items
          (3) bypass_policy=True without bypass_reason raises
              ActionableError
          (4) an SDK failure indicating merge conflicts raises
              ActionableError with conflict guidance
          (5) any other SDK failure raises ActionableError
    WHY: Merge automation is a core lifecycle gap

    MOCK BOUNDARY:
        Mock:  client.git.update_pull_request
        Real:  MergeStrategy coercion, GitPullRequestCompletionOptions
               construction
        Never: AdoClient
    """

    def test_complete_with_squash_strategy(self) -> None:
        """
        Given an approved PR
        When completed with squash strategy
        Then returns detail with status == "completed"
        """
        # Given: SDK returns a completed PR
        sdk_pr = _fake_sdk_pr(status="completed")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: complete_pull_request is called with squash
        from ado_workflows.lifecycle import complete_pull_request

        result = complete_pull_request(
            client,
            "Repo",
            pr_id=42,
            project="Proj",
            merge_strategy=MergeStrategy.SQUASH,
        )

        # Then: status is completed
        assert result.status == "completed", f"Expected status='completed', got {result.status!r}"

    def test_defaults_use_squash_delete_source_transition(self) -> None:
        """
        Given default parameters
        When completed
        Then uses squash, delete-source, transition-work-items defaults
        """
        # Given: SDK returns a completed PR
        sdk_pr = _fake_sdk_pr(status="completed")
        client = Mock()
        client.git.update_pull_request.return_value = sdk_pr

        # When: complete_pull_request is called with defaults
        from ado_workflows.lifecycle import complete_pull_request

        complete_pull_request(client, "Repo", pr_id=42, project="Proj")

        # Then: SDK model has correct defaults in completion options
        call_args = client.git.update_pull_request.call_args
        pr_model = call_args[0][0]
        opts = pr_model.completion_options
        assert opts.merge_strategy == "squash", (
            f"Expected default merge_strategy='squash', got {opts.merge_strategy!r}"
        )
        assert opts.delete_source_branch is True, (
            f"Expected default delete_source_branch=True, got {opts.delete_source_branch}"
        )
        assert opts.transition_work_items is True, (
            f"Expected default transition_work_items=True, got {opts.transition_work_items}"
        )

    def test_bypass_without_reason_raises_actionable_error(self) -> None:
        """
        Given bypass_policy=True without bypass_reason
        When completed
        Then raises ActionableError (reason required)
        """
        # Given: a client (should not be called)
        client = Mock()

        # When/Then: ActionableError raised before SDK call
        from ado_workflows.lifecycle import complete_pull_request

        with pytest.raises(ActionableError):
            complete_pull_request(
                client,
                "Repo",
                pr_id=42,
                project="Proj",
                bypass_policy=True,
            )

        # Then: SDK was never called
        client.git.update_pull_request.assert_not_called()

    def test_merge_conflicts_raise_actionable_error_with_guidance(self) -> None:
        """
        Given merge conflicts
        When completed
        Then raises ActionableError with conflict guidance
        """
        # Given: SDK raises with a conflict error
        client = Mock()
        client.git.update_pull_request.side_effect = Exception(
            "TF401180: The pull request has merge conflicts"
        )

        # When/Then: ActionableError includes conflict context
        from ado_workflows.lifecycle import complete_pull_request

        with pytest.raises(ActionableError) as exc_info:
            complete_pull_request(client, "Repo", pr_id=42, project="Proj")

        error_msg = str(exc_info.value)
        assert "conflict" in error_msg.lower(), (
            f"Expected conflict guidance in error, got: {error_msg}"
        )

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When completed
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.update_pull_request.side_effect = Exception("Permission denied")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import complete_pull_request

        with pytest.raises(ActionableError):
            complete_pull_request(client, "Repo", pr_id=42, project="Proj")

"""
BDD tests for ado_workflows.lifecycle — PR reviewer, label, and work item operations.

Covers:
- TestAddReviewer: add optional or required reviewer
- TestRemoveReviewer: remove a reviewer
- TestListReviewers: list reviewers with vote details
- TestAddLabel: add a label/tag
- TestRemoveLabel: remove a label
- TestListLabels: list all labels
- TestGetPRWorkItemRefs: list linked work items (read-only)

Public API surface (from src/ado_workflows/lifecycle.py):
    add_reviewer(client, repository, pr_id, project, *,
                 reviewer_id, is_required) -> ReviewerDetail
    remove_reviewer(client, repository, pr_id, project, *,
                    reviewer_id) -> None
    list_reviewers(client, repository, pr_id,
                   project) -> list[ReviewerDetail]
    add_label(client, repository, pr_id, project, *,
              name) -> LabelDetail
    remove_label(client, repository, pr_id, project, *,
                 label_name) -> None
    list_labels(client, repository, pr_id,
                project) -> list[LabelDetail]
    get_pr_work_item_refs(client, repository, pr_id,
                          project) -> list[WorkItemRef]

I/O boundaries:
    client.git.create_pull_request_reviewer (SDK REST call)
    client.git.delete_pull_request_reviewer (SDK REST call)
    client.git.get_pull_request_reviewers (SDK REST call)
    client.git.create_pull_request_label (SDK REST call)
    client.git.delete_pull_request_labels (SDK REST call)
    client.git.get_pull_request_labels (SDK REST call)
    client.git.get_pull_request_work_item_refs (SDK REST call)
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.models import LabelDetail, ReviewerDetail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_reviewer_response(
    *,
    reviewer_id: str = "guid-reviewer-1",
    display_name: str = "Bob Reviewer",
    unique_name: str = "bob@example.com",
    vote: int = 0,
    is_required: bool = False,
    is_container: bool = False,
) -> Mock:
    """Return a mock SDK IdentityRefWithVote response."""
    reviewer = Mock()
    reviewer.id = reviewer_id
    reviewer.display_name = display_name
    reviewer.unique_name = unique_name
    reviewer.vote = vote
    reviewer.is_required = is_required
    reviewer.is_container = is_container
    return reviewer


def _fake_label_response(
    *,
    label_id: str = "label-guid-1",
    name: str = "bug-fix",
) -> Mock:
    """Return a mock SDK WebApiTagDefinition response."""
    label = Mock()
    label.id = label_id
    label.name = name
    return label


def _fake_work_item_ref_response(
    *,
    wi_id: str = "37290513",
    url: str = "https://dev.azure.com/Org/_apis/wit/workItems/37290513",
) -> Mock:
    """Return a mock SDK ResourceRef response."""
    ref = Mock()
    ref.id = wi_id
    ref.url = url
    return ref


# ---------------------------------------------------------------------------
# TestAddReviewer
# ---------------------------------------------------------------------------


class TestAddReviewer:
    """
    REQUIREMENT: Add a reviewer to a PR.

    WHO: Agents managing review assignments
    WHAT: (1) adding a reviewer as optional returns ReviewerDetail with
              is_required=False
          (2) adding a reviewer as required returns ReviewerDetail with
              is_required=True
          (3) an SDK failure raises ActionableError
    WHY: Automating reviewer assignment

    MOCK BOUNDARY:
        Mock:  client.git.create_pull_request_reviewer
        Real:  IdentityRefWithVote construction, required-flag mapping
        Never: AdoClient
    """

    def test_add_optional_reviewer(self) -> None:
        """
        Given a valid reviewer GUID
        When added as optional
        Then returns ReviewerDetail with is_required=False
        """
        # Given: SDK returns an optional reviewer
        sdk_reviewer = _fake_reviewer_response(
            reviewer_id="guid-1", display_name="Alice", is_required=False
        )
        client = Mock()
        client.git.create_pull_request_reviewer.return_value = sdk_reviewer

        # When: add_reviewer is called without is_required
        from ado_workflows.lifecycle import add_reviewer

        result = add_reviewer(client, "Repo", pr_id=42, project="Proj", reviewer_id="guid-1")

        # Then: result is ReviewerDetail with is_required=False
        assert isinstance(result, ReviewerDetail), (
            f"Expected ReviewerDetail, got {type(result).__name__}"
        )
        assert result.is_required is False, f"Expected is_required=False, got {result.is_required}"
        assert result.display_name == "Alice", (
            f"Expected display_name='Alice', got {result.display_name!r}"
        )

    def test_add_required_reviewer(self) -> None:
        """
        Given a valid reviewer GUID
        When added as required
        Then returns ReviewerDetail with is_required=True
        """
        # Given: SDK returns a required reviewer
        sdk_reviewer = _fake_reviewer_response(
            reviewer_id="guid-2", display_name="Bob", is_required=True
        )
        client = Mock()
        client.git.create_pull_request_reviewer.return_value = sdk_reviewer

        # When: add_reviewer is called with is_required=True
        from ado_workflows.lifecycle import add_reviewer

        result = add_reviewer(
            client,
            "Repo",
            pr_id=42,
            project="Proj",
            reviewer_id="guid-2",
            is_required=True,
        )

        # Then: result has is_required=True
        assert result.is_required is True, f"Expected is_required=True, got {result.is_required}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When adding
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.create_pull_request_reviewer.side_effect = Exception("Identity not found")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import add_reviewer

        with pytest.raises(ActionableError):
            add_reviewer(client, "Repo", pr_id=42, project="Proj", reviewer_id="bad-guid")


# ---------------------------------------------------------------------------
# TestRemoveReviewer
# ---------------------------------------------------------------------------


class TestRemoveReviewer:
    """
    REQUIREMENT: Remove a reviewer from a PR.

    WHO: Agents managing review assignments
    WHAT: (1) removing an existing reviewer succeeds and returns None
          (2) an SDK failure raises ActionableError
    WHY: Cleanup of incorrect or unnecessary reviewers

    MOCK BOUNDARY:
        Mock:  client.git.delete_pull_request_reviewer
        Real:  nothing beyond delegation
        Never: AdoClient
    """

    def test_remove_existing_reviewer(self) -> None:
        """
        Given an existing reviewer
        When removed
        Then succeeds (returns None)
        """
        # Given: SDK delete succeeds (returns None)
        client = Mock()
        client.git.delete_pull_request_reviewer.return_value = None

        # When: remove_reviewer is called
        from ado_workflows.lifecycle import remove_reviewer

        result = remove_reviewer(client, "Repo", pr_id=42, project="Proj", reviewer_id="guid-1")

        # Then: returns None
        assert result is None, f"Expected None, got {result!r}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When removing
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.delete_pull_request_reviewer.side_effect = Exception("Reviewer not found")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import remove_reviewer

        with pytest.raises(ActionableError):
            remove_reviewer(client, "Repo", pr_id=42, project="Proj", reviewer_id="bad-guid")


# ---------------------------------------------------------------------------
# TestListReviewers
# ---------------------------------------------------------------------------


class TestListReviewers:
    """
    REQUIREMENT: List all reviewers on a PR with vote details.

    WHO: Any tool needing reviewer information
    WHAT: (1) a PR with reviewers returns a list of ReviewerDetail with
              vote text
          (2) a PR with no reviewers returns an empty list
          (3) an SDK failure raises ActionableError
    WHY: Needed for display, filtering, and decision-making

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_reviewers
        Real:  ReviewerDetail mapping, VOTE_TEXT lookup
        Never: AdoClient
    """

    def test_pr_with_reviewers(self) -> None:
        """
        Given a PR with reviewers
        When listed
        Then returns list of ReviewerDetail with votes
        """
        # Given: SDK returns two reviewers
        r1 = _fake_reviewer_response(reviewer_id="g1", display_name="Alice", vote=10)
        r2 = _fake_reviewer_response(reviewer_id="g2", display_name="Bob", vote=-5)
        client = Mock()
        client.git.get_pull_request_reviewers.return_value = [r1, r2]

        # When: list_reviewers is called
        from ado_workflows.lifecycle import list_reviewers

        result = list_reviewers(client, "Repo", pr_id=42, project="Proj")

        # Then: returns two ReviewerDetail instances with vote text
        assert len(result) == 2, f"Expected 2 reviewers, got {len(result)}"
        assert result[0].vote_text == "Approved", (
            f"Expected vote_text='Approved', got {result[0].vote_text!r}"
        )
        assert result[1].vote_text == "Waiting for author", (
            f"Expected vote_text='Waiting for author', got {result[1].vote_text!r}"
        )

    def test_pr_with_no_reviewers(self) -> None:
        """
        Given a PR with no reviewers
        When listed
        Then returns empty list
        """
        # Given: SDK returns empty list
        client = Mock()
        client.git.get_pull_request_reviewers.return_value = []

        # When: list_reviewers is called
        from ado_workflows.lifecycle import list_reviewers

        result = list_reviewers(client, "Repo", pr_id=42, project="Proj")

        # Then: empty list
        assert result == [], f"Expected empty list, got {result}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When listed
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.get_pull_request_reviewers.side_effect = Exception("Auth failed")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import list_reviewers

        with pytest.raises(ActionableError):
            list_reviewers(client, "Repo", pr_id=42, project="Proj")


# ---------------------------------------------------------------------------
# TestAddLabel
# ---------------------------------------------------------------------------


class TestAddLabel:
    """
    REQUIREMENT: Add a label/tag to a PR.

    WHO: Agents categorizing PRs
    WHAT: (1) adding a label by name returns LabelDetail with the name
              and a generated ID
          (2) an SDK failure raises ActionableError
    WHY: PR tagging for filtering and reporting

    MOCK BOUNDARY:
        Mock:  client.git.create_pull_request_label
        Real:  model construction
        Never: AdoClient
    """

    def test_add_label_by_name(self) -> None:
        """
        Given a label name
        When added
        Then returns LabelDetail with name and generated ID
        """
        # Given: SDK returns a label with generated ID
        sdk_label = _fake_label_response(label_id="new-id", name="priority")
        client = Mock()
        client.git.create_pull_request_label.return_value = sdk_label

        # When: add_label is called
        from ado_workflows.lifecycle import add_label

        result = add_label(client, "Repo", pr_id=42, project="Proj", name="priority")

        # Then: result is LabelDetail with correct fields
        assert isinstance(result, LabelDetail), (
            f"Expected LabelDetail, got {type(result).__name__}"
        )
        assert result.name == "priority", f"Expected name='priority', got {result.name!r}"
        assert result.id == "new-id", f"Expected id='new-id', got {result.id!r}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When adding
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.create_pull_request_label.side_effect = Exception("Auth failed")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import add_label

        with pytest.raises(ActionableError):
            add_label(client, "Repo", pr_id=42, project="Proj", name="bad")


# ---------------------------------------------------------------------------
# TestRemoveLabel
# ---------------------------------------------------------------------------


class TestRemoveLabel:
    """
    REQUIREMENT: Remove a label from a PR.

    WHO: Agents managing PR categorization
    WHAT: (1) removing an existing label succeeds and returns None
          (2) an SDK failure raises ActionableError
    WHY: Cleanup of incorrect labels

    MOCK BOUNDARY:
        Mock:  client.git.delete_pull_request_labels
        Real:  nothing beyond delegation
        Never: AdoClient
    """

    def test_remove_existing_label(self) -> None:
        """
        Given an existing label
        When removed
        Then succeeds (returns None)
        """
        # Given: SDK delete succeeds
        client = Mock()
        client.git.delete_pull_request_labels.return_value = None

        # When: remove_label is called
        from ado_workflows.lifecycle import remove_label

        result = remove_label(client, "Repo", pr_id=42, project="Proj", label_name="old-tag")

        # Then: returns None
        assert result is None, f"Expected None, got {result!r}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When removing
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.delete_pull_request_labels.side_effect = Exception("Not found")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import remove_label

        with pytest.raises(ActionableError):
            remove_label(client, "Repo", pr_id=42, project="Proj", label_name="gone")


# ---------------------------------------------------------------------------
# TestListLabels
# ---------------------------------------------------------------------------


class TestListLabels:
    """
    REQUIREMENT: List all labels on a PR.

    WHO: Any tool needing label information
    WHAT: (1) a PR with labels returns a list of LabelDetail
          (2) a PR with no labels returns an empty list
          (3) an SDK failure raises ActionableError
    WHY: Display and filtering

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_labels
        Real:  LabelDetail mapping
        Never: AdoClient
    """

    def test_pr_with_labels(self) -> None:
        """
        Given a PR with labels
        When listed
        Then returns list of LabelDetail
        """
        # Given: SDK returns two labels
        l1 = _fake_label_response(label_id="id-1", name="bug")
        l2 = _fake_label_response(label_id="id-2", name="urgent")
        client = Mock()
        client.git.get_pull_request_labels.return_value = [l1, l2]

        # When: list_labels is called
        from ado_workflows.lifecycle import list_labels

        result = list_labels(client, "Repo", pr_id=42, project="Proj")

        # Then: returns two LabelDetail instances
        assert len(result) == 2, f"Expected 2 labels, got {len(result)}"
        assert result[0].name == "bug", f"Expected name='bug', got {result[0].name!r}"
        assert result[1].name == "urgent", f"Expected name='urgent', got {result[1].name!r}"

    def test_pr_with_no_labels(self) -> None:
        """
        Given a PR with no labels
        When listed
        Then returns empty list
        """
        # Given: SDK returns empty list
        client = Mock()
        client.git.get_pull_request_labels.return_value = []

        # When: list_labels is called
        from ado_workflows.lifecycle import list_labels

        result = list_labels(client, "Repo", pr_id=42, project="Proj")

        # Then: empty list
        assert result == [], f"Expected empty list, got {result}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When listed
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.get_pull_request_labels.side_effect = Exception("Auth failed")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import list_labels

        with pytest.raises(ActionableError):
            list_labels(client, "Repo", pr_id=42, project="Proj")


# ---------------------------------------------------------------------------
# TestGetPRWorkItemRefs
# ---------------------------------------------------------------------------


class TestGetPRWorkItemRefs:
    """
    REQUIREMENT: List work items linked to a PR.

    WHO: Agents cross-referencing PRs and work items (WSR generation, FR3)
    WHAT: (1) a PR with linked work items returns a list of WorkItemRef
              with IDs and URLs
          (2) a PR with no work items returns an empty list
          (3) an SDK failure raises ActionableError
    WHY: Read-only access to PR-to-work-item relationships

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_work_item_refs
        Real:  WorkItemRef mapping
        Never: AdoClient
    """

    def test_pr_with_linked_work_items(self) -> None:
        """
        Given a PR with linked work items
        When queried
        Then returns list of WorkItemRef with IDs
        """
        # Given: SDK returns two work item refs
        w1 = _fake_work_item_ref_response(wi_id="100", url="https://a/100")
        w2 = _fake_work_item_ref_response(wi_id="200", url="https://a/200")
        client = Mock()
        client.git.get_pull_request_work_item_refs.return_value = [w1, w2]

        # When: get_pr_work_item_refs is called
        from ado_workflows.lifecycle import get_pr_work_item_refs

        result = get_pr_work_item_refs(client, "Repo", pr_id=42, project="Proj")

        # Then: returns two WorkItemRef instances
        assert len(result) == 2, f"Expected 2 work items, got {len(result)}"
        assert result[0].id == "100", f"Expected id='100', got {result[0].id!r}"
        assert result[1].id == "200", f"Expected id='200', got {result[1].id!r}"

    def test_pr_with_no_work_items(self) -> None:
        """
        Given a PR with no work items
        When queried
        Then returns empty list
        """
        # Given: SDK returns empty list
        client = Mock()
        client.git.get_pull_request_work_item_refs.return_value = []

        # When: get_pr_work_item_refs is called
        from ado_workflows.lifecycle import get_pr_work_item_refs

        result = get_pr_work_item_refs(client, "Repo", pr_id=42, project="Proj")

        # Then: empty list
        assert result == [], f"Expected empty list, got {result}"

    def test_sdk_failure_raises_actionable_error(self) -> None:
        """
        Given SDK failure
        When queried
        Then raises ActionableError
        """
        # Given: SDK raises
        client = Mock()
        client.git.get_pull_request_work_item_refs.side_effect = Exception("Auth failed")

        # When/Then: ActionableError is raised
        from ado_workflows.lifecycle import get_pr_work_item_refs

        with pytest.raises(ActionableError):
            get_pr_work_item_refs(client, "Repo", pr_id=42, project="Proj")

"""BDD tests for ado_workflows.models — domain types.

Covers:
- TestReviewerInfoConstruction: ReviewerInfo dataclass with reviewer identity and vote metadata
- TestPendingPRConstruction: PendingPR dataclass with PR metadata for review reminders

Public API surface (from src/ado_workflows/models.py):
    VOTE_TEXT: dict[int, str]
    ReviewerInfo(display_name, unique_name, vote, is_required, is_container)
    VoteStatus(name, email, vote, vote_text, vote_invalidated, invalidated_by_commit,
               is_container, voted_for_ids, reviewer_id)
    PendingPR(pr_id, title, author, creation_date, repository, organization, project,
              web_url, pending_reviewers, days_open, merge_status, has_conflicts,
              needs_approvals_count=0, valid_approvals_count=0)
"""

from __future__ import annotations

from datetime import datetime

from ado_workflows.models import PendingPR, ReviewerInfo


class TestReviewerInfoConstruction:
    """
    REQUIREMENT: ReviewerInfo dataclass holds reviewer identity and vote metadata.

    WHO: PendingPR.pending_reviewers field; Phase 6c reminder logic.
    WHAT: Five fields: display_name, unique_name, vote, is_required, is_container.
          Typed container replaces ad-hoc dicts for reviewer data in reminder workflows.
    WHY: Without a typed container, reviewer data would be passed as untyped dicts,
         making field access error-prone and undiscoverable.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  ReviewerInfo
        Never: N/A
    """

    def test_all_fields_accessible_with_correct_values(self) -> None:
        """
        Given field values for a required individual reviewer
        When ReviewerInfo is constructed
        Then all five fields are accessible with correct values
        """
        # Given: field values for a required individual reviewer
        info = ReviewerInfo(
            display_name="Alice Smith",
            unique_name="alice@contoso.com",
            vote=10,
            is_required=True,
            is_container=False,
        )

        # When / Then: all fields are accessible with correct values
        assert info.display_name == "Alice Smith", (
            f"Expected display_name 'Alice Smith', got '{info.display_name}'"
        )
        assert info.unique_name == "alice@contoso.com", (
            f"Expected unique_name 'alice@contoso.com', got '{info.unique_name}'"
        )
        assert info.vote == 10, (
            f"Expected vote 10, got {info.vote}"
        )
        assert info.is_required is True, (
            f"Expected is_required True, got {info.is_required}"
        )
        assert info.is_container is False, (
            f"Expected is_container False, got {info.is_container}"
        )

    def test_container_reviewer_has_is_container_true(self) -> None:
        """
        Given is_container=True
        When ReviewerInfo is constructed
        Then is_container is True
        """
        # Given: a team/container reviewer
        info = ReviewerInfo(
            display_name="Payments Team",
            unique_name="payments-team@contoso.com",
            vote=0,
            is_required=True,
            is_container=True,
        )

        # When / Then: is_container reflects the team reviewer status
        assert info.is_container is True, (
            f"Expected is_container True for team reviewer, got {info.is_container}"
        )


class TestPendingPRConstruction:
    """
    REQUIREMENT: PendingPR dataclass holds PR metadata for review reminders.

    WHO: Phase 6c send_pr_review_reminders().
    WHAT: 14 fields including nested list[ReviewerInfo] for pending_reviewers.
          needs_approvals_count and valid_approvals_count default to 0.
    WHY: Without a typed container, PR data would be passed as untyped dicts,
         making field access error-prone and the approval count defaults implicit.

    MOCK BOUNDARY:
        Mock:  nothing — dataclass construction
        Real:  PendingPR, ReviewerInfo
        Never: N/A
    """

    def test_all_required_fields_plus_reviewerinfo_list(self) -> None:
        """
        Given all required fields and a ReviewerInfo list
        When PendingPR is constructed
        Then all fields are accessible with correct values
        """
        # Given: a ReviewerInfo for the pending reviewers list
        reviewer = ReviewerInfo(
            display_name="Bob Jones",
            unique_name="bob@contoso.com",
            vote=0,
            is_required=True,
            is_container=False,
        )
        creation = datetime(2026, 3, 1, 12, 0, 0)

        # When: PendingPR is constructed with all required fields
        pr = PendingPR(
            pr_id=42,
            title="Add payment validation",
            author="Alice Smith",
            creation_date=creation,
            repository="PaymentsRepo",
            organization="ContosoOrg",
            project="Payments",
            web_url="https://dev.azure.com/ContosoOrg/Payments/_git/PaymentsRepo/pullrequest/42",
            pending_reviewers=[reviewer],
            days_open=3,
            merge_status="succeeded",
            has_conflicts=False,
        )

        # Then: all fields are accessible with correct values
        assert pr.pr_id == 42, f"Expected pr_id 42, got {pr.pr_id}"
        assert pr.title == "Add payment validation", (
            f"Expected title 'Add payment validation', got '{pr.title}'"
        )
        assert pr.author == "Alice Smith", (
            f"Expected author 'Alice Smith', got '{pr.author}'"
        )
        assert pr.creation_date == creation, (
            f"Expected creation_date {creation}, got {pr.creation_date}"
        )
        assert pr.repository == "PaymentsRepo", (
            f"Expected repository 'PaymentsRepo', got '{pr.repository}'"
        )
        assert pr.organization == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{pr.organization}'"
        )
        assert pr.project == "Payments", (
            f"Expected project 'Payments', got '{pr.project}'"
        )
        assert pr.web_url.endswith("/pullrequest/42"), (
            f"Expected web_url to end with '/pullrequest/42', got '{pr.web_url}'"
        )
        assert pr.pending_reviewers == [reviewer], (
            f"Expected pending_reviewers to contain one reviewer, "
            f"got {pr.pending_reviewers}"
        )
        assert pr.days_open == 3, f"Expected days_open 3, got {pr.days_open}"
        assert pr.merge_status == "succeeded", (
            f"Expected merge_status 'succeeded', got '{pr.merge_status}'"
        )
        assert pr.has_conflicts is False, (
            f"Expected has_conflicts False, got {pr.has_conflicts}"
        )

    def test_defaults_for_approval_counts(self) -> None:
        """
        Given only required fields
        When PendingPR is constructed
        Then needs_approvals_count=0 and valid_approvals_count=0
        """
        # Given: only required fields (no approval counts specified)
        pr = PendingPR(
            pr_id=99,
            title="Fix typo",
            author="Charlie Brown",
            creation_date=datetime(2026, 3, 4, 8, 0, 0),
            repository="DocsRepo",
            organization="ContosoOrg",
            project="Docs",
            web_url="https://dev.azure.com/ContosoOrg/Docs/_git/DocsRepo/pullrequest/99",
            pending_reviewers=[],
            days_open=1,
            merge_status="succeeded",
            has_conflicts=False,
        )

        # When / Then: defaults are applied
        assert pr.needs_approvals_count == 0, (
            f"Expected needs_approvals_count default 0, got {pr.needs_approvals_count}"
        )
        assert pr.valid_approvals_count == 0, (
            f"Expected valid_approvals_count default 0, got {pr.valid_approvals_count}"
        )

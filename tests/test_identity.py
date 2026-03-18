"""BDD tests for ado_workflows identity operations -- PR author and current user.

Covers:
- TestGetPrAuthor: extract PR creator's identity
- TestGetCurrentUser: identify the authenticated user

Public API surface:
    From src/ado_workflows/pr.py:
        get_pr_author(client: AdoClient, pr_id: int, project: str) -> UserIdentity
    From src/ado_workflows/auth.py:
        get_current_user(client: AdoClient) -> UserIdentity
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.auth import get_current_user
from ado_workflows.models import UserIdentity
from ado_workflows.pr import get_pr_author

# ---------------------------------------------------------------------------
# TestGetPrAuthor
# ---------------------------------------------------------------------------


class TestGetPrAuthor:
    """
    REQUIREMENT: Extract the PR creator's identity.

    WHO: Workflow logic that needs to know who created the PR (e.g., filtering
         self-authored feedback).
    WHAT: (1) a valid PR ID returns a UserIdentity with display_name, id (GUID), and unique_name
          (2) an invalid PR ID raises ActionableError with the original error context and potential corrective actions for the user
    WHY: Display names vary by context (e.g., 'Alice Smith' vs 'Alice Smith (CONTOSO)').
         The GUID is the only reliable identity comparator across ADO surfaces.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_by_id (SDK network call)
        Real:  get_pr_author field extraction
        Never: get_pr_author itself
    """

    def test_valid_pr_returns_creator_identity(self) -> None:
        """
        Given a valid PR ID,
        When get_pr_author is called,
        Then it returns a UserIdentity with display_name, id, and unique_name.
        """
        # Given: SDK returns a PR with a creator
        client = Mock()
        pr_mock = Mock()
        pr_mock.created_by.display_name = "Alice Smith (CONTOSO)"
        pr_mock.created_by.id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        pr_mock.created_by.unique_name = "alice@contoso.com"
        client.git.get_pull_request_by_id.return_value = pr_mock

        # When: get_pr_author is called
        result = get_pr_author(client, 15020627, "MyProject")

        # Then: returns a UserIdentity with all fields
        assert isinstance(result, UserIdentity), (
            f"Expected UserIdentity, got {type(result).__name__}"
        )
        assert result.display_name == "Alice Smith (CONTOSO)", (
            f"Expected display_name='Alice Smith (CONTOSO)', got {result.display_name!r}"
        )
        assert result.id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890", (
            f"Expected GUID id, got {result.id!r}"
        )
        assert result.unique_name == "alice@contoso.com", (
            f"Expected unique_name='alice@contoso.com', got {result.unique_name!r}"
        )

    def test_invalid_pr_raises_actionable_error_with_guidance(self) -> None:
        """
        Given an invalid PR ID,
        When get_pr_author is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: SDK raises for invalid PR
        client = Mock()
        client.git.get_pull_request_by_id.side_effect = Exception(
            "TF401180: The requested pull request was not found."
        )

        # When / Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            get_pr_author(client, 99999999, "MyProject")

        assert "not found" in str(exc_info.value).lower() or "TF401180" in str(exc_info.value), (
            f"Expected error about PR not found, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "not_found", (
            f"Expected error_type='not_found', got {exc_info.value.error_type!r}"
        )


# ---------------------------------------------------------------------------
# TestGetCurrentUser
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    """
    REQUIREMENT: Identify the authenticated user.

    WHO: Workflow logic that compares current user to PR author.
    WHAT: (1) valid credentials return a UserIdentity with display_name and id (GUID)
          (2) expired or invalid credentials raise ActionableError with the original error context and potential corrective actions for the user
    WHY: Display names differ across contexts ('Bob Jones' vs 'Bob Jones (CONTOSO)').
         The GUID is the only reliable way to answer 'is this my PR?'.

    MOCK BOUNDARY:
        Mock:  client.location.get_connection_data (SDK identity endpoint)
        Real:  get_current_user identity extraction logic
        Never: get_current_user itself
    """

    def test_valid_credentials_return_user_identity(self) -> None:
        """
        Given valid credentials,
        When get_current_user is called,
        Then it returns a UserIdentity with display_name and id (GUID).
        """
        # Given: connection data returns an authenticated user
        client = Mock()
        conn_data = Mock()
        conn_data.authenticated_user.provider_display_name = "Bob Jones"
        conn_data.authenticated_user.id = "f9e8d7c6-b5a4-3210-fedc-ba0987654321"
        # LocationClient accessed via client.location property
        client.location.get_connection_data.return_value = conn_data

        # When: get_current_user is called
        result = get_current_user(client)

        # Then: returns a UserIdentity with display_name and id
        assert isinstance(result, UserIdentity), (
            f"Expected UserIdentity, got {type(result).__name__}"
        )
        assert result.display_name == "Bob Jones", (
            f"Expected display_name='Bob Jones', got {result.display_name!r}"
        )
        assert result.id == "f9e8d7c6-b5a4-3210-fedc-ba0987654321", (
            f"Expected GUID id, got {result.id!r}"
        )

    def test_invalid_credentials_raise_actionable_error_with_guidance(self) -> None:
        """
        Given expired or invalid credentials,
        When get_current_user is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: SDK raises for invalid credentials
        client = Mock()
        client.location.get_connection_data.side_effect = Exception(
            "401 Unauthorized: token expired"
        )

        # When / Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            get_current_user(client)

        assert "401" in str(exc_info.value) or "unauthorized" in str(exc_info.value).lower(), (
            f"Expected auth error context, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "authentication", (
            f"Expected error_type='authentication', got {exc_info.value.error_type!r}"
        )

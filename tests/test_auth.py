"""BDD tests for ado_workflows.auth — ConnectionFactory and token bridge.

Covers:
    TestConnectionCreation — factory creates valid SDK connections
    TestConnectionCaching — per-org caching with token refresh
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

from ado_workflows.auth import (
    AZURE_DEVOPS_RESOURCE_ID,
    ConnectionFactory,
)

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_connection(**kw: Any) -> Mock:
    """Factory for mock Connection objects keyed by base_url."""
    return Mock(name=kw.get("base_url", "conn"))


def _fake_token(*, expires_in: float = 3600.0) -> SimpleNamespace:
    """Return a token-like object with .token and .expires_on."""
    return SimpleNamespace(token="fake-token-abc", expires_on=time.time() + expires_in)


def _make_credential(token: SimpleNamespace | None = None) -> Mock:
    """Return a mock credential whose get_token returns *token*."""
    cred = Mock()
    cred.get_token.return_value = token or _fake_token()
    return cred


# ---------------------------------------------------------------------------
# TestConnectionCreation
# ---------------------------------------------------------------------------


class TestConnectionCreation:
    """
    REQUIREMENT: ConnectionFactory creates Azure DevOps SDK connections
    from organization URLs.

    WHO: Any workflow layer that needs authenticated access to the
         Azure DevOps API
    WHAT: A factory initialized with a credential acquires a scoped token
          and creates a Connection with the correct base URL and auth;
          a factory with no explicit credential defaults to
          DefaultAzureCredential
    WHY: The SDK requires a msrest-compatible credential, but modern auth
         uses azure-identity — the factory bridges this gap

    MOCK BOUNDARY:
        Mock:  credential.get_token (token I/O), Connection constructor,
               BasicTokenAuthentication constructor, DefaultAzureCredential
        Real:  ConnectionFactory logic, _normalize_org_url
        Never: Make real network calls or obtain real tokens
    """

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_factory_creates_connection_with_token(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given a credential that returns a valid token
        When get_connection is called with an org URL
        Then a Connection is created with BasicTokenAuthentication wrapping the token
        """
        # Given: a credential returning a known token
        token = _fake_token()
        credential = _make_credential(token)
        factory = ConnectionFactory(credential=credential)

        # When: get_connection is called
        factory.get_connection("https://dev.azure.com/ExampleOrg")

        # Then: token was acquired with the correct scope
        credential.get_token.assert_called_once_with(
            f"{AZURE_DEVOPS_RESOURCE_ID}/.default"
        )
        # Then: BasicTokenAuthentication received the token string
        mock_bta.assert_called_once_with({"access_token": "fake-token-abc"})
        # Then: Connection received the org URL and credentials
        mock_conn_cls.assert_called_once_with(
            base_url="https://dev.azure.com/ExampleOrg",
            creds=mock_bta.return_value,
        )

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_factory_uses_provided_credential(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given an explicitly provided credential
        When get_connection is called
        Then the provided credential is used for token acquisition
        """
        # Given: a custom credential
        custom_cred = _make_credential()
        factory = ConnectionFactory(credential=custom_cred)

        # When: connection requested
        factory.get_connection("https://dev.azure.com/MyOrg")

        # Then: the custom credential was used
        custom_cred.get_token.assert_called_once()

    @patch("ado_workflows.auth.DefaultAzureCredential")
    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_factory_defaults_to_default_azure_credential(
        self, mock_bta: Mock, mock_conn_cls: Mock, mock_dac: Mock
    ) -> None:
        """
        When a ConnectionFactory is created without an explicit credential
        Then DefaultAzureCredential is instantiated and used
        """
        # Given: DefaultAzureCredential mock returns a token
        mock_dac.return_value.get_token.return_value = _fake_token()

        # When: factory created with no credential, then connection requested
        factory = ConnectionFactory()
        factory.get_connection("https://dev.azure.com/AutoOrg")

        # Then: DefaultAzureCredential was instantiated
        mock_dac.assert_called_once()
        # Then: its get_token was called
        mock_dac.return_value.get_token.assert_called_once()

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_connection_base_url_matches_org_url(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given a specific org URL
        When get_connection is called
        Then the Connection's base_url matches the normalized org URL
        """
        # Given: an org URL
        credential = _make_credential()
        factory = ConnectionFactory(credential=credential)

        # When: connection requested
        factory.get_connection("https://dev.azure.com/SpecificOrg")

        # Then: Connection created with matching base_url
        mock_conn_cls.assert_called_once()
        call_kwargs = mock_conn_cls.call_args
        assert call_kwargs[1]["base_url"] == "https://dev.azure.com/SpecificOrg"

    def test_resource_id_is_azure_devops_guid(self) -> None:
        """
        When the AZURE_DEVOPS_RESOURCE_ID constant is accessed
        Then it matches the well-known Azure DevOps resource identifier
        """
        # When/Then: the constant has the expected value
        assert AZURE_DEVOPS_RESOURCE_ID == "499b84ac-1321-427f-aa17-267ca6975798"


# ---------------------------------------------------------------------------
# TestConnectionCaching
# ---------------------------------------------------------------------------


class TestConnectionCaching:
    """
    REQUIREMENT: ConnectionFactory caches connections per organization
    and refreshes tokens on expiry.

    WHO: Callers making repeated requests to the same organization
    WHAT: Repeated calls with the same org URL return the cached connection;
          expired or near-expired tokens trigger fresh token acquisition;
          different org URLs maintain separate cached connections;
          clear_cache removes all cached connections;
          trailing-slash variations of the same org URL share a cache entry
    WHY: Token acquisition involves network I/O — caching avoids unnecessary
         round-trips while token refresh prevents auth failures

    MOCK BOUNDARY:
        Mock:  credential.get_token (token I/O), Connection constructor,
               BasicTokenAuthentication constructor
        Real:  ConnectionFactory caching logic, time comparison
        Never: Use real time.time() — freeze via monkeypatch
    """

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_same_org_returns_cached_connection(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given a connection already created for an org
        When get_connection is called again with the same org URL
        Then the same connection object is returned without re-acquiring a token
        """
        # Given: a factory with a valid cached connection
        credential = _make_credential()
        factory = ConnectionFactory(credential=credential)
        first = factory.get_connection("https://dev.azure.com/CachedOrg")

        # When: same org requested again
        second = factory.get_connection("https://dev.azure.com/CachedOrg")

        # Then: same object returned
        assert first is second
        # Then: get_token called only once (not re-acquired)
        credential.get_token.assert_called_once()

    @patch("ado_workflows.auth.Connection", side_effect=_fake_connection)
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_different_orgs_have_separate_connections(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given connections to two different organizations
        When get_connection is called for each
        Then separate connection objects are maintained
        """
        # Given: a factory
        credential = _make_credential()
        factory = ConnectionFactory(credential=credential)

        # When: two different orgs requested
        conn_a = factory.get_connection("https://dev.azure.com/OrgA")
        conn_b = factory.get_connection("https://dev.azure.com/OrgB")

        # Then: different connection objects
        assert conn_a is not conn_b
        # Then: get_token called twice (once per org)
        assert credential.get_token.call_count == 2

    @patch("ado_workflows.auth.Connection", side_effect=_fake_connection)
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_expired_token_triggers_refresh(
        self, mock_bta: Mock, mock_conn_cls: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Given a cached connection whose token has expired
        When get_connection is called
        Then a new token is acquired and a fresh connection is created
        """
        # Given: a token that expired 10 seconds ago
        expired_token = SimpleNamespace(token="old-token", expires_on=1000.0)
        fresh_token = SimpleNamespace(token="new-token", expires_on=5000.0)
        credential = Mock()
        credential.get_token.side_effect = [expired_token, fresh_token]
        factory = ConnectionFactory(credential=credential)

        # Freeze time at 1010 (after expiry)
        monkeypatch.setattr(time, "time", lambda: 1010.0)

        # When: first call creates connection, second should refresh
        first = factory.get_connection("https://dev.azure.com/ExpiredOrg")
        second = factory.get_connection("https://dev.azure.com/ExpiredOrg")

        # Then: token was acquired twice (initial + refresh)
        assert credential.get_token.call_count == 2
        # Then: a new connection was created
        assert first is not second

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_near_expiry_triggers_refresh(
        self, mock_bta: Mock, mock_conn_cls: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Given a cached connection whose token expires within the refresh buffer
        When get_connection is called
        Then a new token is acquired proactively
        """
        # Given: token expires at 1300, buffer is 300s, time is 1050 (within buffer)
        near_expiry_token = SimpleNamespace(token="expiring-soon", expires_on=1300.0)
        fresh_token = SimpleNamespace(token="refreshed", expires_on=5000.0)
        credential = Mock()
        credential.get_token.side_effect = [near_expiry_token, fresh_token]
        factory = ConnectionFactory(credential=credential)

        monkeypatch.setattr(time, "time", lambda: 1050.0)

        # First call — creates with near-expiry token
        factory.get_connection("https://dev.azure.com/NearExpiryOrg")

        # When: second call — time is within buffer window (1050 >= 1300 - 300)
        factory.get_connection("https://dev.azure.com/NearExpiryOrg")

        # Then: token was refreshed
        assert credential.get_token.call_count == 2

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_valid_token_skips_refresh(
        self, mock_bta: Mock, mock_conn_cls: Mock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Given a cached connection whose token is well within its validity period
        When get_connection is called
        Then no new token is acquired
        """
        # Given: token expires at 5000, time is 1000 (well within validity)
        valid_token = SimpleNamespace(token="valid", expires_on=5000.0)
        credential = _make_credential(valid_token)
        factory = ConnectionFactory(credential=credential)

        monkeypatch.setattr(time, "time", lambda: 1000.0)

        # When: two calls to the same org
        factory.get_connection("https://dev.azure.com/ValidOrg")
        factory.get_connection("https://dev.azure.com/ValidOrg")

        # Then: get_token called only once
        credential.get_token.assert_called_once()

    @patch("ado_workflows.auth.Connection", side_effect=_fake_connection)
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_clear_cache_removes_all_connections(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given cached connections for multiple orgs
        When clear_cache is called
        Then subsequent get_connection calls create fresh connections
        """
        # Given: two cached connections
        credential = _make_credential()
        factory = ConnectionFactory(credential=credential)
        first_a = factory.get_connection("https://dev.azure.com/OrgA")
        factory.get_connection("https://dev.azure.com/OrgB")
        assert credential.get_token.call_count == 2

        # When: cache cleared
        factory.clear_cache()

        # Then: new connections created on next access
        second_a = factory.get_connection("https://dev.azure.com/OrgA")
        assert second_a is not first_a
        assert credential.get_token.call_count == 3

    @patch("ado_workflows.auth.Connection")
    @patch("ado_workflows.auth.BasicTokenAuthentication")
    def test_trailing_slash_and_no_slash_share_cache(
        self, mock_bta: Mock, mock_conn_cls: Mock
    ) -> None:
        """
        Given connections requested with and without a trailing slash
        When both are requested from the same factory
        Then the same cached connection is returned
        """
        # Given: a factory
        credential = _make_credential()
        factory = ConnectionFactory(credential=credential)

        # When: same org requested with and without trailing slash
        conn_with = factory.get_connection("https://dev.azure.com/SlashOrg/")
        conn_without = factory.get_connection("https://dev.azure.com/SlashOrg")

        # Then: same cached connection
        assert conn_with is conn_without, (
            f"Expected same cached connection, got {conn_with!r} vs {conn_without!r}"
        )
        credential.get_token.assert_called_once()

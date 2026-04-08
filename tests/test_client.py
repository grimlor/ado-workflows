"""
BDD tests for ado_workflows.client — AdoClient typed SDK accessors.

Covers:
    TestAdoClientAccess — typed property access to Git, Core, Work Item, Policy clients
    TestAdoClientCaching — lazy initialization and caching behavior
"""

from __future__ import annotations

from unittest.mock import Mock

from ado_workflows.client import AdoClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sentinel objects — one per SDK client type, used to verify correct routing
_GIT_SENTINEL = Mock(name="git-client")
_CORE_SENTINEL = Mock(name="core-client")
_WIT_SENTINEL = Mock(name="wit-client")
_POLICY_SENTINEL = Mock(name="policy-client")
_LOCATION_SENTINEL = Mock(name="location-client")

_CLIENT_MAP: dict[str, Mock] = {
    "azure.devops.v7_1.git.git_client.GitClient": _GIT_SENTINEL,
    "azure.devops.v7_1.core.core_client.CoreClient": _CORE_SENTINEL,
    "azure.devops.v7_1.work_item_tracking.work_item_tracking_client.WorkItemTrackingClient": _WIT_SENTINEL,
    "azure.devops.v7_1.policy.policy_client.PolicyClient": _POLICY_SENTINEL,
    "azure.devops.v7_1.location.location_client.LocationClient": _LOCATION_SENTINEL,
}


def _fake_get_client(path: str) -> Mock:
    """Side-effect for mock Connection.get_client() — returns a known sentinel per path."""
    return _CLIENT_MAP.get(path, Mock(name=f"unknown:{path}"))


def _mock_connection() -> Mock:
    """Return a mock Connection with a get_client method."""
    connection = Mock()
    connection.get_client.side_effect = _fake_get_client
    return connection


# ---------------------------------------------------------------------------
# TestAdoClientAccess
# ---------------------------------------------------------------------------


class TestAdoClientAccess:
    """
    REQUIREMENT: AdoClient provides typed access to Azure DevOps SDK clients.

    WHO: Workflow layers that need Git, Core, Work Item Tracking, or Policy operations
    WHAT: (1) the git property returns the Git client from the connection
          (2) the core property returns the Core client
          (3) the work_items property returns the Work Item Tracking client
          (4) the policy property returns the Policy client
          (5) the location property returns the Location client
    WHY: Direct SDK client construction via connection.get_client(string)
         is untyped and error-prone — the wrapper provides a clean,
         discoverable API surface

    MOCK BOUNDARY:
        Mock:  Connection and its get_client method
        Real:  AdoClient property access
        Never: Construct real SDK clients or make network calls
    """

    def test_git_property_returns_git_client(self) -> None:
        """
        When the git property is accessed
        Then get_client is called with the GitClient class path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: git property accessed
        git = client.git

        # Then: returns the Git client sentinel
        assert git is _GIT_SENTINEL, f"Expected git property to return the Git client, got {git!r}"

    def test_core_property_returns_core_client(self) -> None:
        """
        When the core property is accessed
        Then get_client is called with the CoreClient class path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: core property accessed
        core = client.core

        # Then: returns the Core client sentinel
        assert core is _CORE_SENTINEL, (
            f"Expected core property to return the Core client, got {core!r}"
        )

    def test_work_items_property_returns_wit_client(self) -> None:
        """
        When the work_items property is accessed
        Then get_client is called with the WorkItemTrackingClient class path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: work_items property accessed
        wit = client.work_items

        # Then: returns the Work Item Tracking client sentinel
        assert wit is _WIT_SENTINEL, (
            f"Expected work_items property to return the WIT client, got {wit!r}"
        )

    def test_policy_property_returns_policy_client(self) -> None:
        """
        When the policy property is accessed
        Then get_client is called with the PolicyClient class path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: policy property accessed
        policy = client.policy

        # Then: returns the Policy client sentinel
        assert policy is _POLICY_SENTINEL, (
            f"Expected policy property to return the Policy client, got {policy!r}"
        )

    def test_location_property_returns_location_client(self) -> None:
        """
        When the location property is accessed
        Then get_client is called with the LocationClient class path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: location property accessed
        location = client.location

        # Then: returns the Location client sentinel
        assert location is _LOCATION_SENTINEL, (
            f"Expected location property to return the Location client, got {location!r}"
        )


# ---------------------------------------------------------------------------
# TestAdoClientCaching
# ---------------------------------------------------------------------------


class TestAdoClientCaching:
    """
    REQUIREMENT: SDK clients are lazily initialized and cached after first access.

    WHO: Callers accessing the same client property multiple times
    WHAT: (1) git property returns the same instance on subsequent accesses
          (2) core property returns the same instance on subsequent accesses
          (3) work_items property returns the same instance on subsequent accesses
          (4) policy property returns the same instance on subsequent accesses
          (5) different client properties (git, core, work_items, policy) return
              distinct objects
    WHY: get_client may involve resource area discovery (network I/O) —
         caching avoids repeated overhead

    MOCK BOUNDARY:
        Mock:  Connection and its get_client method
        Real:  AdoClient caching via cached_property
        Never: Construct real SDK clients
    """

    def test_git_client_is_cached_after_first_access(self) -> None:
        """
        Given the git property has been accessed once
        When it is accessed again
        Then the same object is returned without another get_client call
        """
        # Given: access git once
        connection = _mock_connection()
        client = AdoClient(connection)
        first = client.git

        # When: access again
        second = client.git

        # Then: same object, cached
        assert first is second

    def test_core_client_is_cached_after_first_access(self) -> None:
        """
        Given the core property has been accessed once
        When it is accessed again
        Then the same object is returned
        """
        # Given: access core once
        connection = _mock_connection()
        client = AdoClient(connection)
        first = client.core

        # When: access again
        second = client.core

        # Then: same object
        assert first is second

    def test_work_items_client_is_cached_after_first_access(self) -> None:
        """
        Given the work_items property has been accessed once
        When it is accessed again
        Then the same object is returned
        """
        # Given: access work_items once
        connection = _mock_connection()
        client = AdoClient(connection)
        first = client.work_items

        # When: access again
        second = client.work_items

        # Then: same object
        assert first is second

    def test_policy_client_is_cached_after_first_access(self) -> None:
        """
        Given the policy property has been accessed once
        When it is accessed again
        Then the same object is returned
        """
        # Given: access policy once
        connection = _mock_connection()
        client = AdoClient(connection)
        first = client.policy

        # When: access again
        second = client.policy

        # Then: same object
        assert first is second

    def test_different_clients_are_independent(self) -> None:
        """
        When git, core, work_items, and policy are all accessed
        Then each returns a distinct object
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: all four clients accessed
        git = client.git
        core = client.core
        work_items = client.work_items
        policy = client.policy

        # Then: each is a distinct object
        clients = [git, core, work_items, policy]
        assert len(set(id(c) for c in clients)) == 4, (
            "Expected 4 distinct client objects, got duplicates"
        )

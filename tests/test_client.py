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


def _fake_get_client(path: str) -> Mock:
    """Side-effect for mock Connection.get_client()."""
    return Mock(name=f"client:{path}")


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
          (6) each property requests the correct SDK client class path
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

        # Then: correct client requested
        connection.get_client.assert_any_call("azure.devops.v7_1.git.git_client.GitClient")
        assert git is not None

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

        # Then: correct client requested
        connection.get_client.assert_any_call("azure.devops.v7_1.core.core_client.CoreClient")
        assert core is not None

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

        # Then: correct client requested
        connection.get_client.assert_any_call(
            "azure.devops.v7_1.work_item_tracking.work_item_tracking_client.WorkItemTrackingClient"
        )
        assert wit is not None

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

        # Then: correct client requested
        connection.get_client.assert_any_call(
            "azure.devops.v7_1.policy.policy_client.PolicyClient"
        )
        assert policy is not None

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

        # Then: correct client requested
        connection.get_client.assert_any_call(
            "azure.devops.v7_1.location.location_client.LocationClient"
        )
        assert location is not None


# ---------------------------------------------------------------------------
# TestAdoClientCaching
# ---------------------------------------------------------------------------


class TestAdoClientCaching:
    """
    REQUIREMENT: SDK clients are lazily initialized and cached after first access.

    WHO: Callers accessing the same client property multiple times
    WHAT: (1) the first access to a client property calls get_client on the connection
          (2) subsequent accesses return the cached instance without calling get_client again
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

        # Then: same object, get_client called only once for git
        assert first is second
        # get_client called exactly once (for the git path)
        assert connection.get_client.call_count == 1

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
        assert connection.get_client.call_count == 1

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
        assert connection.get_client.call_count == 1

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
        assert connection.get_client.call_count == 1

    def test_different_clients_are_independent(self) -> None:
        """
        When git, core, work_items, and policy are all accessed
        Then each triggers a separate get_client call with its own path
        """
        # Given: a mock connection
        connection = _mock_connection()
        client = AdoClient(connection)

        # When: all four clients accessed
        _ = client.git
        _ = client.core
        _ = client.work_items
        _ = client.policy

        # Then: four separate get_client calls
        assert connection.get_client.call_count == 4, (
            f"Expected 4 get_client calls, got {connection.get_client.call_count}"
        )
        paths = [call.args[0] for call in connection.get_client.call_args_list]
        assert "azure.devops.v7_1.git.git_client.GitClient" in paths
        assert "azure.devops.v7_1.core.core_client.CoreClient" in paths
        assert (
            "azure.devops.v7_1.work_item_tracking.work_item_tracking_client.WorkItemTrackingClient"
        ) in paths
        assert "azure.devops.v7_1.policy.policy_client.PolicyClient" in paths

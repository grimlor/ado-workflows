"""Azure DevOps authentication — DefaultAzureCredential → Connection bridge.

Bridges azure-identity's ``DefaultAzureCredential`` into the azure-devops SDK's
msrest-based auth layer.  ``ConnectionFactory`` handles per-org connection caching
and proactive token refresh before expiry.

Typical usage::

    factory = ConnectionFactory()  # uses DefaultAzureCredential
    connection = factory.get_connection("https://dev.azure.com/MyOrg")
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from actionable_errors import ActionableError
from azure.devops.connection import Connection
from azure.identity import DefaultAzureCredential
from msrest.authentication import BasicTokenAuthentication

from ado_workflows.models import UserIdentity

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

    from ado_workflows.client import AdoClient

AZURE_DEVOPS_RESOURCE_ID: str = "499b84ac-1321-427f-aa17-267ca6975798"
"""Well-known Azure DevOps resource identifier for token acquisition."""

_SCOPE: str = f"{AZURE_DEVOPS_RESOURCE_ID}/.default"
_TOKEN_REFRESH_BUFFER_SECONDS: int = 300  # refresh 5 min before expiry


class ConnectionFactory:
    """Creates and caches Azure DevOps SDK connections per organization URL.

    Bridges ``azure-identity``'s :class:`DefaultAzureCredential` into the
    azure-devops SDK's msrest-based auth layer, handling token refresh and
    per-org connection caching.

    Parameters
    ----------
    credential:
        Optional :class:`TokenCredential` for dependency injection / testing.
        Defaults to :class:`DefaultAzureCredential` when *None*.
    """

    def __init__(self, credential: TokenCredential | None = None) -> None:
        self._credential: TokenCredential = credential or DefaultAzureCredential()
        self._connections: dict[str, Connection] = {}
        self._token_expiry: dict[str, float] = {}

    def get_connection(self, org_url: str) -> Connection:
        """Get or create a cached connection for *org_url*.

        Returns a cached :class:`Connection` if the token is still valid
        (more than 5 minutes until expiry).  Otherwise acquires a fresh
        token and creates a new connection.
        """
        key = _normalize_org_url(org_url)
        now = time.time()

        if key in self._connections:
            expiry = self._token_expiry.get(key, 0.0)
            if now < expiry - _TOKEN_REFRESH_BUFFER_SECONDS:
                return self._connections[key]

        token = self._credential.get_token(_SCOPE)
        creds = BasicTokenAuthentication({"access_token": token.token})
        connection = Connection(base_url=key, creds=creds)

        self._connections[key] = connection
        self._token_expiry[key] = token.expires_on

        return connection

    def clear_cache(self) -> None:
        """Remove all cached connections and token expiry records."""
        self._connections.clear()
        self._token_expiry.clear()


def _normalize_org_url(org_url: str) -> str:
    """Normalize *org_url* to a consistent form for cache-key usage."""
    return org_url.rstrip("/")


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def get_current_user(client: AdoClient) -> UserIdentity:
    """Return the identity of the authenticated user.

    Uses :class:`LocationClient`'s ``get_connection_data()`` to resolve
    the authenticated user from the active connection.

    Args:
        client: An authenticated :class:`~client.AdoClient`.

    Returns:
        :class:`~models.UserIdentity` with display name and GUID.
        ``unique_name`` is ``None`` (not available on auth identity).

    Raises:
        ActionableError: When credentials are invalid or the call fails.
    """
    try:
        conn_data = client.location.get_connection_data()
    except Exception as exc:
        raise ActionableError.authentication(
            service="AzureDevOps",
            raw_error=str(exc),
            suggestion=(
                "Check your Azure DevOps credentials. "
                "Try 'az login' to refresh your authentication token."
            ),
        ) from exc

    user = conn_data.authenticated_user
    return UserIdentity(
        display_name=user.provider_display_name,
        id=user.id,
    )

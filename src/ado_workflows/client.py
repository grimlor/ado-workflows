"""Typed accessors for Azure DevOps SDK clients.

Wraps :meth:`Connection.get_client` with lazy, cached properties that
provide type-safe access to the Git, Core, and Work Item Tracking clients.

Typical usage::

    from ado_workflows.auth import ConnectionFactory
    from ado_workflows.client import AdoClient

    factory = ConnectionFactory()
    connection = factory.get_connection("https://dev.azure.com/MyOrg")
    client = AdoClient(connection)

    repos = client.git.get_repositories("MyProject")
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from azure.devops.connection import Connection
    from azure.devops.v7_1.core.core_client import CoreClient
    from azure.devops.v7_1.git.git_client import GitClient
    from azure.devops.v7_1.work_item_tracking.work_item_tracking_client import (
        WorkItemTrackingClient,
    )

_GIT_CLIENT_PATH = "azure.devops.v7_1.git.git_client.GitClient"
_CORE_CLIENT_PATH = "azure.devops.v7_1.core.core_client.CoreClient"
_WIT_CLIENT_PATH = (
    "azure.devops.v7_1.work_item_tracking"
    ".work_item_tracking_client.WorkItemTrackingClient"
)


class AdoClient:
    """Typed, lazy accessor for Azure DevOps SDK clients.

    Each client property is initialized on first access and cached for
    the lifetime of this instance.  If the underlying connection's token
    expires, create a new ``AdoClient`` from a fresh connection.

    Parameters
    ----------
    connection:
        An authenticated :class:`Connection` (typically from
        :meth:`ConnectionFactory.get_connection`).
    """

    def __init__(self, connection: Connection) -> None:
        self._connection: Any = connection

    @cached_property
    def git(self) -> GitClient:
        """Git operations: repositories, pull requests, threads, commits."""
        return self._connection.get_client(_GIT_CLIENT_PATH)

    @cached_property
    def core(self) -> CoreClient:
        """Core operations: projects, teams."""
        return self._connection.get_client(_CORE_CLIENT_PATH)

    @cached_property
    def work_items(self) -> WorkItemTrackingClient:
        """Work item operations: queries, work items."""
        return self._connection.get_client(_WIT_CLIENT_PATH)

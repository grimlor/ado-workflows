"""ado-workflows: Azure DevOps workflow automation library.

Three-layer API for Azure DevOps operations:
- Layer 1 — Primitives: pure functions (URL parsing, git inspection, date parsing)
- Layer 2 — Context: stateful caching (RepositoryContext, thread-safe)
- Layer 3 — PR Context: composed workflows (AzureDevOpsPRContext)
"""

from __future__ import annotations

from ado_workflows.auth import AZURE_DEVOPS_RESOURCE_ID, ConnectionFactory
from ado_workflows.client import AdoClient
from ado_workflows.context import (
    RepositoryContext,
    clear_repository_context,
    get_context_status,
    get_repository_context,
    set_repository_context,
)
from ado_workflows.discovery import (
    discover_repositories,
    infer_target_repository,
    inspect_git_repository,
)
from ado_workflows.parsing import parse_ado_date, parse_ado_url

__all__: list[str] = [
    "AZURE_DEVOPS_RESOURCE_ID",
    "AdoClient",
    "ConnectionFactory",
    "RepositoryContext",
    "clear_repository_context",
    "discover_repositories",
    "get_context_status",
    "get_repository_context",
    "infer_target_repository",
    "inspect_git_repository",
    "parse_ado_date",
    "parse_ado_url",
    "set_repository_context",
]

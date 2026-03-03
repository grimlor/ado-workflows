"""ado-workflows: Azure DevOps workflow automation library.

Three-layer API for Azure DevOps operations:
- Layer 1 — Primitives: pure functions (URL parsing, git inspection, date parsing)
- Layer 2 — Context: stateful caching (RepositoryContext, thread-safe)
- Layer 3 — PR Context: composed workflows (AzureDevOpsPRContext)
"""

from __future__ import annotations

from ado_workflows.discovery import (
    discover_repositories,
    infer_target_repository,
    inspect_git_repository,
)
from ado_workflows.parsing import parse_ado_date, parse_ado_url

__all__: list[str] = [
    "discover_repositories",
    "infer_target_repository",
    "inspect_git_repository",
    "parse_ado_date",
    "parse_ado_url",
]

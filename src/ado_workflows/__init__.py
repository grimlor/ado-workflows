"""ado-workflows: Azure DevOps workflow automation library.

Three-layer API for Azure DevOps operations:
- Layer 1 — Primitives: pure functions (URL parsing, git inspection, date parsing)
- Layer 2 — Context: stateful caching (RepositoryContext, thread-safe)
- Layer 3 — PR Context: composed workflows (AzureDevOpsPRContext)
"""

from __future__ import annotations

__all__: list[str] = []

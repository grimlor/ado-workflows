# Architecture

> Three-layer API design and module responsibilities for contributors.

---

## Design Principle: Layered Abstraction

`ado-workflows` provides Azure DevOps automation through three progressively
richer layers. Each layer builds on the one below, and consumers choose their
entry point based on how much context they need.

---

## Layer Overview

| Layer | Modules | State | SDK Dependency | Purpose |
|-------|---------|-------|----------------|---------|
| **1 — Primitives** | `parsing`, `discovery` | None | None | Pure functions: URL parsing, git inspection, date parsing |
| **2 — Context** | `context` | Thread-safe | None | Repository context caching with `RepositoryContext` |
| **3 — PR Operations** | `pr`, `review`, `comments`, `votes`, `lifecycle` | Composes L1+L2 | `azure-devops` | Composed PR workflows via `AzureDevOpsPRContext` |

Supporting modules: `auth` (credential bridge), `client` (SDK wrappers), `models` (domain dataclasses).

---

## Module Map

```
ado_workflows/
├── parsing.py       Layer 1: parse_ado_url, parse_ado_date (stdlib only)
├── discovery.py     Layer 1: inspect_git_repository, discover_repositories
├── auth.py          ConnectionFactory — DefaultAzureCredential → SDK bridge
├── client.py        AdoClient — typed, lazy-cached git/core/work_items properties
├── context.py       Layer 2: RepositoryContext (thread-safe caching)
├── pr.py            Layer 3: AzureDevOpsPRContext + establish_pr_context factory
├── review.py        Layer 3: get_review_status, analyze_pending_reviews, fetch helpers
├── comments.py      Layer 3: analyze_pr_comments, post_comment, reply_to_comment, resolve_comments
├── votes.py         Pure: determine_vote_status, deduplicate_team_containers
├── lifecycle.py     Layer 3: create_pull_request (future: update, complete)
├── models.py        Domain dataclasses (ReviewerInfo, VoteStatus, CreatedPR, etc.)
└── py.typed         PEP 561 marker
```

### Dependency Flow (Internal)

```
parsing.py ──▶ discovery.py ──▶ context.py ──▶ pr.py
                                    │            │
                auth.py ──▶ client.py ──────────┤
                                                 │
                          models.py ◀── votes.py │
                                │                │
                         review.py ◀─────────────┘
                         comments.py
                         lifecycle.py
```

---

## Key Decisions

### Three-Layer Separation

Layer 1 functions are pure — no state, no SDK, no network. They can be tested
without mocks and used in any context. Layer 2 adds thread-safe state (caching).
Layer 3 composes both and delegates to the Azure DevOps SDK.

This layering means consumers who only need URL parsing don't pull in `azure-devops`,
and consumers who need full PR workflows get a composed API that handles context
resolution automatically.

### SDK Over CLI

Every Azure DevOps operation uses the `azure-devops` Python SDK rather than
`az` CLI subprocess calls. Benefits:

- No shell escaping, no JSON encoding via CLI arguments
- Typed models (`GitPullRequest`, `Comment`, etc.) instead of string manipulation
- Direct object passing — eliminates the CLI JSON-encoding fragility that caused
  production failures with complex comment bodies

### ActionableError Integration

All SDK failures are wrapped in `ActionableError` from `actionable-errors`.
Each error includes:

- **Service name** — always `"AzureDevOps"`
- **Context** — repository, PR ID, operation URL
- **Suggestion** — actionable recovery steps (permissions, branch existence, etc.)

This means every failure is self-documenting for both human operators and AI agents.

### Auth Architecture

`ConnectionFactory` bridges `azure-identity`'s `DefaultAzureCredential` into the
SDK's `msrest`-based auth layer:

```
DefaultAzureCredential → get_token(AZURE_DEVOPS_SCOPE) → BasicTokenAuthentication → Connection
```

Supports: Azure CLI credential (local dev), managed identity (CI), PAT fallback.
Token refresh is automatic (5-minute buffer before expiry). Connections are cached
per-org URL.

### Thread-Safe Context

`RepositoryContext` uses a `Lock` for thread safety. In multi-threaded MCP servers
(FastMCP), concurrent tool calls share the same context without race conditions.

### Partial-Success Operations

Batch operations like `resolve_comments()` and `analyze_pending_reviews()` use
partial-success semantics — individual failures are collected, not raised. The
caller receives a result container with structured success/failure lists:

- `ResolveResult` — `resolved`, `failed`, and `skipped` thread ID lists
- `PendingReviewResult` — `pending_prs` and `skipped` (with `ActionableError`
  entries for each per-PR enrichment failure)

This two-tier error pattern (Raise for non-recoverable, Collect for partial
success) is used consistently across all operations that iterate over multiple
items.

# ado-workflows

Azure DevOps workflow automation library — PR review analysis, comment lifecycle, repository discovery, and Python SDK wrappers.

[![CI](https://github.com/grimlor/ado-workflows/actions/workflows/ci.yml/badge.svg)](https://github.com/grimlor/ado-workflows/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/grimlor/b7836cded70590f934b1877fd521c26b/raw/ado-workflows-coverage.json)](https://github.com/grimlor/ado-workflows)
[![PyPI](https://img.shields.io/pypi/v/ado-workflows)](https://pypi.org/project/ado-workflows/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Install

```bash
pip install ado-workflows
```

## Quick Start

```python
from ado_workflows import ConnectionFactory, AdoClient

# Authenticate (uses DefaultAzureCredential — Azure CLI, managed identity, etc.)
factory = ConnectionFactory()
connection = factory.get_connection("https://dev.azure.com/MyOrg")
client = AdoClient(connection)
```

### PR Review Status

```python
from ado_workflows import get_review_status

status = get_review_status(client, "MyProject", "MyRepo", pr_id=12345)
print(status.approval_status)    # ApprovalStatus.APPROVED / PENDING / REJECTED
print(status.total_reviewers)    # 3
print(status.required_approvals) # 2
for vs in status.vote_statuses:
    print(f"{vs.name}: {vs.vote_text} (stale: {vs.vote_invalidated})")
```

### Pending Review Analysis

```python
from ado_workflows import analyze_pending_reviews

result = analyze_pending_reviews(
    client, "MyProject", "MyRepo",
    max_days_old=14,
    creator_filter="alice",
)
for pr in result.pending_prs:  # sorted by days_open descending
    print(f"PR #{pr.pr_id} ({pr.days_open}d): {pr.title}")
    print(f"  Needs {pr.needs_approvals_count} more approvals")
    for r in pr.pending_reviewers:
        print(f"  - {r.display_name}")
```

### Comment Operations

```python
from ado_workflows import (
    analyze_pr_comments,
    post_comment,
    reply_to_comment,
    resolve_comments,
)

# Analyze threads
analysis = analyze_pr_comments(client, "MyProject", "MyRepo", pr_id=12345)
print(f"{analysis.summary.active_count} active, {analysis.summary.resolved_count} resolved")

# Post, reply, resolve
thread_id = post_comment(client, "MyProject", "MyRepo", pr_id=12345, content="LGTM")
reply_to_comment(client, "MyProject", "MyRepo", pr_id=12345, thread_id=thread_id, content="Thanks!")
result = resolve_comments(client, "MyProject", "MyRepo", pr_id=12345, thread_ids=[thread_id])
print(f"Resolved: {result.resolved}, Failed: {result.failed}")
```

### Repository Discovery

```python
from ado_workflows import discover_repositories, parse_ado_url

# Parse any Azure DevOps URL
org, project, repo, pr_id = parse_ado_url(
    "https://dev.azure.com/MyOrg/MyProject/_git/MyRepo/pullrequest/42"
)

# Discover git repos in a workspace
repos = discover_repositories("/path/to/workspace")
for r in repos:
    print(f"{r['name']} → {r['remote_url']}")
```

### PR Context

```python
from ado_workflows import establish_pr_context

# From a URL
ctx = establish_pr_context("https://dev.azure.com/MyOrg/MyProject/_git/MyRepo/pullrequest/42")

# From a PR ID (requires RepositoryContext to be set)
ctx = establish_pr_context(42, working_directory="/path/to/repo")

print(ctx.org_url)    # https://dev.azure.com/MyOrg
print(ctx.pr_id)      # 42
```

## Architecture

Three-layer API:

| Layer | Purpose | State | SDK dependency |
|-------|---------|-------|----------------|
| **1 — Primitives** | URL parsing, git inspection, date parsing | None | None |
| **2 — Context** | Repository context caching | Thread-safe | None |
| **3 — PR Operations** | Review, comments, lifecycle, pending analysis | Composes L1+L2 | `azure-devops` |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full design rationale.

## Error Handling

All errors are `ActionableError` instances (from [`actionable-errors`](https://pypi.org/project/actionable-errors/)) with structured context:

```python
from actionable_errors import ActionableError

try:
    status = get_review_status(client, "MyProject", "MyRepo", pr_id=99999)
except ActionableError as e:
    print(e.error_type)   # ErrorType.CONNECTION
    print(e.service)      # "AzureDevOps"
    print(e.suggestion)   # "Verify the PR ID exists and you have read access..."
```

## Development

```bash
uv sync --extra dev
task check          # lint + type-check + test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development setup and coding standards.

## License

MIT

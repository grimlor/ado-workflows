"""
Data-gathering functions for PR listing, work item querying, and commit history.

Generic building blocks for reporting, dashboards, and automation.
All SDK calls are wrapped with :class:`~actionable_errors.ActionableError`
for consistent error handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from actionable_errors import ActionableError
from azure.devops.v7_1.git.models import GitPullRequest, GitPullRequestSearchCriteria
from azure.devops.v7_1.work_item_tracking.models import TeamContext, Wiql, WorkItem
from git import Repo

from ado_workflows._mapping import map_work_item_detail
from ado_workflows.errors import classify_ado_error
from ado_workflows.models import CommitSummary, PullRequestSummary, WorkItemDetail, WorkItemSummary

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient

_BATCH_SIZE = 200

_WORK_ITEM_FIELDS = [
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "System.AssignedTo",
    "System.IterationPath",
    "Microsoft.VSTS.Scheduling.CompletedWork",
    "Microsoft.VSTS.Scheduling.RemainingWork",
]


# ---------------------------------------------------------------------------
# PR listing — B1
# ---------------------------------------------------------------------------


def list_pull_requests(
    client: AdoClient,
    project: str,
    *,
    creator_id: str | None = None,
    reviewer_id: str | None = None,
    status: str = "all",
    repository_id: str | None = None,
    top: int = 50,
) -> list[PullRequestSummary]:
    """
    List pull requests matching search criteria.

    Routes to ``get_pull_requests`` (repo-scoped) when *repository_id* is
    given, or ``get_pull_requests_by_project`` (project-wide) otherwise.

    Returns:
        List of :class:`PullRequestSummary` with computed ``web_url``.

    Raises:
        ActionableError: When the SDK call fails.

    """
    criteria = GitPullRequestSearchCriteria(
        status=status,
        creator_id=creator_id,
        reviewer_id=reviewer_id,
    )

    try:
        if repository_id is not None:
            raw_prs = client.git.get_pull_requests(
                repository_id,
                criteria,
                project=project,
                top=top,
            )
        else:
            raw_prs = client.git.get_pull_requests_by_project(
                project,
                criteria,
                top=top,
            )
    except Exception as exc:
        raise classify_ado_error(
            exc, operation=f"list pull requests in '{project}'", context_hint=project
        ) from exc

    return [_map_pr_summary(pr) for pr in raw_prs]


def _map_pr_summary(pr: GitPullRequest) -> PullRequestSummary:
    repo_web_url = pr.repository.web_url
    pr_id = pr.pull_request_id
    return PullRequestSummary(
        pr_id=pr_id,
        title=pr.title,
        status=pr.status or "",
        created_by=pr.created_by.display_name,
        creation_date=str(pr.creation_date) if pr.creation_date else "",
        source_branch=pr.source_ref_name,
        target_branch=pr.target_ref_name,
        repository_name=pr.repository.name,
        web_url=f"{repo_web_url}/pullrequest/{pr_id}",
        is_draft=pr.is_draft,
        merge_status=pr.merge_status or "",
    )


# ---------------------------------------------------------------------------
# Work item fetch by ID — FR6a read operations
# ---------------------------------------------------------------------------


def get_work_item(
    client: AdoClient,
    project: str,
    work_item_id: int,
) -> WorkItemDetail:
    """
    Fetch a single work item by ID with full field data.

    Wraps ``client.work_items.get_work_item()`` with ``expand="All"``
    to retrieve all fields and relations.

    Returns:
        A single :class:`WorkItemDetail`.

    Raises:
        ActionableError: When the SDK call fails.

    """
    try:
        raw = client.work_items.get_work_item(
            work_item_id,
            project=project,
            expand="All",
        )
    except Exception as exc:
        raise classify_ado_error(
            exc,
            operation=f"fetch work item {work_item_id} in '{project}'",
            context_hint=project,
        ) from exc

    return map_work_item_detail(raw)


def get_work_items(
    client: AdoClient,
    project: str,
    work_item_ids: list[int],
) -> list[WorkItemDetail]:
    """
    Batch-fetch multiple work items by ID with full field data.

    Fetches in chunks of 200 (the ADO API batch limit) with
    ``expand="All"`` for full fields and relations. Returns an
    empty list when *work_item_ids* is empty, without making
    any SDK calls.

    Returns:
        List of :class:`WorkItemDetail`.

    Raises:
        ActionableError: When the SDK call fails.

    """
    if not work_item_ids:
        return []

    items: list[WorkItemDetail] = []
    for i in range(0, len(work_item_ids), _BATCH_SIZE):
        batch_ids = work_item_ids[i : i + _BATCH_SIZE]
        try:
            raw_items = client.work_items.get_work_items(
                batch_ids,
                project=project,
                expand="All",
            )
        except Exception as exc:
            raise classify_ado_error(
                exc,
                operation=f"fetch work items in '{project}'",
                context_hint=project,
            ) from exc
        items.extend(map_work_item_detail(wi) for wi in raw_items)

    return items


# ---------------------------------------------------------------------------
# Work item querying — B2
# ---------------------------------------------------------------------------


def query_work_items(
    client: AdoClient,
    project: str,
    wiql: str,
    *,
    top: int | None = None,
) -> list[WorkItemSummary]:
    """
    Execute a WIQL query and return enriched work item data.

    Performs a two-step fetch: WIQL query for IDs, then batch
    ``get_work_items`` in chunks of 200.

    Returns:
        List of :class:`WorkItemSummary`.

    Raises:
        ActionableError: When either SDK call fails.

    """
    wiql_obj = Wiql(query=wiql)
    try:
        query_result = client.work_items.query_by_wiql(
            wiql_obj,
            team_context=TeamContext(project=project),
            top=top,
        )
    except Exception as exc:
        raise classify_ado_error(
            exc, operation=f"execute WIQL query in '{project}'", context_hint=project
        ) from exc

    ids = [wi.id for wi in (query_result.work_items or [])]
    if not ids:
        return []

    items: list[WorkItemSummary] = []
    for i in range(0, len(ids), _BATCH_SIZE):
        batch_ids = ids[i : i + _BATCH_SIZE]
        try:
            raw_items = client.work_items.get_work_items(
                batch_ids,
                project=project,
                fields=_WORK_ITEM_FIELDS,
            )
        except Exception as exc:
            raise classify_ado_error(
                exc, operation=f"fetch work items in '{project}'", context_hint=project
            ) from exc
        items.extend(_map_work_item(wi) for wi in raw_items)

    return items


def _map_work_item(wi: WorkItem) -> WorkItemSummary:
    fields = wi.fields
    assigned_to = fields.get("System.AssignedTo")
    ip = fields.get("System.IterationPath")
    cw = fields.get("Microsoft.VSTS.Scheduling.CompletedWork")
    rw = fields.get("Microsoft.VSTS.Scheduling.RemainingWork")
    return WorkItemSummary(
        id=wi.id,
        title=str(fields.get("System.Title", "")),
        state=str(fields.get("System.State", "")),
        work_item_type=str(fields.get("System.WorkItemType", "")),
        assigned_to=str(assigned_to) if assigned_to is not None else None,
        iteration_path=str(ip) if ip is not None else None,
        completed_work=float(cw) if cw is not None else None,
        remaining_work=float(rw) if rw is not None else None,
        url=wi.url,
    )


# ---------------------------------------------------------------------------
# Git commit history — B3
# ---------------------------------------------------------------------------


def list_commits(
    repo_path: str,
    *,
    authors: list[str] | None = None,
    since: str | None = None,
    max_count: int = 100,
) -> list[CommitSummary]:
    """
    List git commits from a local repository.

    Uses GitPython to iterate commits across all branches (``--all``),
    deduplicating by SHA.

    Returns:
        List of :class:`CommitSummary` sorted by date descending.

    Raises:
        ActionableError: When the repo path is invalid.

    """
    repo_name = Path(repo_path).name

    try:
        repo = Repo(repo_path)
    except Exception as exc:
        raise ActionableError.not_found(
            service="git",
            resource_type="repository",
            resource_id=repo_path,
            raw_error=str(exc),
        ) from exc

    author_pattern = "|".join(authors) if authors else None

    seen: set[str] = set()
    commits: list[CommitSummary] = []
    for commit in repo.iter_commits(
        all=True,
        max_count=max_count,
        since=since,
        author=author_pattern,
    ):
        sha: str = commit.hexsha
        if sha in seen:
            continue
        seen.add(sha)
        commits.append(
            CommitSummary(
                sha=sha,
                message=str(commit.message),
                author=str(commit.author.name or ""),
                date=commit.committed_date,
                repo_name=repo_name,
            )
        )

    # Sort by date descending (newest first)
    commits.sort(key=lambda c: c.date, reverse=True)
    return commits

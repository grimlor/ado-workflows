"""ado-workflows: Azure DevOps workflow automation library.

Three-layer API for Azure DevOps operations:
- Layer 1 — Primitives: pure functions (URL parsing, git inspection, date parsing)
- Layer 2 — Context: stateful caching (RepositoryContext, thread-safe)
- Layer 3 — PR Context: composed workflows (AzureDevOpsPRContext)
"""

from __future__ import annotations

from ado_workflows.auth import AZURE_DEVOPS_RESOURCE_ID, ConnectionFactory
from ado_workflows.client import AdoClient
from ado_workflows.comments import (
    analyze_pr_comments,
    post_comment,
    reply_to_comment,
    resolve_comments,
    sanitize_ado_response,
)
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
from ado_workflows.lifecycle import create_pull_request
from ado_workflows.models import (
    VOTE_TEXT,
    ApprovalStatus,
    AuthorSample,
    CommentAnalysis,
    CommentInfo,
    CommentPayload,
    CommentSummary,
    ContentResult,
    CreatedPR,
    FileChange,
    FileContent,
    IterationContext,
    IterationInfo,
    PendingPR,
    PendingReviewResult,
    PostingResult,
    ResolveResult,
    ReviewerInfo,
    ReviewStatus,
    UserIdentity,
    VoteStatus,
)
from ado_workflows.parsing import parse_ado_date, parse_ado_url
from ado_workflows.pr import AzureDevOpsPRContext, establish_pr_context
from ado_workflows.review import (
    analyze_pending_reviews,
    fetch_required_approvals,
    fetch_vote_timestamps,
    get_review_status,
)
from ado_workflows.votes import deduplicate_team_containers, determine_vote_status

__all__: list[str] = [
    "AZURE_DEVOPS_RESOURCE_ID",
    "VOTE_TEXT",
    "AdoClient",
    "ApprovalStatus",
    "AuthorSample",
    "AzureDevOpsPRContext",
    "CommentAnalysis",
    "CommentInfo",
    "CommentPayload",
    "CommentSummary",
    "ConnectionFactory",
    "ContentResult",
    "CreatedPR",
    "FileChange",
    "FileContent",
    "IterationContext",
    "IterationInfo",
    "PendingPR",
    "PendingReviewResult",
    "PostingResult",
    "RepositoryContext",
    "ResolveResult",
    "ReviewStatus",
    "ReviewerInfo",
    "UserIdentity",
    "VoteStatus",
    "analyze_pending_reviews",
    "analyze_pr_comments",
    "clear_repository_context",
    "create_pull_request",
    "deduplicate_team_containers",
    "determine_vote_status",
    "discover_repositories",
    "establish_pr_context",
    "fetch_required_approvals",
    "fetch_vote_timestamps",
    "get_context_status",
    "get_repository_context",
    "get_review_status",
    "infer_target_repository",
    "inspect_git_repository",
    "parse_ado_date",
    "parse_ado_url",
    "post_comment",
    "reply_to_comment",
    "resolve_comments",
    "sanitize_ado_response",
    "set_repository_context",
]

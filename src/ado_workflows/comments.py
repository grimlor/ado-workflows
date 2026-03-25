"""
Comment analysis, response sanitization, and write operations for Azure DevOps PRs.

Provides :func:`sanitize_ado_response` (pure utility for Windows-1252
smart-quote fix), :func:`analyze_pr_comments` (SDK-based thread
analysis returning a typed :class:`~models.CommentAnalysis`),
:func:`post_comment`, :func:`reply_to_comment`, and
:func:`resolve_comments` (SDK-based write operations).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from actionable_errors import ActionableError

from ado_workflows.iterations import get_latest_iteration_context
from ado_workflows.models import (
    AuthorSample,
    CommentAnalysis,
    CommentInfo,
    CommentPayload,
    CommentSummary,
    IterationContext,
    PostingResult,
    ResolveResult,
)

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient

from azure.devops.v7_1.git.models import (
    Comment,
    CommentIterationContext,
    CommentPosition,
    CommentThreadContext,
    GitPullRequestCommentThread,
    GitPullRequestCommentThreadContext,
)


def sanitize_ado_response(raw_data: bytes | str) -> str:
    """
    Fix Windows-1252 smart-quote corruption in ADO responses.

    Azure DevOps REST API sometimes returns Windows-1252 smart quotes
    (0x91-0x94) which are invalid UTF-8.  This function replaces those
    bytes with their UTF-8 equivalents before decoding.

    String inputs pass through unchanged.

    Args:
        raw_data: Raw bytes or string from an ADO API response.

    Returns:
        A properly decoded UTF-8 string.

    """
    if isinstance(raw_data, str):
        return raw_data

    raw_bytes: bytes = raw_data

    # Windows-1252 → UTF-8 smart quote replacements
    # 0x91 = left single quote  (') → U+2018
    # 0x92 = right single quote (') → U+2019
    # 0x93 = left double quote  (") → U+201C
    # 0x94 = right double quote (") → U+201D
    sanitized = raw_bytes.replace(b"\x91", b"\xe2\x80\x98")
    sanitized = sanitized.replace(b"\x92", b"\xe2\x80\x99")
    sanitized = sanitized.replace(b"\x93", b"\xe2\x80\x9c")
    sanitized = sanitized.replace(b"\x94", b"\xe2\x80\x9d")

    return sanitized.decode("utf-8")


def analyze_pr_comments(
    client: AdoClient,
    pr_id: int,
    project: str,
    repository: str,
) -> CommentAnalysis:
    """
    Analyze all comment threads on a pull request.

    Fetches threads via ``client.git.get_threads()``, categorizes by
    status, extracts author statistics, and identifies active (unresolved)
    comments.  Applies :func:`sanitize_ado_response` to comment content
    strings.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        pr_id: Pull request ID.
        project: Azure DevOps project name or GUID.
        repository: Repository name or GUID.

    Returns:
        A :class:`~models.CommentAnalysis` with thread statistics,
        author breakdowns, and active comments.

    """
    threads = client.git.get_threads(repository, pr_id, project=project)

    # Categorize threads by status
    active_threads = [t for t in threads if t.status == "active"]
    fixed_threads = [t for t in threads if t.status == "fixed"]
    total_threads = len(threads)

    active_percentage = (
        round(len(active_threads) / total_threads * 100, 1) if total_threads > 0 else 0.0
    )

    # Analyze comments: authors, content, file context
    comment_authors: dict[str, int] = {}
    all_comments: list[CommentInfo] = []
    active_comments: list[CommentInfo] = []

    for thread in threads:
        # Extract file context from thread_context
        file_path: str | None = None
        line_start: int | None = None
        line_end: int | None = None

        thread_context = thread.thread_context
        if thread_context is not None:
            file_path = thread_context.file_path

            # Prefer right (new) side, fall back to left (old) side
            if thread_context.right_file_start is not None:
                line_start = thread_context.right_file_start.line
            if line_start is None and thread_context.left_file_start is not None:
                line_start = thread_context.left_file_start.line

            if thread_context.right_file_end is not None:
                line_end = thread_context.right_file_end.line
            if line_end is None and thread_context.left_file_end is not None:
                line_end = thread_context.left_file_end.line

        for comment in thread.comments or []:
            author_name: str = comment.author.display_name
            comment_authors[author_name] = comment_authors.get(author_name, 0) + 1

            content: str = sanitize_ado_response(comment.content or "")
            preview = content[:200] + "..." if len(content) > 200 else content

            info = CommentInfo(
                thread_id=thread.id,
                thread_status=thread.status or "unknown",
                author=author_name,
                content_preview=preview,
                full_content=content,
                created_date=comment.published_date,
                is_deleted=comment.is_deleted or False,
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
            )
            all_comments.append(info)
            if thread.status == "active":
                active_comments.append(info)

    # Build author samples (latest non-deleted comment per author)
    author_samples: dict[str, AuthorSample] = {}
    for author_name in comment_authors:
        author_comments = [c for c in all_comments if c.author == author_name and not c.is_deleted]
        if author_comments:
            latest = author_comments[-1]
            author_samples[author_name] = AuthorSample(
                count=comment_authors[author_name],
                latest_comment=latest.content_preview,
                latest_status=latest.thread_status,
            )

    return CommentAnalysis(
        pr_id=pr_id,
        comment_summary=CommentSummary(
            total_threads=total_threads,
            active_threads=len(active_threads),
            fixed_threads=len(fixed_threads),
            active_percentage=active_percentage,
        ),
        comment_authors=comment_authors,
        author_samples=author_samples,
        active_comments=active_comments,
        resolution_ready=len(active_threads) == 0,
    )


# ---------------------------------------------------------------------------
# Phase 6d — Comment write operations
# ---------------------------------------------------------------------------


def post_comment(
    client: AdoClient,
    repository: str,
    pr_id: int,
    content: str,
    project: str,
    *,
    status: str = "active",
    file_path: str | None = None,
    line_number: int | None = None,
    iteration_context: IterationContext | None = None,
) -> int:
    """
    Create a new comment thread on a pull request.

    When *file_path* and *line_number* are provided, the comment is anchored
    to that line in the PR diff.  If *iteration_context* is also provided,
    the comment targets the correct iteration.  If *iteration_context* is
    ``None`` but *file_path* is given, the latest iteration context is
    auto-resolved.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        content: Comment body text (must not be empty).
        project: Azure DevOps project name or GUID.
        status: Thread status (default ``"active"``).
        file_path: Optional file path for line-positioned comments.
        line_number: Optional line number (requires *file_path*).
        iteration_context: Optional pre-resolved iteration context.

    Returns:
        The new thread ID.

    Raises:
        ActionableError: When *content* is empty/whitespace, when
            *file_path*/*line_number* are inconsistent, or when the SDK fails.

    """
    if not content or not content.strip():
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="content",
            reason="Cannot post a comment with empty content.",
            suggestion="Provide non-empty comment content.",
        )

    # Validate file_path / line_number consistency
    if file_path is not None and line_number is None:
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="line_number",
            reason="file_path was provided without line_number.",
            suggestion="Provide both file_path and line_number for positioned comments.",
        )
    if line_number is not None and file_path is None:
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="file_path",
            reason="line_number was provided without file_path.",
            suggestion="Provide both file_path and line_number for positioned comments.",
        )

    thread = GitPullRequestCommentThread(
        comments=[Comment(content=content)],
        status=status,
    )

    # Add file/line positioning if requested
    if file_path is not None and line_number is not None:
        thread.thread_context = CommentThreadContext(
            file_path=f"/{file_path}" if not file_path.startswith("/") else file_path,
            right_file_start=CommentPosition(line=line_number, offset=1),
            right_file_end=CommentPosition(line=line_number, offset=1),
        )

        # Auto-resolve iteration context if not provided
        if iteration_context is None:
            iteration_context = get_latest_iteration_context(client, repository, pr_id, project)

        # Look up change tracking ID for this file
        file_key = file_path.lstrip("/")
        file_change = iteration_context.file_changes.get(file_key)
        change_tracking_id = file_change.change_tracking_id if file_change else 1

        thread.pull_request_thread_context = GitPullRequestCommentThreadContext(
            change_tracking_id=change_tracking_id,
            iteration_context=CommentIterationContext(
                first_comparing_iteration=1,
                second_comparing_iteration=iteration_context.iteration_id,
            ),
        )

    try:
        response = client.git.create_thread(thread, repository, pr_id, project=project)
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/threads",
            raw_error=str(exc),
        ) from exc

    return int(response.id)


def post_comments(
    client: AdoClient,
    repository: str,
    pr_id: int,
    comments: list[CommentPayload],
    project: str,
    *,
    dry_run: bool = False,
) -> PostingResult:
    """
    Batch-post comment threads with per-comment file/line positioning.

    Each :class:`~models.CommentPayload` carries its own content, file_path,
    and line_number.  Iteration context is resolved once and shared across
    all positioned comments.

    Uses partial-success semantics: individual failures are collected,
    not raised.

    When *dry_run* is ``True``, validates all comments and returns what
    would be posted without making any API calls.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        comments: List of comment payloads to post.
        project: Azure DevOps project name or GUID.
        dry_run: If ``True``, validate without posting.

    Returns:
        :class:`~models.PostingResult` with successes, failures, and skipped.

    """
    if not comments:
        return PostingResult(posted=[], failures=[], skipped=[], dry_run=dry_run)

    if dry_run:
        return PostingResult(
            posted=[],
            failures=[],
            skipped=list(range(len(comments))),
            dry_run=True,
        )

    # Resolve iteration context once for all positioned comments
    iter_ctx: IterationContext | None = None
    has_positioned = any(c.file_path is not None for c in comments)
    if has_positioned:
        with contextlib.suppress(Exception):
            iter_ctx = get_latest_iteration_context(client, repository, pr_id, project)

    posted: list[int] = []
    failures: list[ActionableError] = []

    for i, comment in enumerate(comments):
        try:
            thread_id = post_comment(
                client,
                repository,
                pr_id,
                comment.content,
                project,
                status=comment.status,
                file_path=comment.file_path,
                line_number=comment.line_number,
                iteration_context=iter_ctx,
            )
            posted.append(thread_id)
        except Exception as exc:
            err = ActionableError.internal(
                service="ado-workflows",
                operation="post_comment",
                raw_error=str(exc),
                suggestion=(
                    f"Comment {i} failed to post. Check content, file path, and line number."
                ),
            )
            err.context = {
                "index": i,
                "content_preview": comment.content[:80],
            }
            failures.append(err)

    return PostingResult(
        posted=posted,
        failures=failures,
        skipped=[],
        dry_run=False,
    )


def reply_to_comment(
    client: AdoClient,
    repository: str,
    pr_id: int,
    thread_id: int,
    content: str,
    project: str,
) -> int:
    """
    Add a reply to an existing comment thread.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        thread_id: Existing thread ID to reply to.
        content: Reply body text (must not be empty).
        project: Azure DevOps project name or GUID.

    Returns:
        The new comment ID.

    Raises:
        ActionableError: When *content* is empty/whitespace or the SDK fails.

    """
    if not content or not content.strip():
        raise ActionableError.validation(
            service="AzureDevOps",
            field_name="content",
            reason="Cannot reply with empty content.",
        )

    comment = Comment(content=content, parent_comment_id=1)

    try:
        response = client.git.create_comment(
            comment,
            repository,
            pr_id,
            thread_id,
            project=project,
        )
    except Exception as exc:
        raise ActionableError.connection(
            service="AzureDevOps",
            url=f"{repository}/pullrequests/{pr_id}/threads/{thread_id}/comments",
            raw_error=str(exc),
        ) from exc

    return int(response.id)


def resolve_comments(
    client: AdoClient,
    repository: str,
    pr_id: int,
    thread_ids: list[int],
    project: str,
    *,
    status: str = "fixed",
) -> ResolveResult:
    """
    Batch-resolve comment threads with partial-success reporting.

    Iterates *thread_ids* and calls ``client.git.update_thread()`` for
    each.  Threads already in the target status are skipped.  Individual
    failures are collected — the function never raises on a single thread
    failure.

    Args:
        client: An authenticated :class:`~client.AdoClient`.
        repository: Repository name or GUID.
        pr_id: Pull request ID.
        thread_ids: Thread IDs to resolve.
        project: Azure DevOps project name or GUID.
        status: Target thread status (default ``"fixed"``).

    Returns:
        A :class:`~models.ResolveResult` partitioning threads into
        *resolved*, *errors*, and *skipped*.

    """
    resolved: list[int] = []
    errors: list[ActionableError] = []
    skipped: list[int] = []

    if not thread_ids:
        return ResolveResult(resolved=resolved, errors=errors, skipped=skipped)

    # Fetch current thread statuses for skip detection
    all_threads = client.git.get_threads(
        repository,
        pr_id,
        project=project,
    )
    current_status: dict[int, str | None] = {t.id: t.status for t in all_threads}

    for tid in thread_ids:
        # Skip threads already in the target status
        if current_status.get(tid) == status:
            skipped.append(tid)
            continue

        thread_update = GitPullRequestCommentThread(status=status)
        try:
            client.git.update_thread(
                thread_update,
                repository,
                pr_id,
                tid,
                project=project,
            )
            resolved.append(tid)
        except Exception as exc:
            err = ActionableError.internal(
                service="AzureDevOps",
                operation=f"resolve_thread({tid})",
                raw_error=str(exc),
            )
            err.context = {"thread_id": tid}
            errors.append(err)

    return ResolveResult(resolved=resolved, errors=errors, skipped=skipped)

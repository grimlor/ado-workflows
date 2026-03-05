"""Comment analysis and response sanitization for Azure DevOps PRs.

Provides :func:`sanitize_ado_response` (pure utility for Windows-1252
smart-quote fix) and :func:`analyze_pr_comments` (SDK-based thread
analysis returning a typed :class:`~models.CommentAnalysis`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ado_workflows.models import (
    AuthorSample,
    CommentAnalysis,
    CommentInfo,
    CommentSummary,
)

if TYPE_CHECKING:
    from ado_workflows.client import AdoClient


def sanitize_ado_response(raw_data: bytes | str) -> str:
    """Fix Windows-1252 smart-quote corruption in ADO responses.

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

    raw_bytes: bytes = bytes(raw_data) if not isinstance(raw_data, bytes) else raw_data

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
    """Analyze all comment threads on a pull request.

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
    threads: list[Any] = client.git.get_threads(repository, pr_id, project=project)

    # Categorize threads by status
    active_threads = [t for t in threads if t.status == "active"]
    fixed_threads = [t for t in threads if t.status == "fixed"]
    total_threads = len(threads)

    active_percentage = (
        round(len(active_threads) / total_threads * 100, 1)
        if total_threads > 0
        else 0.0
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
            preview = (
                content[:200] + "..." if len(content) > 200 else content
            )

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
        author_comments = [
            c for c in all_comments
            if c.author == author_name and not c.is_deleted
        ]
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

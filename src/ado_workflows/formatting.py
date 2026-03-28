"""
Comment formatting for Azure DevOps rich code review comments.

Provides a default markdown formatter and a dispatch function that
supports pluggable custom formatters.
"""

from __future__ import annotations

from collections.abc import Callable

from ado_workflows.models import CommentSeverity, CommentType, RichComment

CommentFormatter = Callable[[RichComment], str]
"""Callable that renders a :class:`~models.RichComment` as a string."""

_SEVERITY_ICONS: dict[CommentSeverity, str] = {
    CommentSeverity.INFO: "\u2139\ufe0f",
    CommentSeverity.SUGGESTION: "\U0001f4a1",  # 💡
    CommentSeverity.WARNING: "\u26a0\ufe0f",  # ⚠️
    CommentSeverity.ERROR: "\u274c",  # ❌
    CommentSeverity.CRITICAL: "\U0001f6a8",  # 🚨
}

_TYPE_HEADERS: dict[CommentType, tuple[str, str]] = {
    CommentType.SECURITY: ("\U0001f512", "SECURITY"),  # 🔒
    CommentType.PERFORMANCE: ("\u26a1", "PERFORMANCE"),  # ⚡
}


def default_comment_formatter(comment: RichComment) -> str:
    """
    Format a :class:`~models.RichComment` into Azure DevOps markdown.

    Produces severity icons, type-specific headers, suggested code
    blocks, reasoning and business-impact sections, and tag badges.
    """
    severity_icon = _SEVERITY_ICONS.get(comment.severity, "\U0001f4dd")  # 📝 fallback

    # Build header based on comment type
    type_info = _TYPE_HEADERS.get(comment.comment_type)
    if type_info is not None:
        type_icon, type_label = type_info
        header = f"{type_icon} **{type_label}**: {severity_icon} {comment.title}"
    else:
        header = f"{severity_icon} **{comment.title}**"

    parts: list[str] = [header, "", comment.content]

    if comment.suggested_code:
        parts.extend(
            ["", "**\U0001f4a1 Suggested Code:**", "```python", comment.suggested_code, "```"]
        )

    if comment.reasoning:
        parts.extend(["", "**\U0001f914 Reasoning:**", comment.reasoning])

    if comment.business_impact:
        parts.extend(["", "**\U0001f4c8 Business Impact:**", comment.business_impact])

    if comment.tags:
        tag_str = " ".join(f"`{tag}`" for tag in comment.tags)
        parts.extend(["", f"**\U0001f3f7\ufe0f Tags:** {tag_str}"])

    return "\n".join(parts)


def format_comment(
    comment: RichComment,
    formatter: CommentFormatter | None = None,
) -> str:
    """
    Apply a formatter to a :class:`~models.RichComment`.

    Uses :func:`default_comment_formatter` when *formatter* is ``None``.
    """
    fn = formatter if formatter is not None else default_comment_formatter
    return fn(comment)

"""
Self-praise detection and filtering for AI-generated code review comments.

Prevents AI agents from posting self-congratulatory comments when
reviewing their own pull requests.
"""

from __future__ import annotations

import re

from ado_workflows.models import CommentSeverity, RichComment, UserIdentity

_PRAISE_PATTERNS: list[str] = [
    r"\bexcellent\s+work\b",
    r"\bgreat\s+job\b",
    r"\bwell\s+done\b",
    r"\bnice\s+work\b",
    r"\bgood\s+job\b",
    r"\bawesome\s+work\b",
    r"\bfantastic\s+job\b",
    r"\bbrilliant\s+work\b",
    r"\bimpressive\s+work\b",
    r"\blooks?\s+great\b",
    r"\blooks?\s+good\b",
    r"\bperfect(ly)?\b",
    r"\bkudos\b",
    r"\bwell\s+written\b",
]

_POSITIVE_WORD_RE = re.compile(
    r"\b("
    r"excellent|great|good|perfect|awesome|fantastic|amazing|wonderful|"
    r"brilliant|superb|outstanding|exceptional|impressive|beautiful|"
    r"elegant|clean|clear|comprehensive|thorough"
    r")\b",
    re.IGNORECASE,
)


def is_praise_comment(comment: RichComment) -> bool:
    """
    Detect whether a comment is praise based on title/content heuristics.

    Returns ``False`` immediately for comments with severity
    :attr:`~models.CommentSeverity.ERROR` or
    :attr:`~models.CommentSeverity.CRITICAL` — those represent real
    problems and should never be suppressed.
    """
    # Critical feedback is never praise
    if comment.severity in {CommentSeverity.ERROR, CommentSeverity.CRITICAL}:
        return False

    text = f"{comment.title} {comment.content}".lower()

    # Explicit praise patterns
    for pattern in _PRAISE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    # Positive-word density heuristic
    positive_words = _POSITIVE_WORD_RE.findall(text)
    word_count = len(text.split())
    return len(positive_words) >= 2 and word_count < 100


def filter_self_praise(
    comments: list[RichComment],
    pr_author: UserIdentity,
    current_user: UserIdentity,
) -> tuple[list[RichComment], list[RichComment]]:
    """
    Partition *comments* into ``(to_post, local_praise)``.

    When ``current_user.id == pr_author.id``, praise comments (as
    determined by :func:`is_praise_comment`) are moved to *local_praise*.
    When IDs differ, all comments go to *to_post*.
    """
    if current_user.id != pr_author.id:
        return list(comments), []

    to_post: list[RichComment] = []
    local_praise: list[RichComment] = []

    for comment in comments:
        if is_praise_comment(comment):
            local_praise.append(comment)
        else:
            to_post.append(comment)

    return to_post, local_praise

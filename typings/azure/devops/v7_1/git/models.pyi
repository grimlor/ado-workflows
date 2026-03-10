"""Partial stubs for azure.devops.v7_1.git.models — only types used by ado-workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any

class IdentityRef:
    display_name: str
    unique_name: str
    id: str
    def __init__(
        self,
        *,
        display_name: str | None = None,
        unique_name: str | None = None,
        id: str | None = None,
        **kwargs: Any,
    ) -> None: ...


class CommentPosition:
    line: int
    offset: int
    def __init__(
        self,
        *,
        line: int | None = None,
        offset: int | None = None,
    ) -> None: ...


class CommentThreadContext:
    file_path: str | None
    left_file_start: CommentPosition | None
    left_file_end: CommentPosition | None
    right_file_start: CommentPosition | None
    right_file_end: CommentPosition | None
    def __init__(
        self,
        *,
        file_path: str | None = None,
        left_file_start: CommentPosition | None = None,
        left_file_end: CommentPosition | None = None,
        right_file_start: CommentPosition | None = None,
        right_file_end: CommentPosition | None = None,
    ) -> None: ...


class Comment:
    author: IdentityRef
    content: str | None
    id: int
    is_deleted: bool | None
    parent_comment_id: int | None
    published_date: str | None
    def __init__(
        self,
        *,
        content: str | None = None,
        parent_comment_id: int | None = None,
        author: IdentityRef | None = None,
        id: int | None = None,
        is_deleted: bool | None = None,
        published_date: str | None = None,
        **kwargs: Any,
    ) -> None: ...


class GitPullRequestCommentThread:
    id: int
    status: str | None
    comments: list[Comment] | None
    thread_context: CommentThreadContext | None
    properties: dict[str, Any] | None
    identities: dict[str, IdentityRef] | None
    published_date: datetime | None
    is_deleted: bool | None
    def __init__(
        self,
        *,
        comments: list[Comment] | None = None,
        status: str | None = None,
        id: int | None = None,
        thread_context: CommentThreadContext | None = None,
        properties: dict[str, Any] | None = None,
        identities: dict[str, IdentityRef] | None = None,
        published_date: datetime | None = None,
        is_deleted: bool | None = None,
        **kwargs: Any,
    ) -> None: ...


class GitPullRequestSearchCriteria:
    status: str | None
    def __init__(
        self,
        *,
        status: str | None = None,
        **kwargs: Any,
    ) -> None: ...


class _VotedForRef:
    id: str


class IdentityRefWithVote:
    display_name: str | None
    unique_name: str | None
    id: str | None
    vote: int | None
    is_container: bool | None
    voted_for: list[_VotedForRef] | None
    def __init__(
        self,
        *,
        display_name: str | None = None,
        unique_name: str | None = None,
        id: str | None = None,
        vote: int | None = None,
        is_container: bool | None = None,
        voted_for: list[_VotedForRef] | None = None,
        **kwargs: Any,
    ) -> None: ...


class GitUserDate:
    date: datetime
    email: str | None
    name: str | None


class GitCommitRef:
    author: GitUserDate
    commit_id: str | None
    def __init__(
        self,
        *,
        author: GitUserDate | None = None,
        commit_id: str | None = None,
        **kwargs: Any,
    ) -> None: ...


class GitPullRequest:
    pull_request_id: int
    title: str
    url: str
    source_ref_name: str
    target_ref_name: str
    is_draft: bool
    status: str | None
    merge_status: str | None
    creation_date: datetime | None
    created_by: IdentityRef
    reviewers: list[IdentityRefWithVote] | None
    description: str | None
    def __init__(
        self,
        *,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        is_draft: bool | None = None,
        status: str | None = None,
        **kwargs: Any,
    ) -> None: ...

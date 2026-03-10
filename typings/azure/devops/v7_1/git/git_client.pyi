"""Partial stubs for azure.devops.v7_1.git.git_client — only methods used by ado-workflows."""

from __future__ import annotations

from typing import Any

from azure.devops.v7_1.git.models import (
    Comment,
    GitCommitRef,
    GitPullRequest,
    GitPullRequestCommentThread,
    GitPullRequestSearchCriteria,
)

class GitClient:
    def get_pull_request_by_id(
        self,
        pull_request_id: int,
        project: str | None = None,
    ) -> GitPullRequest: ...

    def get_pull_requests(
        self,
        repository_id: str,
        search_criteria: GitPullRequestSearchCriteria,
        project: str | None = None,
        max_comment_length: int | None = None,
        skip: int | None = None,
        top: int | None = None,
    ) -> list[GitPullRequest]: ...

    def get_threads(
        self,
        repository_id: str,
        pull_request_id: int,
        project: str | None = None,
        iteration: int | None = None,
        base_iteration: int | None = None,
    ) -> list[GitPullRequestCommentThread]: ...

    def create_thread(
        self,
        comment_thread: GitPullRequestCommentThread,
        repository_id: str,
        pull_request_id: int,
        project: str | None = None,
    ) -> GitPullRequestCommentThread: ...

    def create_comment(
        self,
        comment: Comment,
        repository_id: str,
        pull_request_id: int,
        thread_id: int,
        project: str | None = None,
    ) -> Comment: ...

    def update_thread(
        self,
        comment_thread: GitPullRequestCommentThread,
        repository_id: str,
        pull_request_id: int,
        thread_id: int,
        project: str | None = None,
    ) -> GitPullRequestCommentThread: ...

    def create_pull_request(
        self,
        git_pull_request_to_create: GitPullRequest,
        repository_id: str,
        project: str | None = None,
    ) -> GitPullRequest: ...

    def get_pull_request_commits(
        self,
        repository_id: str,
        pull_request_id: int,
        project: str | None = None,
    ) -> list[GitCommitRef]: ...

    def get_pull_request_properties(
        self,
        repository_id: str,
        pull_request_id: int,
        project: str | None = None,
    ) -> dict[str, Any]: ...

    def get_repositories(
        self,
        project: str | None = None,
        include_links: bool | None = None,
        include_all_urls: bool | None = None,
        include_hidden: bool | None = None,
    ) -> list[Any]: ...

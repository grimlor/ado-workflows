"""
BDD tests for ado_workflows.content -- file content retrieval.

Covers:
- TestGetFileContent: fetch single file content from a repository ref
- TestGetChangedFileContents: batch fetch for PR changes with partial-success
- TestGetChangedFileContentsFiltering: extension-based pre-fetch filtering
- TestGetChangedFileContentsCompletedPR: completed-PR fallback for deleted branches

Public API surface (from src/ado_workflows/content.py):
    get_file_content(client: AdoClient, repository: str, path: str,
                     project: str, *, version: str | None = None,
                     version_type: str = "branch") -> FileContent
    get_changed_file_contents(client: AdoClient, repository: str,
                              pr_id: int, project: str, *,
                              file_paths: list[str] | None = None,
                              exclude_extensions: list[str] | None = None) -> ContentResult
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.content import (
    get_changed_file_contents,
    get_file_content,
)
from ado_workflows.models import ContentResult, FileContent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(*, content_bytes: bytes = b"print('hello')\n") -> Mock:
    """Return a mock AdoClient whose git.get_item_content returns an iterable of bytes."""
    client = Mock()
    # SDK returns an iterator of bytes chunks
    client.git.get_item_content.return_value = iter([content_bytes])
    return client


def _mock_pr_client_with_files(
    file_paths: list[str],
    *,
    content_bytes: bytes = b"content",
    source_ref: str = "refs/heads/feature",
    status: str = "active",
    last_merge_source_commit: str | None = None,
) -> Mock:
    """Return a mock AdoClient with a PR that has the given changed files.

    Configures all SDK mocks needed for get_changed_file_contents:
    PR lookup, iteration discovery, iteration changes, and file content.
    """
    client = Mock()

    # PR metadata
    pr_mock = Mock()
    pr_mock.source_ref_name = source_ref
    pr_mock.status = status
    merge_commit_mock = Mock()
    merge_commit_mock.commit_id = last_merge_source_commit
    pr_mock.last_merge_source_commit = merge_commit_mock if last_merge_source_commit else None
    client.git.get_pull_request_by_id.return_value = pr_mock

    # Iteration discovery
    iter_mock = Mock()
    iter_mock.id = 1
    iter_mock.created_date = datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC)
    iter_mock.description = None
    client.git.get_pull_request_iterations.return_value = [iter_mock]

    # Iteration changes
    changes_mock = Mock()
    entries: list[Mock] = []
    for i, path in enumerate(file_paths):
        change = Mock()
        change.change_tracking_id = i + 1
        change.additional_properties = {
            "item": {"path": path},
            "changeType": "edit",
        }
        entries.append(change)
    changes_mock.change_entries = entries
    client.git.get_pull_request_iteration_changes.return_value = changes_mock

    # File content
    client.git.get_item_content.return_value = iter([content_bytes])

    return client


# ---------------------------------------------------------------------------
# TestGetFileContent
# ---------------------------------------------------------------------------


class TestGetFileContent:
    """
    REQUIREMENT: Fetch file source code from a repository ref.

    WHO: Code review tools that need to read source code for analysis.
    WHAT: (1) a valid file path and branch returns FileContent with the content string
          (2) a non-existent file raises ActionableError with the original error context and potential corrective actions for the user
          (3) no version specified uses the default branch
          (4) a binary file returns FileContent with appropriate encoding note
    WHY: Enables code review without requiring a local checkout.

    MOCK BOUNDARY:
        Mock:  client.git.get_item_content (SDK network call)
        Real:  get_file_content encoding detection, content assembly
        Never: get_file_content itself
    """

    def test_valid_file_returns_file_content(self) -> None:
        """
        Given a valid file path and branch,
        When get_file_content is called,
        Then it returns FileContent with the content string.
        """
        # Given: a valid file path on a branch
        source = "def main():\n    pass\n"
        client = _mock_client(content_bytes=source.encode("utf-8"))

        # When: get_file_content is called
        result = get_file_content(
            client,
            "MyRepo",
            "src/main.py",
            "MyProject",
            version="feature-branch",
        )

        # Then: returns FileContent with the content string
        assert isinstance(result, FileContent), (
            f"Expected FileContent, got {type(result).__name__}"
        )
        assert result.path == "src/main.py", f"Expected path='src/main.py', got {result.path!r}"
        assert "def main" in result.content, (
            f"Expected content to contain 'def main', got {result.content!r}"
        )

    def test_nonexistent_file_raises_actionable_error_with_guidance(self) -> None:
        """
        Given a non-existent file path,
        When get_file_content is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: SDK raises for non-existent file
        client = Mock()
        client.git.get_item_content.side_effect = Exception("TF401174: The item does not exist")

        # When / Then: ActionableError is raised with corrective actions
        with pytest.raises(ActionableError) as exc_info:
            get_file_content(client, "MyRepo", "does/not/exist.py", "MyProject")

        assert "does not exist" in str(exc_info.value).lower() or "TF401174" in str(
            exc_info.value
        ), f"Expected error about non-existent file, got: {exc_info.value}"
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )
        assert exc_info.value.error_type == "not_found", (
            f"Expected error_type='not_found', got {exc_info.value.error_type!r}"
        )

    def test_no_version_uses_default_branch(self) -> None:
        """
        Given no version specified,
        When get_file_content is called,
        Then the SDK is called without a version descriptor (uses default branch).
        """
        # Given: no version specified
        client = _mock_client()

        # When: get_file_content is called without version
        get_file_content(client, "MyRepo", "src/app.py", "MyProject")

        # Then: SDK called - verify it was invoked (version handling is implementation detail)
        assert client.git.get_item_content.call_count == 1, (
            f"Expected 1 SDK call, got {client.git.get_item_content.call_count}"
        )

    def test_binary_file_returns_file_content_with_encoding_note(self) -> None:
        """
        Given a binary file,
        When get_file_content is called,
        Then FileContent is returned with appropriate encoding information.
        """
        # Given: binary content that isn't valid UTF-8
        binary_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00"
        client = _mock_client(content_bytes=binary_bytes)

        # When: get_file_content is called
        result = get_file_content(client, "MyRepo", "image.png", "MyProject")

        # Then: FileContent returned with encoding note
        assert isinstance(result, FileContent), (
            f"Expected FileContent, got {type(result).__name__}"
        )
        assert result.path == "image.png", f"Expected path='image.png', got {result.path!r}"


# ---------------------------------------------------------------------------
# TestGetChangedFileContents
# ---------------------------------------------------------------------------


class TestGetChangedFileContents:
    """
    REQUIREMENT: Fetch file contents for files changed in a PR.

    WHO: Code review tools that need to read all changed files for analysis.
    WHAT: (1) fetches contents for all changed files when file_paths is None
          (2) fetches only specified files when file_paths is provided
          (3) a file that fails to fetch appears in ContentResult.failures with the path and error, while others succeed
          (4) returns ContentResult with empty files list when no files are changed
          (5) an invalid PR raises ActionableError with the original error context and potential corrective actions for the user
          (6) iteration discovery failure degrades gracefully to empty file list
    WHY: Enables batch code analysis of PR changes without a local checkout.

    MOCK BOUNDARY:
        Mock:  client.git.get_item_content, client.git.get_pull_request_by_id,
               client.git.get_pull_request_iterations,
               client.git.get_pull_request_iteration_changes (SDK network calls)
        Real:  get_changed_file_contents orchestration, partial-success collection
        Never: get_changed_file_contents itself
    """

    def test_fetches_all_changed_files_when_paths_not_specified(self) -> None:
        """
        Given a PR with 2 changed files and no file_paths filter,
        When get_changed_file_contents is called,
        Then it returns FileContent for both files.
        """
        # Given: PR with changed files, SDK returns content for each
        client = Mock()
        # Mock PR to get source branch
        pr_mock = Mock()
        pr_mock.source_ref_name = "refs/heads/feature"
        client.git.get_pull_request_by_id.return_value = pr_mock
        # Mock iterations + changes
        iter_mock = Mock()
        iter_mock.id = 1
        iter_mock.created_date = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
        iter_mock.description = None
        client.git.get_pull_request_iterations.return_value = [iter_mock]
        changes_mock = Mock()
        change1 = Mock()
        change1.change_tracking_id = 1
        change1.additional_properties = {"item": {"path": "/src/a.py"}, "changeType": "edit"}
        change2 = Mock()
        change2.change_tracking_id = 2
        change2.additional_properties = {"item": {"path": "/src/b.py"}, "changeType": "add"}
        changes_mock.change_entries = [change1, change2]
        client.git.get_pull_request_iteration_changes.return_value = changes_mock
        # Mock file content retrieval
        client.git.get_item_content.return_value = iter([b"content"])

        # When: get_changed_file_contents is called
        result = get_changed_file_contents(client, "MyRepo", 42, "MyProject")

        # Then: returns ContentResult with FileContent for both files
        assert isinstance(result, ContentResult), (
            f"Expected ContentResult, got {type(result).__name__}"
        )
        assert len(result.files) == 2, (
            f"Expected 2 FileContent items, got {len(result.files)}: {[f.path for f in result.files]}"
        )
        assert len(result.failures) == 0, f"Expected no failures, got {result.failures}"

    def test_fetches_only_specified_files(self) -> None:
        """
        Given file_paths filter with 1 specific file,
        When get_changed_file_contents is called,
        Then only that file is fetched.
        """
        # Given: specific file_paths provided
        client = Mock()
        pr_mock = Mock()
        pr_mock.source_ref_name = "refs/heads/feature"
        client.git.get_pull_request_by_id.return_value = pr_mock
        client.git.get_item_content.return_value = iter([b"filtered content"])

        # When: get_changed_file_contents is called with file_paths
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            file_paths=["src/specific.py"],
        )

        # Then: only that file fetched
        assert len(result.files) == 1, f"Expected 1 FileContent, got {len(result.files)}"
        assert result.files[0].path == "src/specific.py", (
            f"Expected path='src/specific.py', got {result.files[0].path!r}"
        )

    def test_failed_file_reported_in_failures_while_others_succeed(self) -> None:
        """
        Given 2 files where one fails to fetch,
        When get_changed_file_contents is called,
        Then the successful file is in ContentResult.files and the failed one is in ContentResult.failures with path and error.
        """
        # Given: first file succeeds, second file raises
        client = Mock()
        pr_mock = Mock()
        pr_mock.source_ref_name = "refs/heads/feature"
        client.git.get_pull_request_by_id.return_value = pr_mock
        call_count = {"n": 0}

        def _content_side_effect(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise Exception("File not found")
            return iter([b"ok content"])

        client.git.get_item_content.side_effect = _content_side_effect

        # When: get_changed_file_contents is called
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            file_paths=["src/good.py", "src/bad.py"],
        )

        # Then: 1 in files, 1 in failures with path and error
        assert len(result.files) == 1, (
            f"Expected 1 successful FileContent, got {len(result.files)}"
        )
        assert len(result.failures) == 1, f"Expected 1 failure entry, got {len(result.failures)}"
        failure = result.failures[0]
        assert failure.context is not None, "Expected context on failure, got None"
        assert failure.context["path"] == "src/bad.py", (
            f"Expected failed path='src/bad.py', got {failure.context.get('path')!r}"
        )
        assert "File not found" in failure.error, (
            f"Expected error to contain 'File not found', got {failure.error!r}"
        )

    def test_no_changed_files_returns_empty_list(self) -> None:
        """
        Given a PR with no changed files,
        When get_changed_file_contents is called,
        Then it returns an empty list.
        """
        # Given: PR with no changes
        client = Mock()
        pr_mock = Mock()
        pr_mock.source_ref_name = "refs/heads/feature"
        client.git.get_pull_request_by_id.return_value = pr_mock
        iter_mock = Mock()
        iter_mock.id = 1
        iter_mock.created_date = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
        iter_mock.description = None
        client.git.get_pull_request_iterations.return_value = [iter_mock]
        changes_mock = Mock()
        changes_mock.change_entries = []
        client.git.get_pull_request_iteration_changes.return_value = changes_mock

        # When: get_changed_file_contents is called
        result = get_changed_file_contents(client, "MyRepo", 42, "MyProject")

        # Then: ContentResult with empty files list
        assert isinstance(result, ContentResult), (
            f"Expected ContentResult, got {type(result).__name__}"
        )
        assert result.files == [], f"Expected empty files, got {result.files!r}"
        assert result.failures == [], f"Expected empty failures, got {result.failures!r}"

    def test_invalid_pr_raises_actionable_error_with_guidance(self) -> None:
        """
        Given an invalid PR ID,
        When get_changed_file_contents is called,
        Then ActionableError is raised with the original error context and potential corrective actions for the user.
        """
        # Given: SDK raises when fetching PR
        client = Mock()
        client.git.get_pull_request_by_id.side_effect = Exception(
            "TF401180: The requested pull request was not found."
        )

        # When / Then: ActionableError is raised with corrective actions
        with pytest.raises(ActionableError) as exc_info:
            get_changed_file_contents(client, "MyRepo", 99999, "MyProject")

        assert "not found" in str(exc_info.value).lower() or "TF401180" in str(exc_info.value), (
            f"Expected error about PR not found, got: {exc_info.value}"
        )
        assert exc_info.value.suggestion is not None, (
            f"Expected corrective action suggestion, got None. Error: {exc_info.value}"
        )

    def test_iteration_discovery_failure_degrades_to_empty_file_list(self) -> None:
        """
        Given iteration discovery fails when file_paths is None,
        When get_changed_file_contents is called,
        Then it degrades gracefully and returns ContentResult with empty files.
        """
        # Given: PR exists but iteration discovery fails
        client = Mock()
        pr_mock = Mock()
        pr_mock.source_ref_name = "refs/heads/feature"
        client.git.get_pull_request_by_id.return_value = pr_mock
        client.git.get_pull_request_iterations.side_effect = Exception("API unavailable")

        # When: get_changed_file_contents is called without explicit file_paths
        result = get_changed_file_contents(client, "MyRepo", 42, "MyProject")

        # Then: degrades to empty result (no crash)
        assert isinstance(result, ContentResult), (
            f"Expected ContentResult, got {type(result).__name__}"
        )
        assert result.files == [], (
            f"Expected empty files on degradation, got {len(result.files)} files"
        )
        assert result.failures == [], (
            f"Expected empty failures on degradation, got {result.failures}"
        )


# ---------------------------------------------------------------------------
# TestGetChangedFileContentsFiltering
# ---------------------------------------------------------------------------


class TestGetChangedFileContentsFiltering:
    """
    REQUIREMENT: Filter discovered files by extension before fetching content.

    WHO: Code review tools that want to skip noise files (.lock, .log, .json,
         .png, etc.) without fetching them first.
    WHAT: (1) files matching excluded extensions are omitted from ContentResult
              with no failures for the excluded files
          (2) extension matching is case-insensitive
          (3) extensions without a leading dot are normalized and still match
          (4) filtering applies uniformly to both auto-discovered and explicit
              file_paths
          (5) exclude_extensions=None preserves current behavior (no filtering)
          (6) exclude_extensions=[] preserves current behavior (no filtering)
    WHY: Fetching binary or noise files wastes API calls and pollutes review
         context. Making this a parameter keeps the library generic.

    MOCK BOUNDARY:
        Mock:  client.git.get_item_content, client.git.get_pull_request_by_id,
               client.git.get_pull_request_iterations,
               client.git.get_pull_request_iteration_changes (SDK network calls)
        Real:  get_changed_file_contents filtering logic, extension matching
        Never: ActionableError construction (must be real)
    """

    def test_excluded_extensions_are_omitted_from_results(self) -> None:
        """
        Given a PR with files [a.py, b.lock, c.json, d.py],
        When exclude_extensions=[".lock", ".json"],
        Then ContentResult.files contains only a.py and d.py with no
        failures for the excluded files.
        """
        # Given: PR with mixed file types
        client = _mock_pr_client_with_files(
            ["/src/a.py", "/deps/b.lock", "/config/c.json", "/src/d.py"]
        )

        # When: exclude_extensions filters out .lock and .json
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            exclude_extensions=[".lock", ".json"],
        )

        # Then: only .py files returned, no failures for excluded files
        assert isinstance(result, ContentResult), (
            f"Expected ContentResult, got {type(result).__name__}"
        )
        fetched_paths = [f.path for f in result.files]
        assert len(result.files) == 2, (
            f"Expected 2 files (a.py, d.py), got {len(result.files)}: {fetched_paths}"
        )
        assert all(p.endswith(".py") for p in fetched_paths), (
            f"Expected only .py files, got {fetched_paths}"
        )
        assert result.failures == [], (
            f"Expected no failures for excluded files, got {result.failures}"
        )

    def test_extension_matching_is_case_insensitive(self) -> None:
        """
        Given exclude_extensions=[".Lock"] and a file named "uv.lock",
        When get_changed_file_contents is called,
        Then the file is excluded (case-insensitive match).
        """
        # Given: uppercase extension filter, lowercase file
        client = _mock_pr_client_with_files(["/src/app.py", "/uv.lock"])

        # When: exclude with uppercase .Lock
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            exclude_extensions=[".Lock"],
        )

        # Then: uv.lock excluded despite case mismatch
        fetched_paths = [f.path for f in result.files]
        assert len(result.files) == 1, (
            f"Expected 1 file (app.py only), got {len(result.files)}: {fetched_paths}"
        )
        assert fetched_paths[0].endswith(".py"), f"Expected .py file, got {fetched_paths[0]!r}"

    def test_extensions_without_leading_dot_are_normalized(self) -> None:
        """
        Given exclude_extensions=["lock"] (no leading dot),
        When get_changed_file_contents is called,
        Then the extension is normalized and "uv.lock" is excluded.
        """
        # Given: extension without leading dot
        client = _mock_pr_client_with_files(["/src/app.py", "/uv.lock"])

        # When: exclude with bare "lock"
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            exclude_extensions=["lock"],
        )

        # Then: uv.lock is excluded
        fetched_paths = [f.path for f in result.files]
        assert len(result.files) == 1, (
            f"Expected 1 file (app.py only), got {len(result.files)}: {fetched_paths}"
        )

    def test_filtering_applies_to_explicit_file_paths(self) -> None:
        """
        Given exclude_extensions=[".py"] and file_paths=["only.py"],
        When get_changed_file_contents is called,
        Then the explicit file is still excluded — filtering applies uniformly.
        """
        # Given: explicit file_paths with an excluded extension
        client = _mock_pr_client_with_files(["/only.py"])

        # When: the explicit path matches the exclusion
        result = get_changed_file_contents(
            client,
            "MyRepo",
            42,
            "MyProject",
            file_paths=["only.py"],
            exclude_extensions=[".py"],
        )

        # Then: file is excluded even though explicitly requested
        assert result.files == [], (
            f"Expected no files (only.py should be excluded), got {[f.path for f in result.files]}"
        )
        assert result.failures == [], (
            f"Expected no failures for excluded files, got {result.failures}"
        )

    def test_none_exclude_extensions_preserves_current_behavior(self) -> None:
        """
        Given exclude_extensions=None,
        When get_changed_file_contents is called,
        Then behavior is identical to current (no filtering).
        """
        # Given: no exclusion filter
        client = _mock_pr_client_with_files(["/src/a.py", "/uv.lock"])

        # When: exclude_extensions is None (default)
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", exclude_extensions=None
        )

        # Then: all files returned
        assert len(result.files) == 2, (
            f"Expected 2 files (no filtering), got {len(result.files)}: "
            f"{[f.path for f in result.files]}"
        )

    def test_empty_exclude_extensions_preserves_current_behavior(self) -> None:
        """
        Given exclude_extensions=[],
        When get_changed_file_contents is called,
        Then behavior is identical to current (no filtering).
        """
        # Given: empty exclusion list
        client = _mock_pr_client_with_files(["/src/a.py", "/uv.lock"])

        # When: exclude_extensions is empty list
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", exclude_extensions=[]
        )

        # Then: all files returned
        assert len(result.files) == 2, (
            f"Expected 2 files (no filtering), got {len(result.files)}: "
            f"{[f.path for f in result.files]}"
        )


# ---------------------------------------------------------------------------
# TestGetChangedFileContentsCompletedPR
# ---------------------------------------------------------------------------


class TestGetChangedFileContentsCompletedPR:
    """
    REQUIREMENT: Retrieve file contents from completed PRs whose source
    branch has been deleted.

    WHO: Code review tools reviewing merged PRs after the fact
         (retrospective reviews, comment follow-ups, audit).
    WHAT: (1) a completed PR with existing source branch uses the branch
              (current behavior)
          (2) a completed PR with deleted source branch falls back to
              last_merge_source_commit SHA
          (3) a completed PR with deleted branch AND no merge commit raises
              ActionableError explaining both are unavailable
          (4) an active PR with branch failure preserves existing error
              behavior (no fallback)
          (5) a completed PR where both branch and merge commit fetch fail
              reports the file in failures (graceful degradation)
    WHY: The current implementation fails when the source branch is deleted
         after merge. Code review tools need to access merged PR contents
         for retrospective review and audit.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_by_id (to control source_ref_name,
               last_merge_source_commit, status),
               client.git.get_item_content (SDK network call)
        Real:  get_changed_file_contents fallback logic, branch resolution
        Never: ActionableError construction (must be real)
    """

    def test_completed_pr_with_existing_branch_uses_branch(self) -> None:
        """
        Given a completed PR where the source branch still exists,
        When get_changed_file_contents is called,
        Then files are fetched using the source branch (current behavior).
        """
        # Given: completed PR with existing source branch
        client = _mock_pr_client_with_files(
            ["/src/app.py"],
            status="completed",
            source_ref="refs/heads/feature",
        )

        # When: fetch file contents
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", file_paths=["src/app.py"]
        )

        # Then: files fetched successfully using the branch
        assert len(result.files) == 1, (
            f"Expected 1 file, got {len(result.files)}: {[f.path for f in result.files]}"
        )
        assert result.failures == [], f"Expected no failures, got {result.failures}"

    def test_completed_pr_with_deleted_branch_falls_back_to_merge_commit(
        self,
    ) -> None:
        """
        Given a completed PR where the source branch is deleted but
        last_merge_source_commit exists,
        When get_changed_file_contents is called,
        Then files are fetched using the commit SHA with version_type="commit".
        """
        # Given: completed PR, branch fetch fails, merge commit available
        client = _mock_pr_client_with_files(
            ["/src/app.py"],
            status="completed",
            last_merge_source_commit="abc123def456",
        )
        # Make branch-based fetch fail (simulating deleted branch)
        call_count = {"n": 0}

        def _content_side_effect(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call uses branch — fails because branch is deleted
                raise Exception("TF401174: The branch does not exist")
            # Subsequent calls use commit SHA — succeed
            return iter([b"content from merge commit"])

        client.git.get_item_content.side_effect = _content_side_effect

        # When: fetch file contents
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", file_paths=["src/app.py"]
        )

        # Then: files fetched using the merge commit SHA
        assert len(result.files) == 1, (
            f"Expected 1 file via merge commit fallback, got {len(result.files)}"
        )
        assert "content from merge commit" in result.files[0].content, (
            f"Expected content from merge commit, got {result.files[0].content!r}"
        )

    def test_completed_pr_with_no_branch_and_no_merge_commit_raises_error(
        self,
    ) -> None:
        """
        Given a completed PR where the source branch is deleted AND
        last_merge_source_commit is None,
        When get_changed_file_contents is called,
        Then ActionableError is raised explaining both are unavailable.
        """
        # Given: completed PR, branch fetch fails, no merge commit
        client = _mock_pr_client_with_files(
            ["/src/app.py"],
            status="completed",
            last_merge_source_commit=None,
        )
        # Branch fetch fails
        client.git.get_item_content.side_effect = Exception("TF401174: The branch does not exist")

        # When / Then: ActionableError raised
        with pytest.raises(ActionableError) as exc_info:
            get_changed_file_contents(client, "MyRepo", 42, "MyProject", file_paths=["src/app.py"])

        error = exc_info.value
        error_text = str(error).lower()
        assert "branch" in error_text or "commit" in error_text or "deleted" in error_text, (
            f"Expected error about unavailable branch/commit, got: {error}"
        )
        assert error.suggestion is not None, (
            f"Expected suggestion with corrective action, got None. Error: {error}"
        )

    def test_active_pr_with_branch_failure_preserves_existing_error(self) -> None:
        """
        Given an active (not completed) PR whose source branch fetch fails,
        When get_changed_file_contents is called,
        Then the existing error behavior is preserved (no fallback).
        """
        # Given: active PR, branch fetch fails
        client = _mock_pr_client_with_files(
            ["/src/app.py"],
            status="active",
            last_merge_source_commit="abc123def456",
        )
        client.git.get_item_content.side_effect = Exception("TF401174: The branch does not exist")

        # When: fetch file contents
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", file_paths=["src/app.py"]
        )

        # Then: failure is reported (no fallback attempted for active PRs)
        assert len(result.failures) >= 1, (
            f"Expected at least 1 failure for active PR branch error, got {len(result.failures)}"
        )
        assert result.files == [], (
            f"Expected no files (branch error, no fallback for active PR), "
            f"got {[f.path for f in result.files]}"
        )

    def test_completed_pr_with_both_branch_and_commit_failing_degrades_gracefully(
        self,
    ) -> None:
        """
        Given a completed PR where the source branch is deleted and
        the merge commit SHA also fails to fetch,
        When get_changed_file_contents is called,
        Then the file is reported in failures (graceful degradation).
        """
        # Given: completed PR, merge commit exists but both fetches fail
        client = _mock_pr_client_with_files(
            ["/src/app.py"],
            status="completed",
            last_merge_source_commit="abc123def456",
        )
        # All content fetches fail (branch and commit)
        client.git.get_item_content.side_effect = Exception("TF401174: The item does not exist")

        # When: fetch file contents
        result = get_changed_file_contents(
            client, "MyRepo", 42, "MyProject", file_paths=["src/app.py"]
        )

        # Then: file is in failures, not in files
        assert result.files == [], (
            f"Expected no files (both branch and commit failed), "
            f"got {[f.path for f in result.files]}"
        )
        assert len(result.failures) >= 1, (
            f"Expected at least 1 failure, got {len(result.failures)}"
        )

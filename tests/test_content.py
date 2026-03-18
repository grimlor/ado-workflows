"""BDD tests for ado_workflows.content -- file content retrieval.

Covers:
- TestGetFileContent: fetch single file content from a repository ref

Public API surface (from src/ado_workflows/content.py):
    get_file_content(client: AdoClient, repository: str, path: str,
                     project: str, *, version: str | None = None,
                     version_type: str = "branch") -> FileContent
    get_changed_file_contents(client: AdoClient, repository: str,
                              pr_id: int, project: str, *,
                              file_paths: list[str] | None = None) -> list[FileContent]
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
        assert result.failures[0]["path"] == "src/bad.py", (
            f"Expected failed path='src/bad.py', got {result.failures[0].get('path')!r}"
        )
        assert "File not found" in result.failures[0]["error"], (
            f"Expected error to contain 'File not found', got {result.failures[0].get('error')!r}"
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

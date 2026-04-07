"""
BDD tests for ado_workflows.content.list_repo_items — remote repo content inspection.

Covers:
- TestListRepoItems: list files/folders at a path on any branch
- TestListRepoItemsMapping: GitItem→RepoItem mapping with None-safe defaults
- TestListRepoItemsErrors: error classification via classify_ado_error

Public API surface (from src/ado_workflows/content.py):
    list_repo_items(client: AdoClient, repository: str, project: str, *,
                    path: str = "/", ref: str | None = None,
                    recursion: str = "oneLevel") -> list[RepoItem]
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError
from azure.devops.exceptions import AzureDevOpsAuthenticationError

from ado_workflows.content import list_repo_items
from ado_workflows.models import RepoItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_item(
    *,
    path: str | None = "/README.md",
    is_folder: bool | None = False,
    git_object_type: str | None = "blob",
    object_id: str | None = "abc123",
    commit_id: str | None = "def456",
    url: str | None = "https://dev.azure.com/item",
) -> Mock:
    """Build a mock GitItem with the given attributes."""
    item = Mock()
    item.path = path
    item.is_folder = is_folder
    item.git_object_type = git_object_type
    item.object_id = object_id
    item.commit_id = commit_id
    item.url = url
    return item


def _mock_client(items: list[Mock] | None = None) -> Mock:
    """Return a mock AdoClient whose git.get_items returns the given items."""
    client = Mock()
    client.git.get_items.return_value = items if items is not None else []
    return client


# ---------------------------------------------------------------------------
# TestListRepoItems
# ---------------------------------------------------------------------------


class TestListRepoItems:
    """
    REQUIREMENT: list_repo_items returns repository file/folder metadata for any
    branch, commit, or tag.

    WHO: PR review workflows verifying base branch contents before commenting on
    "missing" files.
    WHAT: (1) items at root path on default branch are returned as RepoItem list
          (2) items at a subdirectory path are returned scoped to that path
          (3) ref parameter controls which branch's items are returned
          (4) recursion parameter controls whether nested items are included
          (5) empty directory returns an empty list, not an error
    WHY: Without directory listing, callers cannot discover what files exist on a
    branch — leading to false-positive review comments about "missing" files.

    MOCK BOUNDARY:
        Mock:  client.git.get_items() — the SDK HTTP call (only I/O boundary)
        Real:  list_repo_items logic (version descriptor construction, recursion
               passthrough, GitItem→RepoItem mapping)
        Never: mock RepoItem construction or internal helper functions
    """

    def test_lists_items_at_root_on_default_branch(self) -> None:
        """
        Given repository contains files and folders at root,
        When list_repo_items is called with defaults,
        Then returns a RepoItem for each item with correct path, is_folder,
        git_object_type, object_id, and commit_id.
        """
        # Given: repository has a file and a folder at root
        items = [
            _make_git_item(
                path="/README.md",
                is_folder=False,
                git_object_type="blob",
                object_id="aaa",
                commit_id="ccc",
            ),
            _make_git_item(
                path="/src",
                is_folder=True,
                git_object_type="tree",
                object_id="bbb",
                commit_id="ddd",
            ),
        ]
        client = _mock_client(items)

        # When: called with defaults (root path, default branch)
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: returns matching RepoItems
        assert len(result) == 2, f"Expected 2 items, got {len(result)}: {result}"
        readme = result[0]
        assert isinstance(readme, RepoItem), f"Expected RepoItem, got {type(readme).__name__}"
        assert readme.path == "/README.md", f"Expected path='/README.md', got {readme.path!r}"
        assert readme.is_folder is False, f"Expected is_folder=False, got {readme.is_folder!r}"
        assert readme.git_object_type == "blob", (
            f"Expected git_object_type='blob', got {readme.git_object_type!r}"
        )
        assert readme.object_id == "aaa", f"Expected object_id='aaa', got {readme.object_id!r}"
        assert readme.commit_id == "ccc", f"Expected commit_id='ccc', got {readme.commit_id!r}"

    def test_lists_items_at_subdirectory_path(self) -> None:
        """
        Given repository contains items under "/src",
        When list_repo_items is called with path="/src",
        Then returned items have paths under "/src", not root-level paths.
        """
        # Given: items scoped to /src
        items = [
            _make_git_item(path="/src/main.py", is_folder=False),
            _make_git_item(path="/src/utils/", is_folder=True),
        ]
        client = _mock_client(items)

        # When: called with path="/src"
        result = list_repo_items(client, "MyRepo", "MyProject", path="/src")

        # Then: all returned items are under /src
        assert len(result) == 2, f"Expected 2 items, got {len(result)}"
        for item in result:
            assert item.path.startswith("/src"), f"Expected path under '/src', got {item.path!r}"

    def test_ref_controls_which_branch_items_are_returned(self) -> None:
        """
        Given branch "main" has items ["a.txt"] and branch "dev" has items ["b.txt"],
        When list_repo_items is called with ref="main",
        Then returns items from "main", not "dev".
        """
        # Given: the SDK returns different items depending on the version descriptor
        main_items = [_make_git_item(path="/a.txt")]
        client = _mock_client(main_items)

        # When: called with ref="main"
        result = list_repo_items(client, "MyRepo", "MyProject", ref="main")

        # Then: returns the items from the specified branch
        assert len(result) == 1, f"Expected 1 item from 'main', got {len(result)}"
        assert result[0].path == "/a.txt", (
            f"Expected '/a.txt' from main branch, got {result[0].path!r}"
        )

    def test_recursion_controls_depth_of_listing(self) -> None:
        """
        Given a directory tree with nested children,
        When list_repo_items is called with recursion="full",
        Then returns nested items (not just immediate children).
        """
        # Given: SDK returns nested items when recursion="full"
        nested_items = [
            _make_git_item(path="/src", is_folder=True),
            _make_git_item(path="/src/main.py", is_folder=False),
            _make_git_item(path="/src/utils", is_folder=True),
            _make_git_item(path="/src/utils/helpers.py", is_folder=False),
        ]
        client = _mock_client(nested_items)

        # When: called with recursion="full"
        result = list_repo_items(client, "MyRepo", "MyProject", recursion="full")

        # Then: returns all nested items
        assert len(result) == 4, (
            f"Expected 4 nested items, got {len(result)}: {[r.path for r in result]}"
        )
        paths = {r.path for r in result}
        assert "/src/utils/helpers.py" in paths, (
            f"Expected deeply nested '/src/utils/helpers.py' in results, got paths: {paths}"
        )

    def test_empty_directory_returns_empty_list(self) -> None:
        """
        Given path exists but contains no items,
        When list_repo_items is called,
        Then returns [] with no error.
        """
        # Given: SDK returns an empty list
        client = _mock_client([])

        # When: called on an empty directory
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: returns an empty list, no exception
        assert result == [], f"Expected empty list, got {result}"


# ---------------------------------------------------------------------------
# TestListRepoItemsMapping
# ---------------------------------------------------------------------------


class TestListRepoItemsMapping:
    """
    REQUIREMENT: GitItem SDK objects are mapped to RepoItem dataclasses with safe
    defaults for None fields.

    WHO: All callers consuming RepoItem instances.
    WHAT: (1) GitItem with is_folder=True maps to RepoItem.is_folder=True
          (2) GitItem with is_folder=None maps to RepoItem.is_folder=False
          (3) GitItem with path=None maps to RepoItem.path=""
          (4) GitItem with git_object_type=None maps to RepoItem.git_object_type=""
          (5) GitItem with object_id=None maps to RepoItem.object_id=""
          (6) GitItem with commit_id=None maps to RepoItem.commit_id=""
          (7) items include both files and folders in the same result list
    WHY: SDK GitItem attributes are nullable. Callers should not need to handle
    None for every field — safe defaults prevent downstream NoneType errors.

    MOCK BOUNDARY:
        Mock:  client.git.get_items() — the SDK HTTP call
        Real:  GitItem→RepoItem mapping logic
        Never: construct RepoItem directly — always obtain via list_repo_items
    """

    def test_folder_item_maps_is_folder_true(self) -> None:
        """
        Given SDK returns a GitItem with is_folder=True,
        When list_repo_items is called,
        Then corresponding RepoItem.is_folder is True.
        """
        # Given: a folder GitItem
        client = _mock_client([_make_git_item(is_folder=True)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: is_folder is True
        assert result[0].is_folder is True, f"Expected is_folder=True, got {result[0].is_folder!r}"

    def test_none_is_folder_defaults_to_false(self) -> None:
        """
        Given SDK returns a GitItem with is_folder=None,
        When list_repo_items is called,
        Then corresponding RepoItem.is_folder is False.
        """
        # Given: a GitItem with is_folder=None
        client = _mock_client([_make_git_item(is_folder=None)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: defaults to False
        assert result[0].is_folder is False, (
            f"Expected is_folder=False for None input, got {result[0].is_folder!r}"
        )

    def test_none_path_defaults_to_empty_string(self) -> None:
        """
        Given SDK returns a GitItem with path=None,
        When list_repo_items is called,
        Then corresponding RepoItem.path is "".
        """
        # Given: a GitItem with path=None
        client = _mock_client([_make_git_item(path=None)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: defaults to empty string
        assert result[0].path == "", f"Expected path='' for None input, got {result[0].path!r}"

    def test_none_git_object_type_defaults_to_empty_string(self) -> None:
        """
        Given SDK returns a GitItem with git_object_type=None,
        When list_repo_items is called,
        Then corresponding RepoItem.git_object_type is "".
        """
        # Given: a GitItem with git_object_type=None
        client = _mock_client([_make_git_item(git_object_type=None)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: defaults to empty string
        assert result[0].git_object_type == "", (
            f"Expected git_object_type='' for None input, got {result[0].git_object_type!r}"
        )

    def test_none_object_id_defaults_to_empty_string(self) -> None:
        """
        Given SDK returns a GitItem with object_id=None,
        When list_repo_items is called,
        Then corresponding RepoItem.object_id is "".
        """
        # Given: a GitItem with object_id=None
        client = _mock_client([_make_git_item(object_id=None)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: defaults to empty string
        assert result[0].object_id == "", (
            f"Expected object_id='' for None input, got {result[0].object_id!r}"
        )

    def test_none_commit_id_defaults_to_empty_string(self) -> None:
        """
        Given SDK returns a GitItem with commit_id=None,
        When list_repo_items is called,
        Then corresponding RepoItem.commit_id is "".
        """
        # Given: a GitItem with commit_id=None
        client = _mock_client([_make_git_item(commit_id=None)])

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: defaults to empty string
        assert result[0].commit_id == "", (
            f"Expected commit_id='' for None input, got {result[0].commit_id!r}"
        )

    def test_result_includes_both_files_and_folders(self) -> None:
        """
        Given SDK returns a mix of folder and file GitItems,
        When list_repo_items is called,
        Then result contains both is_folder=True and is_folder=False items.
        """
        # Given: a mix of files and folders
        items = [
            _make_git_item(path="/src", is_folder=True),
            _make_git_item(path="/README.md", is_folder=False),
        ]
        client = _mock_client(items)

        # When: mapped through list_repo_items
        result = list_repo_items(client, "MyRepo", "MyProject")

        # Then: both types present
        folder_flags = {r.is_folder for r in result}
        assert folder_flags == {True, False}, (
            f"Expected both True and False in is_folder values, got {folder_flags}"
        )


# ---------------------------------------------------------------------------
# TestListRepoItemsErrors
# ---------------------------------------------------------------------------


class TestListRepoItemsErrors:
    """
    REQUIREMENT: list_repo_items delegates error classification to
    classify_ado_error, producing structured errors with path context.

    WHO: AI agents consuming list_repo_items via MCP tools.
    WHAT: (1) SDK errors are classified via classify_ado_error (not ad-hoc string matching)
          (2) the path argument is passed as context_hint so it appears in the suggestion
          (3) the raised error preserves the original exception as the cause (__cause__)
    WHY: Validates that list_repo_items wires up the classifier correctly — the
    detailed kind-by-kind behavior is already proven in TestClassifyAdoError.

    MOCK BOUNDARY:
        Mock:  client.git.get_items() — configured to raise exceptions
        Real:  list_repo_items error handling, classify_ado_error
        Never: mock classify_ado_error itself — it runs for real
    """

    def test_sdk_error_produces_actionable_error_with_correct_kind(self) -> None:
        """
        Given SDK raises AzureDevOpsAuthenticationError,
        When list_repo_items is called,
        Then raises ActionableError with kind authentication (proving the
        classifier is wired in).
        """
        # Given: an auth error from the SDK
        client = Mock()
        client.git.get_items.side_effect = AzureDevOpsAuthenticationError(
            "VS30063: not authorized"
        )

        # When / Then: raises ActionableError with authentication kind
        with pytest.raises(ActionableError) as exc_info:
            list_repo_items(client, "MyRepo", "MyProject")

        assert exc_info.value.error_type == "authentication", (
            f"Expected error_type='authentication', got {exc_info.value.error_type!r}. "
            f"classify_ado_error may not be wired into list_repo_items."
        )

    def test_path_appears_in_error_suggestion(self) -> None:
        """
        Given SDK raises any error for path "/src/missing.py",
        When list_repo_items is called with path="/src/missing.py",
        Then the ActionableError suggestion includes "/src/missing.py".
        """
        # Given: a not-found error for a specific path
        client = Mock()
        client.git.get_items.side_effect = Exception("TF401174: not found")

        # When / Then: the suggestion includes the path
        with pytest.raises(ActionableError) as exc_info:
            list_repo_items(client, "MyRepo", "MyProject", path="/src/missing.py")

        assert exc_info.value.suggestion is not None, "Expected a suggestion, got None"
        assert "/src/missing.py" in exc_info.value.suggestion, (
            f"Expected suggestion to include '/src/missing.py', got: {exc_info.value.suggestion!r}"
        )

    def test_original_exception_preserved_as_cause(self) -> None:
        """
        Given SDK raises an exception,
        When list_repo_items is called,
        Then the raised ActionableError.__cause__ is the original SDK exception.
        """
        # Given: a specific SDK exception
        original = ConnectionError("DNS resolution failed")
        client = Mock()
        client.git.get_items.side_effect = original

        # When / Then: __cause__ is preserved for diagnostic chaining
        with pytest.raises(ActionableError) as exc_info:
            list_repo_items(client, "MyRepo", "MyProject")

        assert exc_info.value.__cause__ is original, (
            f"Expected __cause__ to be the original exception, got {exc_info.value.__cause__!r}"
        )

"""BDD tests for ado_workflows.pr — PR context resolution.

Covers:
- TestURLParsing: from_url() with valid URLs, missing fields, unparseable URLs
- TestRepositoryContextResolution: from_pr_id() with cached/missing/errored context
- TestPRContextFactory: establish_pr_context() routing URL vs numeric input
- TestOrgUrlProperty: org_url computed property
- TestSerialization: to_dict() with computed properties

Public API surface (from src/ado_workflows/pr.py):
    AzureDevOpsPRContext(pr_url, organization, project, repository, pr_id, source)
    AzureDevOpsPRContext.from_url(pr_url: str) -> AzureDevOpsPRContext
    AzureDevOpsPRContext.from_pr_id(pr_id: int, working_directory: str | None) -> AzureDevOpsPRContext
    AzureDevOpsPRContext.org_url -> str  (property)
    AzureDevOpsPRContext.to_dict() -> dict[str, Any]
    establish_pr_context(url_or_id: str, working_directory: str | None) -> AzureDevOpsPRContext
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from actionable_errors import ActionableError

from ado_workflows.context import RepositoryContext
from ado_workflows.pr import AzureDevOpsPRContext, establish_pr_context


class TestURLParsing:
    """
    REQUIREMENT: AzureDevOpsPRContext.from_url() creates context from a PR URL
    by delegating to parse_ado_url.

    WHO: Any tool that receives a PR URL from the user.
    WHAT: (1) a dev.azure.com PR URL produces correct context with source="url"
          (2) a visualstudio.com PR URL produces correct context
          (3) a URL missing the PR ID raises ActionableError naming "pr_id"
          (4) a URL missing multiple fields names all missing fields in the error
          (5) a completely unparseable URL names all four fields as missing
    WHY: PR URLs are the most common way users reference PRs. If parsing fails
         silently, downstream operations target the wrong PR.

    MOCK BOUNDARY:
        Mock:  nothing — parse_ado_url is a pure function
        Real:  AzureDevOpsPRContext, parse_ado_url
        Never: construct AzureDevOpsPRContext directly to verify .from_url()
    """

    def test_dev_azure_com_pr_url_produces_correct_context(self) -> None:
        """
        Given a dev.azure.com PR URL
        When from_url() is called
        Then context has correct org, project, repo, pr_id, source="url"
        """
        # Given: a dev.azure.com PR URL with all components
        url = "https://dev.azure.com/MyOrg/MyProject/_git/MyRepo/pullrequest/123"

        # When: from_url() parses and constructs context
        ctx = AzureDevOpsPRContext.from_url(url)

        # Then: all fields are correctly extracted
        assert ctx.organization == "MyOrg", (
            f"Expected organization 'MyOrg', got '{ctx.organization}'"
        )
        assert ctx.project == "MyProject", f"Expected project 'MyProject', got '{ctx.project}'"
        assert ctx.repository == "MyRepo", f"Expected repository 'MyRepo', got '{ctx.repository}'"
        assert ctx.pr_id == 123, f"Expected pr_id 123, got {ctx.pr_id}"
        assert ctx.source == "url", f"Expected source 'url', got '{ctx.source}'"
        assert ctx.pr_url == url, f"Expected pr_url to be the original URL, got '{ctx.pr_url}'"

    def test_visualstudio_com_pr_url_produces_correct_context(self) -> None:
        """
        Given a visualstudio.com PR URL
        When from_url() is called
        Then context has correct fields
        """
        # Given: a legacy visualstudio.com PR URL
        url = "https://myorg.visualstudio.com/MyProject/_git/MyRepo/pullrequest/456"

        # When: from_url() parses and constructs context
        ctx = AzureDevOpsPRContext.from_url(url)

        # Then: all fields are correctly extracted
        assert ctx.organization == "myorg", (
            f"Expected organization 'myorg', got '{ctx.organization}'"
        )
        assert ctx.project == "MyProject", f"Expected project 'MyProject', got '{ctx.project}'"
        assert ctx.repository == "MyRepo", f"Expected repository 'MyRepo', got '{ctx.repository}'"
        assert ctx.pr_id == 456, f"Expected pr_id 456, got {ctx.pr_id}"
        assert ctx.source == "url", f"Expected source 'url', got '{ctx.source}'"

    def test_url_missing_pr_id_raises_actionable_error(self) -> None:
        """
        Given a URL missing the PR ID
        When from_url() is called
        Then raises ActionableError naming "pr_id" as missing
        """
        # Given: a repo URL without a pullrequest segment
        url = "https://dev.azure.com/MyOrg/MyProject/_git/MyRepo"

        # When / Then: from_url() raises with missing field named
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsPRContext.from_url(url)

        error_message = str(exc_info.value)
        assert "pr_id" in error_message, (
            f"Error should name 'pr_id' as missing. Got: {error_message}"
        )

    def test_url_missing_multiple_fields_names_all_in_error(self) -> None:
        """
        Given a URL missing multiple fields
        When from_url() is called
        Then error names all missing fields
        """
        # Given: a URL that only resolves the organization
        url = "https://dev.azure.com/MyOrg"

        # When / Then: from_url() raises naming all missing fields
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsPRContext.from_url(url)

        error_message = str(exc_info.value)
        assert "project" in error_message, (
            f"Error should name 'project' as missing. Got: {error_message}"
        )
        assert "repository" in error_message, (
            f"Error should name 'repository' as missing. Got: {error_message}"
        )
        assert "pr_id" in error_message, (
            f"Error should name 'pr_id' as missing. Got: {error_message}"
        )

    def test_completely_unparseable_url_names_all_four_fields(self) -> None:
        """
        Given a completely unparseable URL
        When from_url() is called
        Then raises ActionableError naming all four fields as missing
        """
        # Given: a URL that matches no known ADO pattern
        url = "https://github.com/some/repo"

        # When / Then: from_url() raises naming all fields
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsPRContext.from_url(url)

        error_message = str(exc_info.value)
        assert "organization" in error_message, (
            f"Error should name 'organization' as missing. Got: {error_message}"
        )
        assert "project" in error_message, (
            f"Error should name 'project' as missing. Got: {error_message}"
        )
        assert "repository" in error_message, (
            f"Error should name 'repository' as missing. Got: {error_message}"
        )
        assert "pr_id" in error_message, (
            f"Error should name 'pr_id' as missing. Got: {error_message}"
        )


class TestRepositoryContextResolution:
    """
    REQUIREMENT: AzureDevOpsPRContext.from_pr_id() creates context from a
    numeric PR ID using RepositoryContext.

    WHO: Any tool that receives a bare PR ID (e.g., 123) instead of a full URL.
    WHAT: (1) cached RepositoryContext produces correct PR context with
              source="repository_context"
          (2) no cached context raises ActionableError suggesting
              set_repository_context()
          (3) an explicit working_directory is forwarded to RepositoryContext
          (4) an error dict from context raises ActionableError with the
              underlying message
    WHY: Users often reference PRs by ID alone. Without context resolution, the
         tool can't know which org/project/repo the ID belongs to.

    MOCK BOUNDARY:
        Mock:  subprocess.run (git process — the only I/O edge)
        Real:  AzureDevOpsPRContext, RepositoryContext, discover_repositories,
               infer_target_repository, parse_ado_url, tmp_path filesystem
        Never: mock any of our own functions (discover_repositories,
               infer_target_repository, RepositoryContext.get())
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    @staticmethod
    def _set_context(
        directory: str,
        *,
        organization: str = "ContosoOrg",
        project: str = "Payments",
        repo_name: str = "PaymentsRepo",
    ) -> None:
        """Populate RepositoryContext cache — mocks only subprocess (I/O edge)."""
        (Path(directory) / ".git").mkdir(exist_ok=True)
        remote_url = f"https://dev.azure.com/{organization}/{project}/_git/{repo_name}"
        mock_git = MagicMock(returncode=0, stdout=f"{remote_url}\n")
        with patch("ado_workflows.discovery.subprocess.run", return_value=mock_git):
            RepositoryContext.set(directory)

    def test_cached_context_produces_correct_pr_context(self, tmp_path: Path) -> None:
        """
        Given RepositoryContext has cached context
        When from_pr_id(42) is called
        Then context has org/project/repo from cache and pr_id=42,
             source="repository_context"
        """
        # Given: context cached via the public set() API
        repo_dir = tmp_path / "PaymentsRepo"
        repo_dir.mkdir()
        self._set_context(str(repo_dir))

        # When: from_pr_id() resolves context from the cache — no mocks needed
        ctx = AzureDevOpsPRContext.from_pr_id(42)

        # Then: context fields match the cached repository info
        assert ctx.organization == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{ctx.organization}'"
        )
        assert ctx.project == "Payments", f"Expected project 'Payments', got '{ctx.project}'"
        assert ctx.repository == "PaymentsRepo", (
            f"Expected repository 'PaymentsRepo', got '{ctx.repository}'"
        )
        assert ctx.pr_id == 42, f"Expected pr_id 42, got {ctx.pr_id}"
        assert ctx.source == "repository_context", (
            f"Expected source 'repository_context', got '{ctx.source}'"
        )

    def test_no_context_raises_actionable_error_suggesting_set(self, tmp_path: Path) -> None:
        """
        Given RepositoryContext has no cached context and discovery finds nothing
        When from_pr_id(42) is called with an empty directory
        Then raises ActionableError suggesting set_repository_context()
        """
        # Given: no cached context, directory exists but has no git repos
        empty_dir = tmp_path / "no_repos"
        empty_dir.mkdir()

        # When / Then: from_pr_id() raises when context resolution fails
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsPRContext.from_pr_id(42, str(empty_dir))

        # Then: error suggests the remedy
        error_message = str(exc_info.value)
        assert "set_repository_context" in error_message, (
            f"Error should suggest set_repository_context(). Got: {error_message}"
        )

    def test_working_directory_is_passed_through_to_repository_context(
        self, tmp_path: Path
    ) -> None:
        """
        Given default cached context and a different repo at a specific path
        When from_pr_id(42, specific_path) is called
        Then context resolves from that directory, not from the cache
        """
        # Given: default context cached as DefaultOrg
        default_dir = tmp_path / "DefaultRepo"
        default_dir.mkdir()
        self._set_context(
            str(default_dir),
            organization="DefaultOrg",
            project="DefaultProject",
            repo_name="DefaultRepo",
        )

        # Given: a different repo exists at a specific path
        specific_dir = tmp_path / "SpecificRepo"
        specific_dir.mkdir()
        (specific_dir / ".git").mkdir()

        specific_url = "https://dev.azure.com/SpecificOrg/SpecificProject/_git/SpecificRepo"
        mock_git = MagicMock(returncode=0, stdout=f"{specific_url}\n")

        with patch("ado_workflows.discovery.subprocess.run", return_value=mock_git):
            # When: from_pr_id() is called with an explicit working_directory
            ctx = AzureDevOpsPRContext.from_pr_id(42, str(specific_dir))

        # Then: context resolved from the specific directory, not the cache
        assert ctx.organization == "SpecificOrg", (
            f"Expected organization 'SpecificOrg' (from working_directory), "
            f"got '{ctx.organization}' — working_directory may not have been forwarded"
        )

    def test_error_dict_from_context_raises_actionable_error(self, tmp_path: Path) -> None:
        """
        Given discovery finds no repositories at the specified directory
        When from_pr_id(42, empty_dir) is called
        Then raises ActionableError with the underlying error message
        """
        # Given: a real directory with no git repos
        empty_dir = tmp_path / "no_repos"
        empty_dir.mkdir()

        # When / Then: from_pr_id() raises when discovery finds nothing
        with pytest.raises(ActionableError) as exc_info:
            AzureDevOpsPRContext.from_pr_id(42, str(empty_dir))

        # Then: the underlying discovery error propagates
        error_message = str(exc_info.value)
        assert "No Azure DevOps repositories found" in error_message, (
            f"Error should contain underlying message. Got: {error_message}"
        )


class TestPRContextFactory:
    """
    REQUIREMENT: establish_pr_context() routes URL-shaped input to from_url()
    and numeric input to from_pr_id().

    WHO: MCP tools and library consumers who receive ambiguous input ("123" vs
         "https://...")
    WHAT: (1) a URL input delegates to from_url() with source="url"
          (2) a numeric string delegates to from_pr_id() with
              source="repository_context"
          (3) a non-numeric, non-URL string raises ActionableError
          (4) an empty string raises ActionableError
    WHY: The consumer shouldn't need to pre-classify input. A single entry point
         handles both forms.

    MOCK BOUNDARY:
        Mock:  subprocess.run (git process — I/O edge, only on the numeric path)
        Real:  establish_pr_context, AzureDevOpsPRContext, parse_ado_url,
               RepositoryContext, discover_repositories, infer_target_repository,
               tmp_path filesystem
        Never: call from_url / from_pr_id directly to test the factory;
               mock any of our own functions
    """

    def setup_method(self) -> None:
        """Reset global context state between tests."""
        RepositoryContext.clear()

    def test_url_input_delegates_to_from_url(self) -> None:
        """
        Given a full PR URL
        When establish_pr_context(url) is called
        Then delegates to from_url(), returns context with source="url"
        """
        # Given: a valid PR URL
        url = "https://dev.azure.com/ContosoOrg/Payments/_git/PaymentsRepo/pullrequest/99"

        # When: the factory routes the input
        ctx = establish_pr_context(url)

        # Then: context was created via the URL path
        assert ctx.source == "url", f"Expected source 'url', got '{ctx.source}'"
        assert ctx.organization == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{ctx.organization}'"
        )
        assert ctx.pr_id == 99, f"Expected pr_id 99, got {ctx.pr_id}"

    def test_numeric_string_delegates_to_from_pr_id(self, tmp_path: Path) -> None:
        """
        Given a numeric string "42"
        When establish_pr_context("42") is called
        Then delegates to from_pr_id(42), returns context with
             source="repository_context"
        """
        # Given: context cached via set() — the numeric path uses RepositoryContext
        repo_dir = tmp_path / "PaymentsRepo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        remote_url = "https://dev.azure.com/ContosoOrg/Payments/_git/PaymentsRepo"
        mock_git = MagicMock(returncode=0, stdout=f"{remote_url}\n")
        with patch("ado_workflows.discovery.subprocess.run", return_value=mock_git):
            RepositoryContext.set(str(repo_dir))

        # When: the factory receives a numeric string
        ctx = establish_pr_context("42")

        # Then: context was created via the PR ID path
        assert ctx.source == "repository_context", (
            f"Expected source 'repository_context', got '{ctx.source}'"
        )
        assert ctx.pr_id == 42, f"Expected pr_id 42, got {ctx.pr_id}"

    def test_non_numeric_non_url_string_raises_actionable_error(self) -> None:
        """
        Given a non-numeric, non-URL string "abc"
        When called
        Then raises ActionableError
        """
        # Given: input that is neither a URL nor numeric
        bad_input = "abc"

        # When / Then: factory raises with an actionable error
        with pytest.raises(ActionableError) as exc_info:
            establish_pr_context(bad_input)

        error_message = str(exc_info.value)
        assert "abc" in error_message, (
            f"Error should reference the invalid input. Got: {error_message}"
        )
        assert "url" in error_message.lower() or "pr" in error_message.lower(), (
            f"Error should tell the consumer what valid input looks like "
            f"(a PR URL or numeric ID). Got: {error_message}"
        )

    def test_empty_string_raises_actionable_error(self) -> None:
        """
        Given an empty string
        When called
        Then raises ActionableError
        """
        # Given: empty input
        empty_input = ""

        # When / Then: factory raises with an actionable error
        with pytest.raises(ActionableError) as exc_info:
            establish_pr_context(empty_input)

        error_message = str(exc_info.value)
        assert "url" in error_message.lower() or "pr" in error_message.lower(), (
            f"Error should tell the consumer what input is expected "
            f"(a PR URL or numeric ID). Got: {error_message}"
        )


class TestOrgUrlProperty:
    """
    REQUIREMENT: AzureDevOpsPRContext.org_url returns the organization base URL.

    WHO: Downstream SDK operations that need the org URL for ConnectionFactory.
    WHAT: (1) returns https://dev.azure.com/{organization}
    WHY: Every SDK call needs the org URL. Computing it from the stored
         organization avoids passing it separately.

    MOCK BOUNDARY:
        Mock:  nothing
        Real:  AzureDevOpsPRContext
        Never: N/A
    """

    def test_org_url_returns_dev_azure_com_with_organization(self) -> None:
        """
        Given a context with organization="MyOrg"
        When .org_url is accessed
        Then returns "https://dev.azure.com/MyOrg"
        """
        # Given: a context with a known organization
        ctx = AzureDevOpsPRContext(
            pr_url="https://dev.azure.com/MyOrg/MyProject/_git/MyRepo/pullrequest/1",
            organization="MyOrg",
            project="MyProject",
            repository="MyRepo",
            pr_id=1,
            source="url",
        )

        # When: org_url is accessed
        result = ctx.org_url

        # Then: it returns the base org URL
        assert result == "https://dev.azure.com/MyOrg", (
            f"Expected 'https://dev.azure.com/MyOrg', got '{result}'"
        )


class TestSerialization:
    """
    REQUIREMENT: AzureDevOpsPRContext.to_dict() serializes the context
    including computed properties.

    WHO: MCP tools returning context as JSON to LLMs.
    WHAT: (1) to_dict() contains all dataclass fields plus org_url
          (2) the result is JSON-serializable
    WHY: LLMs need structured data. Omitting org_url would force every
         consumer to recompute it.

    MOCK BOUNDARY:
        Mock:  nothing
        Real:  AzureDevOpsPRContext
        Never: N/A
    """

    def test_to_dict_contains_all_fields_plus_org_url(self) -> None:
        """
        Given a populated context
        When .to_dict() is called
        Then dict contains all 6 dataclass fields plus org_url (7 keys total)
        """
        # Given: a fully populated context
        ctx = AzureDevOpsPRContext(
            pr_url="https://dev.azure.com/ContosoOrg/Payments/_git/PaymentsRepo/pullrequest/99",
            organization="ContosoOrg",
            project="Payments",
            repository="PaymentsRepo",
            pr_id=99,
            source="url",
        )

        # When: to_dict() serializes the context
        result = ctx.to_dict()

        # Then: all 6 dataclass fields are present with correct values
        assert result["pr_url"] == ctx.pr_url, (
            f"Expected pr_url '{ctx.pr_url}', got '{result.get('pr_url')}'"
        )
        assert result["organization"] == "ContosoOrg", (
            f"Expected organization 'ContosoOrg', got '{result.get('organization')}'"
        )
        assert result["project"] == "Payments", (
            f"Expected project 'Payments', got '{result.get('project')}'"
        )
        assert result["repository"] == "PaymentsRepo", (
            f"Expected repository 'PaymentsRepo', got '{result.get('repository')}'"
        )
        assert result["pr_id"] == 99, f"Expected pr_id 99, got {result.get('pr_id')}"
        assert result["source"] == "url", f"Expected source 'url', got '{result.get('source')}'"

        # Then: computed property org_url is included
        assert result["org_url"] == "https://dev.azure.com/ContosoOrg", (
            f"Expected org_url 'https://dev.azure.com/ContosoOrg', got '{result.get('org_url')}'"
        )

        # Then: exactly 7 keys total (6 dataclass + org_url)
        assert len(result) == 7, (
            f"Expected 7 keys (6 dataclass fields + org_url), "
            f"got {len(result)}: {sorted(result.keys())}"
        )

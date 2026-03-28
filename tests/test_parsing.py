"""
BDD tests for ado_workflows.parsing — URL and date parsing primitives.

Covers:
- TestAdoUrlParsing: dev.azure.com, visualstudio.com, SSH, PR URLs, unsupported formats
- TestAdoDateParsing: ISO variants, edge cases, invalid input

Public API surface (from src/ado_workflows/parsing.py):
    parse_ado_url(url: str) -> tuple[str, str, str, str]
    parse_ado_date(date_str: str) -> datetime | None
"""

from __future__ import annotations

from datetime import UTC, datetime

from ado_workflows.parsing import parse_ado_date, parse_ado_url


class TestAdoUrlParsing:
    """
    REQUIREMENT: Azure DevOps URLs are parsed into their constituent parts.

    WHO: Any consumer that needs org, project, repo, or PR ID from a URL.
    WHAT: (1) a modern dev.azure.com repo URL is parsed correctly
          (2) a dev.azure.com PR URL extracts the PR ID
          (3) URL-encoded project names are decoded
          (4) a legacy visualstudio.com URL with DefaultCollection is parsed
          (5) a modern visualstudio.com URL without DefaultCollection is parsed
          (6) a visualstudio.com PR URL extracts the PR ID
          (7) an SSH remote URL is parsed correctly
          (8) an SSH remote with .git suffix has the suffix stripped
          (9) an HTTPS repo URL with .git suffix has the suffix stripped
          (10) an unsupported URL returns empty strings for all fields
          (11) an empty string returns empty strings for all fields
          (12) a malformed SSH URL (missing segments) returns empty strings
    WHY: URL parsing is the foundation of every ADO operation — incorrect
         parsing cascades into wrong API calls, wrong repo targeting, and
         opaque failures.

    MOCK BOUNDARY:
        Mock:  nothing — this class tests pure computation
        Real:  parse_ado_url function
        Never: construct tuples directly — always obtain via parse_ado_url()
    """

    def test_modern_dev_azure_com_repo_url(self) -> None:
        """
        Given a modern dev.azure.com git remote URL
        When parse_ado_url is called
        Then org, project, and repo are extracted; pr_id is empty
        """
        # Given: a modern dev.azure.com URL
        url = "https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all components are extracted correctly
        assert org == "ExampleOrg", f"Expected org 'ExampleOrg', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id for non-PR URL, got '{pr_id}'"

    def test_modern_dev_azure_com_pr_url(self) -> None:
        """
        Given a dev.azure.com pull request URL
        When parse_ado_url is called
        Then org, project, repo, and pr_id are all extracted
        """
        # Given: a PR URL
        url = "https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo/pullrequest/12345"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all components including PR ID are extracted
        assert org == "ExampleOrg", f"Expected org 'ExampleOrg', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"
        assert pr_id == "12345", f"Expected pr_id '12345', got '{pr_id}'"

    def test_url_encoded_project_names_are_decoded(self) -> None:
        """
        Given a dev.azure.com URL with URL-encoded spaces in the project name
        When parse_ado_url is called
        Then the project name has spaces decoded
        """
        # Given: a URL with %20-encoded project name
        url = "https://dev.azure.com/ExampleOrg/My%20Project/_git/MyRepo"

        # When: the URL is parsed
        _, project, _, _ = parse_ado_url(url)

        # Then: URL encoding is resolved
        assert project == "My Project", f"Expected decoded project 'My Project', got '{project}'"

    def test_legacy_visualstudio_com_with_default_collection(self) -> None:
        """
        Given a legacy visualstudio.com URL with DefaultCollection prefix
        When parse_ado_url is called
        Then org, project, and repo are extracted correctly
        """
        # Given: a legacy URL with DefaultCollection
        url = "https://example.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all components are extracted
        assert org == "example", f"Expected org 'example', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id, got '{pr_id}'"

    def test_modern_visualstudio_com_without_default_collection(self) -> None:
        """
        Given a visualstudio.com URL without DefaultCollection
        When parse_ado_url is called
        Then org and project are extracted from the modern layout
        """
        # Given: a modern visualstudio.com URL
        url = "https://example.visualstudio.com/MyProject/_git/MyRepo"

        # When: the URL is parsed
        org, project, repo, _ = parse_ado_url(url)

        # Then: components are extracted correctly
        assert org == "example", f"Expected org 'example', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"

    def test_visualstudio_com_pr_url(self) -> None:
        """
        Given a visualstudio.com pull request URL
        When parse_ado_url is called
        Then the PR ID is extracted alongside org/project/repo
        """
        # Given: a visualstudio.com PR URL
        url = "https://example.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo/pullrequest/99999"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: PR ID is extracted
        assert org == "example", f"Expected org 'example', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"
        assert pr_id == "99999", f"Expected pr_id '99999', got '{pr_id}'"

    def test_ssh_remote_url(self) -> None:
        """
        Given an SSH git remote URL (git@ssh.dev.azure.com:v3/...)
        When parse_ado_url is called
        Then org, project, and repo are extracted from the SSH format
        """
        # Given: an SSH remote URL
        url = "git@ssh.dev.azure.com:v3/ExampleOrg/MyProject/MyRepo"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all components are extracted
        assert org == "ExampleOrg", f"Expected org 'ExampleOrg', got '{org}'"
        assert project == "MyProject", f"Expected project 'MyProject', got '{project}'"
        assert repo == "MyRepo", f"Expected repo 'MyRepo', got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id for SSH URL, got '{pr_id}'"

    def test_ssh_remote_with_git_suffix(self) -> None:
        """
        Given an SSH remote URL with .git suffix on the repository name
        When parse_ado_url is called
        Then the .git suffix is stripped from the repo name
        """
        # Given: SSH URL with .git suffix
        url = "git@ssh.dev.azure.com:v3/ExampleOrg/MyProject/MyRepo.git"

        # When: the URL is parsed
        _, _, repo, _ = parse_ado_url(url)

        # Then: .git suffix is stripped
        assert repo == "MyRepo", f"Expected repo 'MyRepo' without .git suffix, got '{repo}'"

    def test_https_repo_url_with_git_suffix(self) -> None:
        """
        Given a dev.azure.com HTTPS URL with .git suffix
        When parse_ado_url is called
        Then the .git suffix is stripped from the repo name
        """
        # Given: HTTPS URL with .git suffix
        url = "https://dev.azure.com/ExampleOrg/MyProject/_git/MyRepo.git"

        # When: the URL is parsed
        _, _, repo, _ = parse_ado_url(url)

        # Then: .git suffix is stripped
        assert repo == "MyRepo", f"Expected repo 'MyRepo' without .git suffix, got '{repo}'"

    def test_malformed_ssh_url_returns_empty_strings(self) -> None:
        """
        Given an SSH URL containing ssh.dev.azure.com but missing project/repo
        When parse_ado_url is called
        Then all fields are empty strings (SSH regex fails)
        """
        # Given: a malformed SSH URL (missing project and repo segments)
        url = "git@ssh.dev.azure.com:v3/OrgOnly"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all fields are empty (SSH regex requires org/project/repo)
        assert org == "", f"Expected empty org for malformed SSH URL, got '{org}'"
        assert project == "", f"Expected empty project for malformed SSH URL, got '{project}'"
        assert repo == "", f"Expected empty repo for malformed SSH URL, got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id for malformed SSH URL, got '{pr_id}'"

    def test_unsupported_url_returns_empty_strings(self) -> None:
        """
        Given a non-Azure DevOps URL (e.g. GitHub)
        When parse_ado_url is called
        Then all fields are empty strings
        """
        # Given: a GitHub URL
        url = "https://github.com/example/some-repo.git"

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all fields are empty
        assert org == "", f"Expected empty org, got '{org}'"
        assert project == "", f"Expected empty project, got '{project}'"
        assert repo == "", f"Expected empty repo, got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id, got '{pr_id}'"

    def test_empty_string_returns_empty_strings(self) -> None:
        """
        Given an empty string
        When parse_ado_url is called
        Then all fields are empty strings
        """
        # Given: empty input
        url = ""

        # When: the URL is parsed
        org, project, repo, pr_id = parse_ado_url(url)

        # Then: all fields are empty
        assert org == "", f"Expected empty org, got '{org}'"
        assert project == "", f"Expected empty project, got '{project}'"
        assert repo == "", f"Expected empty repo, got '{repo}'"
        assert pr_id == "", f"Expected empty pr_id, got '{pr_id}'"


class TestAdoDateParsing:
    """
    REQUIREMENT: Azure DevOps API date strings are parsed into local datetime objects.

    WHO: Any consumer processing timestamps from ADO API responses (PR dates,
         comment timestamps, vote dates).
    WHAT: (1) an ISO date with timezone suffix is parsed and tzinfo is stripped
          (2) an ISO date with milliseconds is parsed correctly
          (3) an ISO date without timezone is parsed (treated as UTC)
          (4) an empty string returns None
          (5) a malformed date string returns None
          (6) a parsed UTC date matches the expected components after
              round-trip conversion
    WHY: ADO API returns dates in inconsistent formats — callers must not
         perform ad-hoc parsing. A single, tested parser eliminates silent
         date comparison bugs.

    MOCK BOUNDARY:
        Mock:  nothing — this class tests pure computation
        Real:  parse_ado_date function
        Never: construct datetime directly in assertions — always compare
               against known values or property checks
    """

    def test_iso_date_with_timezone_suffix(self) -> None:
        """
        Given an ISO date string with Z timezone suffix
        When parse_ado_date is called
        Then a datetime object is returned with tzinfo stripped
        """
        # Given: ISO date with Z suffix (common ADO format)
        date_str = "2025-03-15T14:30:00Z"

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: a datetime is returned, converted to local time
        assert result is not None, "Expected datetime for valid ISO date, got None"
        assert result.tzinfo is None, (
            f"Expected naive datetime (local time), got tzinfo={result.tzinfo}"
        )

    def test_iso_date_with_milliseconds(self) -> None:
        """
        Given an ISO date string with milliseconds and Z suffix
        When parse_ado_date is called
        Then the date is parsed (milliseconds truncated) and returned as local time
        """
        # Given: ISO date with milliseconds (common in ADO thread timestamps)
        date_str = "2025-03-15T14:30:00.1234567Z"

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: datetime is returned with milliseconds truncated
        assert result is not None, "Expected datetime for date with milliseconds, got None"
        assert result.tzinfo is None, (
            f"Expected naive datetime (local time), got tzinfo={result.tzinfo}"
        )

    def test_iso_date_without_timezone(self) -> None:
        """
        Given an ISO date string without timezone indicator
        When parse_ado_date is called
        Then the date is parsed (treated as UTC) and returned as local time
        """
        # Given: ISO date without timezone
        date_str = "2025-03-15T14:30:00"

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: datetime is returned
        assert result is not None, "Expected datetime for date without timezone, got None"

    def test_empty_string_returns_none(self) -> None:
        """
        Given an empty string
        When parse_ado_date is called
        Then None is returned
        """
        # Given: empty input
        date_str = ""

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: None is returned
        assert result is None, f"Expected None for empty date string, got {result}"

    def test_malformed_date_returns_none(self) -> None:
        """
        Given a string that is not a valid date
        When parse_ado_date is called
        Then None is returned
        """
        # Given: malformed input
        date_str = "not-a-date"

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: None is returned
        assert result is None, f"Expected None for malformed date, got {result}"

    def test_parsed_utc_date_matches_expected_components(self) -> None:
        """
        Given a UTC date with known year/month/day/hour values
        When parse_ado_date is called
        Then the parsed datetime has matching date components when converted back to UTC
        """
        # Given: a known UTC date
        date_str = "2025-06-20T10:00:00Z"

        # When: the date is parsed
        result = parse_ado_date(date_str)

        # Then: date components are correct (verify via round-trip to UTC)
        assert result is not None, "Expected datetime for valid ISO date, got None"
        # The result is in local time. Verify by converting the known input
        # to local time independently.
        expected_utc = datetime(2025, 6, 20, 10, 0, 0, tzinfo=UTC)
        expected_local = expected_utc.astimezone().replace(tzinfo=None)
        assert result == expected_local, f"Expected {expected_local}, got {result}"

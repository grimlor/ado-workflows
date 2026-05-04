"""
BDD tests for ado_workflows.parsing — parse_ado_work_item_url.

Covers:
- TestParseAdoWorkItemUrl: dev.azure.com, visualstudio.com (modern + DefaultCollection),
  URL-decoded project names, unrecognised input.

Public API surface (from src/ado_workflows/parsing.py):
    parse_ado_work_item_url(url: str) -> tuple[str, str, str]
"""

from __future__ import annotations

from ado_workflows.parsing import parse_ado_work_item_url


class TestParseAdoWorkItemUrl:
    """
    REQUIREMENT: parse_ado_work_item_url extracts (organization, project,
    work_item_id) from any supported Azure DevOps work item URL format,
    and returns ('', '', '') for unrecognised input.

    WHO: Callers establishing work item context from a URL — agents,
        downstream MCP tools, and CLI users who paste a URL from the
        ADO web UI.
    WHAT: (1) Returns (org, project, id) for a dev.azure.com work item URL
              of the form https://dev.azure.com/{org}/{project}/_workitems/edit/{id}.
          (2) Returns (org, project, id) for a modern {org}.visualstudio.com
              work item URL of the form
              https://{org}.visualstudio.com/{project}/_workitems/edit/{id}
              — the org lives in the host subdomain, the project is the
              first path segment.
          (3) Returns (org, project, id) for a legacy {org}.visualstudio.com
              URL that retains the historical DefaultCollection path segment:
              https://{org}.visualstudio.com/DefaultCollection/{project}/_workitems/edit/{id}.
          (4) URL-decodes %20-escaped project names so a project like
              "My Project" appears as "My Project" not "My%20Project".
          (5) Returns ('', '', '') for any URL that does not yield all
              three required fields — including non-work-item URLs (a
              PR URL, a non-ADO URL, an empty string) and work-item-
              shaped URLs that are missing one of the fields (a
              non-numeric id, an unknown host, dev.azure.com with a
              one-segment path, visualstudio.com with no project
              segment, visualstudio.com with no org subdomain).
    WHY: Without this parser, every consumer hand-parses URLs, risking
        format drift (visualstudio.com host with org-in-subdomain,
        optional DefaultCollection segment) and silent encoding bugs.
        A pure function with explicit return values lets the rest of
        the library reason about URL-vs-ID without owning regex.

    MOCK BOUNDARY:
        Mock:  Nothing. This is a pure function.
        Real:  parse_ado_work_item_url itself.
        Never: Nothing — no I/O.
    """

    def test_dev_azure_com_url_returns_org_project_id(self) -> None:
        """
        Given a dev.azure.com work item URL with org=Foo, project=Bar, id=42
        When parse_ado_work_item_url is called
        Then it returns ('Foo', 'Bar', '42')
        """
        # Given: a dev.azure.com work item URL
        url = "https://dev.azure.com/Foo/Bar/_workitems/edit/42"

        # When: the URL is parsed
        org, project, work_item_id = parse_ado_work_item_url(url)

        # Then: all components are extracted correctly
        assert org == "Foo", f"Expected org 'Foo', got '{org}'"
        assert project == "Bar", f"Expected project 'Bar', got '{project}'"
        assert work_item_id == "42", f"Expected id '42', got '{work_item_id}'"

    def test_modern_visualstudio_com_url_returns_org_project_id(self) -> None:
        """
        Given a modern {org}.visualstudio.com work item URL with no
            DefaultCollection segment — the actual originating bug URL
            https://msazure.visualstudio.com/One/_workitems/edit/37453680
        When parse_ado_work_item_url is called
        Then it returns ('msazure', 'One', '37453680')
        """
        # Given: a modern visualstudio.com URL where the org lives in
        # the host subdomain and the project is the first path segment
        url = "https://msazure.visualstudio.com/One/_workitems/edit/37453680"

        # When: the URL is parsed
        org, project, work_item_id = parse_ado_work_item_url(url)

        # Then: the host subdomain is the org, the first path segment
        # is the project, the trailing integer is the id
        assert org == "msazure", f"Expected org 'msazure', got '{org}'"
        assert project == "One", f"Expected project 'One', got '{project}'"
        assert work_item_id == "37453680", f"Expected id '37453680', got '{work_item_id}'"

    def test_visualstudio_com_url_with_default_collection_returns_org_project_id(
        self,
    ) -> None:
        """
        Given a {org}.visualstudio.com/DefaultCollection/{project}/_workitems/edit/{id} URL
        When parse_ado_work_item_url is called
        Then it returns (org, project, id) with DefaultCollection stripped
        """
        # Given: a legacy visualstudio.com URL with DefaultCollection
        url = "https://msazure.visualstudio.com/DefaultCollection/One/_workitems/edit/37453680"

        # When: the URL is parsed
        org, project, work_item_id = parse_ado_work_item_url(url)

        # Then: org is the host subdomain, project is the segment after
        # DefaultCollection, id is the trailing integer
        assert org == "msazure", f"Expected org 'msazure', got '{org}'"
        assert project == "One", f"Expected project 'One', got '{project}'"
        assert work_item_id == "37453680", f"Expected id '37453680', got '{work_item_id}'"

    def test_url_with_percent_encoded_project_name_returns_decoded_project(self) -> None:
        """
        Given a URL whose project segment is "My%20Project"
        When parse_ado_work_item_url is called
        Then the returned project is "My Project"
        """
        # Given: a URL with a percent-encoded space in the project name
        url = "https://dev.azure.com/ContosoOrg/My%20Project/_workitems/edit/7"

        # When: the URL is parsed
        _org, project, _work_item_id = parse_ado_work_item_url(url)

        # Then: the project is URL-decoded
        assert project == "My Project", (
            f"Expected project 'My Project' (URL-decoded), got '{project}'"
        )

    def test_unrecognised_url_returns_empty_tuple(self) -> None:
        """
        Given inputs that are not recognised work item URLs
            (a PR URL, a GitHub URL, an empty string)
        When parse_ado_work_item_url is called
        Then each returns ('', '', '')
        """
        # Given: a PR URL — same hostname, different path shape
        pr_url = "https://dev.azure.com/Foo/Bar/_git/Baz/pullrequest/42"

        # When/Then: PR URLs are not work item URLs
        assert parse_ado_work_item_url(pr_url) == ("", "", ""), (
            f"PR URL should return empty tuple, got {parse_ado_work_item_url(pr_url)!r}"
        )

        # Given: a GitHub URL — different hostname entirely
        github_url = "https://github.com/grimlor/ado-workflows/issues/42"

        # When/Then: GitHub URLs are not ADO work item URLs
        assert parse_ado_work_item_url(github_url) == ("", "", ""), (
            f"GitHub URL should return empty tuple, got {parse_ado_work_item_url(github_url)!r}"
        )

        # Given: empty input
        # When/Then: empty input returns empty tuple
        assert parse_ado_work_item_url("") == ("", "", ""), (
            f"Empty string should return empty tuple, got {parse_ado_work_item_url('')!r}"
        )

        # Given: a URL with /_workitems/edit/ but a non-numeric id
        no_digit_id = "https://dev.azure.com/Foo/Bar/_workitems/edit/abc"

        # When/Then: missing numeric id → empty tuple (no field
        # extractable, so the contract is empty)
        assert parse_ado_work_item_url(no_digit_id) == ("", "", ""), (
            f"Non-numeric id should return empty tuple, "
            f"got {parse_ado_work_item_url(no_digit_id)!r}"
        )

        # Given: an unknown host that contains the work-item path shape
        unknown_host = "https://example.com/foo/bar/_workitems/edit/42"

        # When/Then: unknown host → empty tuple (no host branch matched,
        # so org/project remain empty and the final all-fields check fails)
        assert parse_ado_work_item_url(unknown_host) == ("", "", ""), (
            f"Unknown host should return empty tuple, "
            f"got {parse_ado_work_item_url(unknown_host)!r}"
        )

        # Given: a dev.azure.com URL with only one path segment before
        # /_workitems/edit/ (missing the project segment)
        bad_devazure = "https://dev.azure.com/onlyone/_workitems/edit/42"

        # When/Then: malformed dev.azure.com path → empty tuple (regex
        # fails to capture project, final all-fields check fails)
        assert parse_ado_work_item_url(bad_devazure) == ("", "", ""), (
            f"Malformed dev.azure.com URL should return empty tuple, "
            f"got {parse_ado_work_item_url(bad_devazure)!r}"
        )

        # Given: a visualstudio.com URL with no project segment between
        # the host and /_workitems/edit/
        bad_visualstudio = "https://msazure.visualstudio.com/_workitems/edit/42"

        # When/Then: malformed visualstudio.com path → empty tuple
        # (neither DefaultCollection nor modern project pattern matches,
        # so project remains empty and the final all-fields check fails)
        assert parse_ado_work_item_url(bad_visualstudio) == ("", "", ""), (
            f"Malformed visualstudio.com URL should return empty tuple, "
            f"got {parse_ado_work_item_url(bad_visualstudio)!r}"
        )

        # Given: a malformed URL where ".visualstudio.com" appears with
        # no preceding non-slash chars to form a valid org subdomain
        no_org_visualstudio = ".visualstudio.com/foo/_workitems/edit/42"

        # When/Then: missing org subdomain → empty tuple (org_match
        # fails, so org remains empty)
        assert parse_ado_work_item_url(no_org_visualstudio) == ("", "", ""), (
            f"Missing-org visualstudio.com URL should return empty tuple, "
            f"got {parse_ado_work_item_url(no_org_visualstudio)!r}"
        )

"""
BDD tests for ado_workflows git commit listing — FR3a B3.

Covers:
- TestListCommits: list git commits filtered by author and date range

Public API surface (new in FR3a):
    From src/ado_workflows/listing.py:
        list_commits(
            repo_path: str, *,
            authors: list[str] | None, since: str | None,
            max_count: int,
        ) -> list[CommitSummary]

    From src/ado_workflows/models.py:
        CommitSummary(sha, message, author, date, repo_name)
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from actionable_errors import ActionableError

from ado_workflows.listing import list_commits
from ado_workflows.models import CommitSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_commit(
    *,
    hexsha: str = "abc1234",
    message: str = "feat: add feature support",
    author_name: str = "Alice Smith",
    author_email: str = "alice@contoso.com",
    committed_date: int = 1711929600,  # 2024-04-01T00:00:00 UTC
) -> Mock:
    """Build a mock GitPython Commit object."""
    commit = Mock()
    commit.hexsha = hexsha
    commit.message = message
    commit.author.name = author_name
    commit.author.email = author_email
    commit.committed_date = committed_date
    return commit


# ---------------------------------------------------------------------------
# TestListCommits — B3
# ---------------------------------------------------------------------------


class TestListCommits:
    """
    REQUIREMENT: List git commits from a local repository filtered by
    author and date range.

    WHO: Reporting tools, commit activity dashboards
    WHAT: (1) returns CommitSummary list sorted by date descending
          (2) empty result returns empty list
          (3) invalid repo path raises ActionableError
          (4) matches commits by any of the provided author patterns
          (5) deduplicates commits by SHA when --all produces duplicates
          (6) derives repo_name from Path(repo_path).name
    WHY: Enables querying commit history across local repos for activity
         reporting and development metrics.

    MOCK BOUNDARY:
        Mock:  git.Repo() (GitPython — I/O boundary)
        Real:  list_commits(), model mapping, deduplication
        Never: nothing
    """

    def test_valid_repo_returns_commit_summaries_sorted_by_date_descending(
        self,
    ) -> None:
        """
        Given a valid repo with matching commits,
        When called,
        Then returns CommitSummary list sorted by date descending.
        """
        # Given: repo with two commits at different times
        older_commit = _mock_commit(
            hexsha="aaa1111",
            message="chore: initial setup",
            committed_date=1711843200,  # earlier
        )
        newer_commit = _mock_commit(
            hexsha="bbb2222",
            message="feat: add feature",
            committed_date=1711929600,  # later
        )
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = iter([older_commit, newer_commit])

        with patch("ado_workflows.listing.Repo", return_value=mock_repo):
            # When: list_commits is called
            result = list_commits("/home/user/repos/ado-workflows")

        # Then: sorted by date descending (newer first)
        assert len(result) == 2, f"Expected 2 commits, got {len(result)}"
        assert all(isinstance(c, CommitSummary) for c in result), (
            "Expected all items to be CommitSummary"
        )
        assert result[0].sha == "bbb2222", f"Expected newer commit first, got sha={result[0].sha}"
        assert result[1].sha == "aaa1111", f"Expected older commit second, got sha={result[1].sha}"

    def test_no_matching_commits_returns_empty_list(self) -> None:
        """
        Given no commits match the filter,
        When called,
        Then returns empty list.
        """
        # Given: repo returns no commits
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = iter([])

        with patch("ado_workflows.listing.Repo", return_value=mock_repo):
            # When: list_commits is called
            result = list_commits("/home/user/repos/ado-workflows")

        # Then: empty list
        assert result == [], f"Expected empty list, got {result}"

    def test_invalid_repo_path_raises_actionable_error(self) -> None:
        """
        Given an invalid repo path,
        When called,
        Then raises ActionableError.
        """
        # Given: Repo() raises on invalid path
        with patch(
            "ado_workflows.listing.Repo",
            side_effect=Exception("not a git repository"),
        ):
            # When / Then: raises ActionableError
            with pytest.raises(ActionableError) as exc_info:
                list_commits("/nonexistent/path")
            assert "not a git repository" in str(exc_info.value), (
                f"Expected error to mention invalid repo, got {exc_info.value!r}"
            )

    def test_author_patterns_match_any_provided_pattern(self) -> None:
        """
        Given authors=["Alice", "alice@example.com"],
        When called,
        Then matches commits by any of the author patterns.
        """
        # Given: repo with commits from matching author
        commit = _mock_commit(
            hexsha="ccc3333",
            author_name="Alice Smith",
            author_email="alice@example.com",
        )
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = iter([commit])

        with patch("ado_workflows.listing.Repo", return_value=mock_repo):
            # When: called with multiple author patterns
            result = list_commits(
                "/home/user/repos/ado-workflows",
                authors=["Alice", "alice@example.com"],
            )

        # Then: the commit is returned
        assert len(result) == 1, f"Expected 1 commit, got {len(result)}"
        assert result[0].author == "Alice Smith", (
            f"Expected author='Alice Smith', got {result[0].author!r}"
        )

    def test_duplicate_commits_across_branches_are_deduplicated_by_sha(
        self,
    ) -> None:
        """
        Given --all returns duplicate commits across branches,
        When called,
        Then deduplicates by SHA.
        """
        # Given: same commit appears twice (from different branches)
        commit_a = _mock_commit(hexsha="ddd4444", message="fix: typo")
        commit_b = _mock_commit(hexsha="ddd4444", message="fix: typo")
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = iter([commit_a, commit_b])

        with patch("ado_workflows.listing.Repo", return_value=mock_repo):
            # When: list_commits is called
            result = list_commits("/home/user/repos/ado-workflows")

        # Then: only one commit returned (deduplicated)
        assert len(result) == 1, f"Expected 1 unique commit after dedup, got {len(result)}"

    def test_repo_name_derived_from_path_basename(self) -> None:
        """
        Given repo_path='/home/user/repos/ado-workflows',
        When called,
        Then CommitSummary.repo_name equals 'ado-workflows'.
        """
        # Given: repo with one commit
        commit = _mock_commit(hexsha="eee5555")
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = iter([commit])

        with patch("ado_workflows.listing.Repo", return_value=mock_repo):
            # When: list_commits is called
            result = list_commits("/home/user/repos/ado-workflows")

        # Then: repo_name is the basename of the path
        assert len(result) == 1, f"Expected 1 commit, got {len(result)}"
        assert result[0].repo_name == "ado-workflows", (
            f"Expected repo_name='ado-workflows', got {result[0].repo_name!r}"
        )

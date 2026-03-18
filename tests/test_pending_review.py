"""BDD tests for ado_workflows.review.analyze_pending_reviews — pending review analysis.

Covers:
- TestAnalyzePendingReviewsOrchestration: core filtering, enrichment, sorting
- TestAnalyzePendingReviewsErrorHandling: API failures, partial success, skipping
- TestAnalyzePendingReviewsEdgeCases: no reviewers, None dates, conflicts, case

Public API surface (from src/ado_workflows/review.py):
    analyze_pending_reviews(client: AdoClient, project: str,
                            repository: str, *,
                            max_days_old: int = 30,
                            creator_filter: str | None = None,
                            default_required_approvals: int = 2
                            ) -> PendingReviewResult

Public models (from src/ado_workflows/models.py):
    PendingReviewResult(pending_prs: list[PendingPR], skipped: list[ActionableError])
    PendingPR(pr_id, title, author, creation_date, repository, organization,
              project, web_url, pending_reviewers, days_open, merge_status,
              has_conflicts, needs_approvals_count, valid_approvals_count)
    ReviewerInfo(display_name, unique_name, vote, is_required, is_container)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.review import analyze_pending_reviews

# ---------------------------------------------------------------------------
# Helpers for mock PR and client construction
# ---------------------------------------------------------------------------

_UNSET: Any = object()


def _make_pr(
    *,
    pr_id: int = 1,
    title: str = "Add feature X",
    author: str = "alice@example.com",
    creation_date: datetime | None | Any = _UNSET,
    is_draft: bool = False,
    reviewers: list[Mock] | None = None,
    merge_status: str = "succeeded",
    url: str | None = None,
    repository_name: str = "Repo",
    source_ref: str = "refs/heads/feature",
) -> Mock:
    """Build a mock GitPullRequest matching the Azure DevOps SDK shape."""
    pr = Mock()
    pr.pull_request_id = pr_id
    pr.title = title
    pr.created_by = Mock()
    pr.created_by.unique_name = author
    pr.creation_date = (
        datetime.now(tz=UTC) - timedelta(days=5) if creation_date is _UNSET else creation_date
    )
    pr.is_draft = is_draft
    pr.reviewers = reviewers or []
    pr.merge_status = merge_status
    pr.url = (
        url
        or f"https://dev.azure.com/Org/Proj/_apis/git/repositories/{repository_name}/pullRequests/{pr_id}"
    )
    pr.repository = Mock()
    pr.repository.name = repository_name
    # web_url construction: ADO SDK PRs have a _links.web.href or we build it
    pr.source_ref_name = source_ref
    return pr


def _make_reviewer(
    *,
    display_name: str = "Bob Review",
    unique_name: str = "bob@example.com",
    reviewer_id: str = "guid-bob",
    vote: int = 0,
    is_container: bool | None = None,
    voted_for: list[Mock] | None = None,
    is_required: bool = True,
) -> Mock:
    """Build a mock IdentityRefWithVote."""
    return Mock(
        display_name=display_name,
        unique_name=unique_name,
        id=reviewer_id,
        vote=vote,
        is_container=is_container,
        voted_for=voted_for,
        is_required=is_required,
        spec=[
            "display_name",
            "unique_name",
            "id",
            "vote",
            "is_container",
            "voted_for",
            "is_required",
        ],
    )


def _make_commit(*, author_date: datetime) -> Mock:
    """Build a mock commit with an author.date attribute."""
    commit = Mock()
    commit.author.date = author_date
    return commit


def _mock_client(
    *,
    prs: list[Mock] | None = None,
    commits_per_pr: dict[int, list[Mock]] | None = None,
    properties_per_pr: dict[int, Any] | None = None,
    threads_per_pr: dict[int, list[Mock]] | None = None,
    policy_evaluations: list[Mock] | None = None,
) -> Mock:
    """Build a fully-wired mock AdoClient for analyze_pending_reviews.

    Configures git.get_pull_requests, git.get_pull_request_commits (per PR),
    git.get_pull_request_properties (per PR), git.get_threads (per PR),
    and policy.get_policy_evaluations.
    """
    client = Mock()

    # PR listing
    client.git.get_pull_requests.return_value = prs or []

    # Per-PR enrichment — use side_effect to return different data per call
    _commits = commits_per_pr or {}
    _properties = properties_per_pr or {}
    _threads = threads_per_pr or {}

    def _get_commits(repo: str, pr_id: int, *, project: str) -> list[Mock]:
        return _commits.get(pr_id, [])

    def _get_properties(repo: str, pr_id: int, *, project: str) -> dict[str, object]:
        return {"value": _properties.get(pr_id, {})}

    def _get_threads(repo: str, pr_id: int, *, project: str) -> list[Mock]:
        return _threads.get(pr_id, [])

    client.git.get_pull_request_commits.side_effect = _get_commits
    client.git.get_pull_request_properties.side_effect = _get_properties
    client.git.get_threads.side_effect = _get_threads

    # Policy evaluations
    client.policy.get_policy_evaluations.return_value = policy_evaluations or []

    return client


def _make_policy_evaluation(
    *,
    display_name: str = "Minimum number of reviewers",
    min_approver_count: int = 2,
) -> Mock:
    """Build a mock PolicyEvaluationRecord."""
    evaluation = Mock()
    evaluation.configuration.type.display_name = display_name
    evaluation.configuration.settings = {
        "minimumApproverCount": min_approver_count,
    }
    return evaluation


# ---------------------------------------------------------------------------
# TestAnalyzePendingReviewsOrchestration
# ---------------------------------------------------------------------------


class TestAnalyzePendingReviewsOrchestration:
    """
    REQUIREMENT: analyze_pending_reviews lists active non-draft PRs, classifies
    reviewer votes with full staleness detection, deduplicates team containers,
    fetches policy-based required approvals, and returns a PendingPR for each
    PR that still needs attention.

    WHO: MCP tools and automation scripts that need to identify which PRs are
         waiting for reviews.
    WHAT: Fetches active PRs via GitClient.get_pull_requests() with
          GitPullRequestSearchCriteria. Filters out drafts, respects
          creator_filter (case-insensitive), max_days_old. Per-PR enrichment
          reuses fetch_vote_timestamps, determine_vote_status,
          deduplicate_team_containers, fetch_required_approvals. Computes
          needs_approvals_count and valid_approvals_count. Only includes PRs
          that still need attention. Returns sorted by days_open descending.
    WHY: The PDP version had three critical bugs: hardcoded required_approvals,
         no vote staleness detection, no team container deduplication.

    MOCK BOUNDARY:
        Mock:  AdoClient — all SDK client methods (git, policy)
        Real:  analyze_pending_reviews(), fetch_vote_timestamps(),
               determine_vote_status(), deduplicate_team_containers(),
               fetch_required_approvals(), PendingPR, ReviewerInfo, VoteStatus
        Never: Construct PendingPR directly in assertions — always obtain from
               analyze_pending_reviews() return value
    """

    def test_active_prs_with_pending_reviewers(self) -> None:
        """
        Given active non-draft PRs with pending reviewers
        When analyze_pending_reviews is called
        Then returns PendingPR entries with correct pending reviewer lists
        """
        # Given: 2 active PRs, each with a pending reviewer (vote=0)
        reviewer1 = _make_reviewer(
            display_name="Bob",
            unique_name="bob@ex.com",
            reviewer_id="guid-b",
            vote=0,
        )
        reviewer2 = _make_reviewer(
            display_name="Carol",
            unique_name="carol@ex.com",
            reviewer_id="guid-c",
            vote=0,
        )
        pr1 = _make_pr(pr_id=10, title="PR One", reviewers=[reviewer1])
        pr2 = _make_pr(pr_id=20, title="PR Two", reviewers=[reviewer2])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr1, pr2],
            commits_per_pr={10: [commit], 20: [commit]},
        )

        # When: analyze_pending_reviews is called
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: both PRs appear in result with pending reviewers
        assert len(result.pending_prs) == 2, (
            f"Expected 2 pending PRs, got {len(result.pending_prs)}"
        )
        pr_ids = [p.pr_id for p in result.pending_prs]
        assert 10 in pr_ids, f"Expected PR 10 in results, got {pr_ids}"
        assert 20 in pr_ids, f"Expected PR 20 in results, got {pr_ids}"
        for p in result.pending_prs:
            assert len(p.pending_reviewers) > 0, f"PR {p.pr_id} should have pending reviewers"

    def test_draft_pr_excluded(self) -> None:
        """
        Given a draft PR in the active list
        When analyze_pending_reviews is called
        Then the draft is excluded from results
        """
        # Given: one draft PR, one non-draft
        reviewer = _make_reviewer(vote=0)
        draft = _make_pr(pr_id=1, is_draft=True, reviewers=[reviewer])
        normal = _make_pr(pr_id=2, reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[draft, normal],
            commits_per_pr={2: [commit]},
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: only the non-draft appears
        pr_ids = [p.pr_id for p in result.pending_prs]
        assert 1 not in pr_ids, f"Draft PR should be excluded, got {pr_ids}"
        assert 2 in pr_ids, f"Non-draft PR 2 should be included, got {pr_ids}"

    def test_old_pr_excluded_by_max_days(self) -> None:
        """
        Given a PR older than max_days_old
        When analyze_pending_reviews is called
        Then the old PR is excluded from results
        """
        # Given: one PR 5 days old, one 45 days old, max_days_old=30
        reviewer = _make_reviewer(vote=0)
        young = _make_pr(
            pr_id=1,
            reviewers=[reviewer],
            creation_date=datetime.now(tz=UTC) - timedelta(days=5),
        )
        old = _make_pr(
            pr_id=2,
            reviewers=[reviewer],
            creation_date=datetime.now(tz=UTC) - timedelta(days=45),
        )
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[young, old],
            commits_per_pr={1: [commit]},
        )

        # When: analyzed with max_days_old=30
        result = analyze_pending_reviews(
            client,
            "Proj",
            "Repo",
            max_days_old=30,
        )

        # Then: only the young PR is included
        pr_ids = [p.pr_id for p in result.pending_prs]
        assert 1 in pr_ids, f"Young PR should be included, got {pr_ids}"
        assert 2 not in pr_ids, f"Old PR should be excluded, got {pr_ids}"

    def test_creator_filter_limits_results(self) -> None:
        """
        Given a creator_filter is provided
        When analyze_pending_reviews is called
        Then only PRs by that creator are returned
        """
        # Given: two PRs by different authors
        reviewer = _make_reviewer(vote=0)
        alice_pr = _make_pr(pr_id=1, author="alice@ex.com", reviewers=[reviewer])
        bob_pr = _make_pr(pr_id=2, author="bob@ex.com", reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[alice_pr, bob_pr],
            commits_per_pr={1: [commit]},
        )

        # When: filtered to alice
        result = analyze_pending_reviews(
            client,
            "Proj",
            "Repo",
            creator_filter="alice",
        )

        # Then: only Alice's PR is returned
        pr_ids = [p.pr_id for p in result.pending_prs]
        assert 1 in pr_ids, f"Alice's PR should be included, got {pr_ids}"
        assert 2 not in pr_ids, f"Bob's PR should be excluded, got {pr_ids}"

    def test_fully_approved_pr_excluded(self) -> None:
        """
        Given a PR with all reviewers approved (not stale) and no rejections
        When analyze_pending_reviews is called
        Then that PR is excluded from pending_prs (no attention needed)
        """
        # Given: PR with 2 approved reviewers, policy requires 2
        r1 = _make_reviewer(
            display_name="Bob",
            reviewer_id="guid-b",
            vote=10,
        )
        r2 = _make_reviewer(
            display_name="Carol",
            reviewer_id="guid-c",
            vote=10,
        )
        pr = _make_pr(pr_id=1, reviewers=[r1, r2])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
            policy_evaluations=[policy],
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: no pending PRs — fully approved
        assert len(result.pending_prs) == 0, (
            f"Expected 0 pending PRs (fully approved), got {len(result.pending_prs)}"
        )

    def test_stale_approval_includes_pr(self) -> None:
        """
        Given a PR with an approval invalidated by a new commit
        When analyze_pending_reviews is called
        Then needs_approvals_count reflects the stale approval and PR is included
        """
        # Given: PR with 1 approved reviewer who is stale (via PR properties)
        reviewer = _make_reviewer(
            display_name="Bob",
            reviewer_id="guid-b",
            vote=10,
        )
        pr = _make_pr(pr_id=1, reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(hours=1))
        # OneReviewPolicyPilot marks guid-b as stale
        stale_properties = {
            "OneReviewPolicyPilot": {
                "$value": '{"staleBecauseOfPush": ["guid-b"]}',
            },
        }
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
            properties_per_pr={1: stale_properties},
            policy_evaluations=[policy],
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: PR is included because the approval is stale
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR (stale approval), got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].needs_approvals_count == 2, (
            f"Expected needs_approvals_count=2 (stale approval doesn't count), "
            f"got {result.pending_prs[0].needs_approvals_count}"
        )

    def test_team_containers_deduplicated(self) -> None:
        """
        Given a PR with team container reviewers duplicating individual votes
        When analyze_pending_reviews is called
        Then containers are deduplicated and approval counts are correct
        """
        # Given: individual Alice approved + team container (voted_for Alice)
        team = _make_reviewer(
            display_name="Team",
            reviewer_id="guid-team",
            vote=10,
            is_container=True,
            voted_for=[],
        )
        alice = _make_reviewer(
            display_name="Alice",
            reviewer_id="guid-alice",
            vote=10,
            is_container=False,
            voted_for=[Mock(id="guid-team")],
        )
        pending = _make_reviewer(
            display_name="PendingGuy",
            reviewer_id="guid-pending",
            vote=0,
            is_container=False,
        )
        pr = _make_pr(pr_id=1, reviewers=[team, alice, pending])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
            policy_evaluations=[policy],
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: team is deduplicated — only 1 valid approval (Alice), needs 1 more
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].valid_approvals_count == 1, (
            f"Expected valid_approvals_count=1 (team deduplicated), "
            f"got {result.pending_prs[0].valid_approvals_count}"
        )

    def test_policy_based_required_count(self) -> None:
        """
        Given fetch_required_approvals returns a policy-based count
        When analyze_pending_reviews is called
        Then needs_approvals_count uses the policy count, not a hardcoded default
        """
        # Given: policy requires 3, only 1 approver
        reviewer = _make_reviewer(
            display_name="Bob",
            reviewer_id="guid-b",
            vote=10,
        )
        pending = _make_reviewer(
            display_name="Carol",
            reviewer_id="guid-c",
            vote=0,
        )
        pr = _make_pr(pr_id=1, reviewers=[reviewer, pending])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        policy = _make_policy_evaluation(min_approver_count=3)
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
            policy_evaluations=[policy],
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: needs 2 more (3 required - 1 valid)
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].needs_approvals_count == 2, (
            f"Expected needs_approvals_count=2 (3 policy - 1 valid), "
            f"got {result.pending_prs[0].needs_approvals_count}"
        )

    def test_results_sorted_by_days_open_descending(self) -> None:
        """
        Given multiple qualifying PRs
        When analyze_pending_reviews is called
        Then results are sorted by days_open descending
        """
        # Given: 3 PRs with different ages
        reviewer = _make_reviewer(vote=0)
        pr_new = _make_pr(
            pr_id=1,
            title="New",
            creation_date=datetime.now(tz=UTC) - timedelta(days=2),
            reviewers=[reviewer],
        )
        pr_mid = _make_pr(
            pr_id=2,
            title="Mid",
            creation_date=datetime.now(tz=UTC) - timedelta(days=10),
            reviewers=[reviewer],
        )
        pr_old = _make_pr(
            pr_id=3,
            title="Old",
            creation_date=datetime.now(tz=UTC) - timedelta(days=20),
            reviewers=[reviewer],
        )
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr_new, pr_mid, pr_old],
            commits_per_pr={1: [commit], 2: [commit], 3: [commit]},
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: sorted oldest first (descending days_open)
        days = [p.days_open for p in result.pending_prs]
        assert days == sorted(days, reverse=True), f"Expected days_open descending, got {days}"


# ---------------------------------------------------------------------------
# TestAnalyzePendingReviewsErrorHandling
# ---------------------------------------------------------------------------


class TestAnalyzePendingReviewsErrorHandling:
    """
    REQUIREMENT: analyze_pending_reviews raises ActionableError when the PR
    listing call fails, and surfaces per-PR enrichment failures as
    ActionableError instances in PendingReviewResult.skipped.

    WHO: Callers who need clear diagnostics when the API is unreachable or
         permissions are insufficient, and who want to report which PRs could
         not be analyzed.
    WHAT: If get_pull_requests() raises, wraps in ActionableError.connection().
          If per-PR enrichment fails, creates ActionableError.internal() naming
          the PR ID. Empty active PR list returns empty result, not an error.
    WHY: The N+1 enrichment pattern means one bad PR should not prevent
         analysis of the rest. The top-level listing failure is non-recoverable.

    MOCK BOUNDARY:
        Mock:  AdoClient — configure git.get_pull_requests to raise, or
               per-PR methods to raise
        Real:  analyze_pending_reviews(), error wrapping, PendingReviewResult
        Never: Catch ActionableError and re-wrap it
    """

    def test_listing_api_failure_raises_actionable_error(self) -> None:
        """
        Given the PR listing API raises an exception
        When analyze_pending_reviews is called
        Then ActionableError is raised with connection error details
        """
        # Given: get_pull_requests raises
        client = Mock()
        client.git.get_pull_requests.side_effect = RuntimeError("Service unavailable")

        # When/Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            analyze_pending_reviews(client, "Proj", "Repo")

        assert "Service unavailable" in str(exc_info.value), (
            f"Expected raw error in message, got: {exc_info.value}"
        )

    def test_per_pr_enrichment_failure_produces_skipped(self) -> None:
        """
        Given one PR's commit fetch raises but others succeed
        When analyze_pending_reviews is called
        Then the failing PR appears in result.skipped and remaining PRs
             are in result.pending_prs
        """
        # Given: PR 1 has commits, PR 2's commits raise
        reviewer = _make_reviewer(vote=0)
        pr1 = _make_pr(pr_id=1, reviewers=[reviewer])
        pr2 = _make_pr(pr_id=2, reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))

        client = _mock_client(prs=[pr1, pr2])
        # Override commit fetch to fail for PR 2

        def _get_commits(repo: str, pr_id: int, *, project: str) -> list[Mock]:
            if pr_id == 2:
                raise RuntimeError("Commit fetch failed")
            return [commit]

        client.git.get_pull_request_commits.side_effect = _get_commits

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: PR 1 succeeds, PR 2 is skipped
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].pr_id == 1, (
            f"Expected PR 1 in results, got {result.pending_prs[0].pr_id}"
        )
        assert len(result.skipped) == 1, f"Expected 1 skipped error, got {len(result.skipped)}"
        assert isinstance(result.skipped[0], ActionableError), (
            f"Expected ActionableError, got {type(result.skipped[0])}"
        )
        assert "2" in str(result.skipped[0]), (
            f"Expected PR ID 2 in error message, got: {result.skipped[0]}"
        )

    def test_no_active_prs_returns_empty_result(self) -> None:
        """
        Given no active PRs exist
        When analyze_pending_reviews is called
        Then returns PendingReviewResult with empty pending_prs and empty skipped
        """
        # Given: empty PR list
        client = _mock_client(prs=[])

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: empty result
        assert result.pending_prs == [], f"Expected empty pending_prs, got {result.pending_prs}"
        assert result.skipped == [], f"Expected empty skipped, got {result.skipped}"

    def test_multiple_prs_fail_enrichment(self) -> None:
        """
        Given multiple PRs fail enrichment
        When analyze_pending_reviews is called
        Then result.skipped contains an ActionableError per failed PR,
             each naming the PR ID
        """
        # Given: 3 PRs, all fail commit fetch
        reviewer = _make_reviewer(vote=0)
        pr1 = _make_pr(pr_id=10, reviewers=[reviewer])
        pr2 = _make_pr(pr_id=20, reviewers=[reviewer])
        pr3 = _make_pr(pr_id=30, reviewers=[reviewer])

        client = _mock_client(prs=[pr1, pr2, pr3])
        client.git.get_pull_request_commits.side_effect = RuntimeError("boom")

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: all 3 in skipped, none in pending_prs
        assert len(result.pending_prs) == 0, (
            f"Expected 0 pending PRs, got {len(result.pending_prs)}"
        )
        assert len(result.skipped) == 3, f"Expected 3 skipped errors, got {len(result.skipped)}"
        skipped_messages = [str(e) for e in result.skipped]
        for pr_id in [10, 20, 30]:
            found = any(str(pr_id) in msg for msg in skipped_messages)
            assert found, f"Expected PR {pr_id} named in skipped errors, got: {skipped_messages}"


# ---------------------------------------------------------------------------
# TestAnalyzePendingReviewsEdgeCases
# ---------------------------------------------------------------------------


class TestAnalyzePendingReviewsEdgeCases:
    """
    REQUIREMENT: analyze_pending_reviews handles edge cases in PR data
    gracefully.

    WHO: Library consumers processing PRs with unusual states.
    WHAT: PRs with no reviewers produce pending_reviewers=[] and
          needs_approvals_count equal to required count. PRs with None
          creation date are excluded. merge_status "conflicts" sets
          has_conflicts=True. creator_filter matching is case-insensitive.
    WHY: Real Azure DevOps data frequently has missing or unexpected values.
         Defensive handling prevents crashes during batch analysis.

    MOCK BOUNDARY:
        Mock:  AdoClient — return PRs with edge-case data shapes
        Real:  analyze_pending_reviews(), all filtering and data extraction
        Never: Skip the mock client — always exercise through the full function
    """

    def test_pr_with_no_reviewers(self) -> None:
        """
        Given a PR with no reviewers
        When analyze_pending_reviews is called
        Then it is included in pending_prs with empty pending_reviewers
             and needs_approvals_count equals the required count
        """
        # Given: PR with empty reviewers, policy requires 2
        pr = _make_pr(pr_id=1, reviewers=[])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
            policy_evaluations=[policy],
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: included with no pending reviewers, needs full count
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].pending_reviewers == [], (
            f"Expected empty pending_reviewers, got {result.pending_prs[0].pending_reviewers}"
        )
        assert result.pending_prs[0].needs_approvals_count == 2, (
            f"Expected needs_approvals_count=2, got {result.pending_prs[0].needs_approvals_count}"
        )

    def test_pr_with_none_creation_date_excluded(self) -> None:
        """
        Given a PR with None creation date
        When analyze_pending_reviews is called
        Then that PR is excluded from pending_prs (not an error — just skipped)
        """
        # Given: PR with None creation_date
        reviewer = _make_reviewer(vote=0)
        bad_pr = _make_pr(pr_id=1, creation_date=None, reviewers=[reviewer])
        good_pr = _make_pr(pr_id=2, reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[bad_pr, good_pr],
            commits_per_pr={2: [commit]},
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: only good_pr appears
        pr_ids = [p.pr_id for p in result.pending_prs]
        assert 1 not in pr_ids, f"PR with None date should be excluded, got {pr_ids}"
        assert 2 in pr_ids, f"Good PR should be included, got {pr_ids}"

    def test_merge_conflicts_sets_has_conflicts(self) -> None:
        """
        Given a PR with merge conflicts
        When analyze_pending_reviews is called
        Then has_conflicts is True and merge_status is "conflicts"
        """
        # Given: PR with merge conflicts
        reviewer = _make_reviewer(vote=0)
        pr = _make_pr(pr_id=1, merge_status="conflicts", reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
        )

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: has_conflicts is True
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        assert result.pending_prs[0].has_conflicts is True, (
            f"Expected has_conflicts=True, got {result.pending_prs[0].has_conflicts}"
        )
        assert result.pending_prs[0].merge_status == "conflicts", (
            f"Expected merge_status='conflicts', got {result.pending_prs[0].merge_status}"
        )

    def test_creator_filter_case_insensitive(self) -> None:
        """
        Given a creator_filter with different casing than the author email
        When analyze_pending_reviews is called
        Then the PR is still matched
        """
        # Given: PR by alice@example.com, filter uses "ALICE"
        reviewer = _make_reviewer(vote=0)
        pr = _make_pr(pr_id=1, author="alice@example.com", reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
        )

        # When: filtered with uppercase
        result = analyze_pending_reviews(
            client,
            "Proj",
            "Repo",
            creator_filter="ALICE",
        )

        # Then: still matched
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR (case-insensitive match), got {len(result.pending_prs)}"
        )

    def test_properties_api_failure_gracefully_ignored(self) -> None:
        """
        Given get_pull_request_properties raises an exception
        When analyze_pending_reviews is called
        Then the PR is still enriched (properties are optional)
        """
        # Given: PR whose properties call will fail
        reviewer = _make_reviewer(vote=0)
        pr = _make_pr(pr_id=1, reviewers=[reviewer])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
        )
        client.git.get_pull_request_properties.side_effect = RuntimeError("403")

        # When: analyzed
        result = analyze_pending_reviews(client, "Proj", "Repo")

        # Then: PR still appears (properties failure is non-fatal)
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR despite properties failure, got {len(result.pending_prs)}"
        )
        assert not result.skipped, "Properties failure should not produce a skipped entry"

    def test_policy_fetch_failure_uses_default_count(self) -> None:
        """
        Given fetch_required_approvals raises ActionableError
        When analyze_pending_reviews is called
        Then default_required_approvals is used as the fallback
        """
        # Given: PR with one approval, policy call will fail
        reviewer = _make_reviewer(vote=10)  # approved
        pending = _make_reviewer(
            display_name="Carol",
            unique_name="carol@example.com",
            reviewer_id="guid-carol",
            vote=0,
        )
        pr = _make_pr(pr_id=1, reviewers=[reviewer, pending])
        commit = _make_commit(author_date=datetime.now(tz=UTC) - timedelta(days=1))
        client = _mock_client(
            prs=[pr],
            commits_per_pr={1: [commit]},
        )
        client.policy.get_policy_evaluations.side_effect = RuntimeError("503")

        # When: analyzed with default_required_approvals=2
        result = analyze_pending_reviews(
            client,
            "Proj",
            "Repo",
            default_required_approvals=2,
        )

        # Then: PR still needs attention (1 of 2 required)
        assert len(result.pending_prs) == 1, (
            f"Expected 1 pending PR, got {len(result.pending_prs)}"
        )
        pr_result = result.pending_prs[0]
        assert pr_result.needs_approvals_count == 1, (
            f"Expected 1 more approval needed (default=2, got 1), "
            f"got needs={pr_result.needs_approvals_count}"
        )
        assert pr_result.valid_approvals_count == 1, (
            f"Expected valid_approvals_count=1, got {pr_result.valid_approvals_count}"
        )

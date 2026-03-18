"""BDD tests for ado_workflows.review — review helpers and orchestrator.

Covers:
- TestFetchRequiredApprovals: policy evaluation parsing, defaults, error surfacing
- TestFetchVoteTimestamps: thread property extraction, dedup, malformed data
- TestGetReviewStatus: end-to-end orchestrator with all system layers running real

Public API surface (from src/ado_workflows/review.py):
    fetch_required_approvals(client: AdoClient, project: str, pr_id: int,
                             *, default_required_approvals: int = 2) -> int
    fetch_vote_timestamps(client: AdoClient, repository: str, pr_id: int,
                          project: str) -> dict[str, datetime]
    get_review_status(client: AdoClient, pr_id: int, project: str,
                      repository: str, *,
                      default_required_approvals: int = 2) -> ReviewStatus
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from actionable_errors import ActionableError

from ado_workflows.review import (
    fetch_required_approvals,
    fetch_vote_timestamps,
    get_review_status,
)

# ---------------------------------------------------------------------------
# Helpers for mock construction
# ---------------------------------------------------------------------------


def _mock_policy_client(evaluations: list[Mock] | None = None) -> Mock:
    """Return a mock AdoClient whose policy.get_policy_evaluations returns *evaluations*."""
    client = Mock()
    client.policy.get_policy_evaluations.return_value = evaluations or []
    return client


def _make_policy_evaluation(
    *,
    display_name: str = "Minimum number of reviewers",
    min_approver_count: int = 2,
) -> Mock:
    """Build a mock PolicyEvaluationRecord with nested configuration."""
    evaluation = Mock()
    evaluation.configuration.type.display_name = display_name
    evaluation.configuration.settings = {
        "minimumApproverCount": min_approver_count,
    }
    return evaluation


def _mock_thread_client(threads: list[Mock] | None = None) -> Mock:
    """Return a mock AdoClient whose git.get_threads returns *threads*."""
    client = Mock()
    client.git.get_threads.return_value = threads or []
    return client


def _make_vote_thread(
    *,
    reviewer_id: str = "guid-1",
    published_date: datetime | None = None,
) -> Mock:
    """Build a mock thread with a CodeReviewVotedByIdentity property.

    Mirrors real SDK behavior: ``$value`` is a thread-local identity
    reference number (e.g. ``"1"``), resolved via ``thread.identities``
    to an ``IdentityRef`` with the actual GUID.
    """
    thread = Mock()
    thread.published_date = published_date or datetime(
        2025,
        6,
        15,
        10,
        0,
        0,
        tzinfo=UTC,
    )
    thread.properties = {
        "CodeReviewVotedByIdentity": {
            "$type": "System.String",
            "$value": "1",
        },
    }
    thread.identities = {"1": Mock(id=reviewer_id)}
    return thread


def _make_reviewer(
    *,
    display_name: str = "Alice Dev",
    unique_name: str = "alice@example.com",
    reviewer_id: str = "guid-alice",
    vote: int = 0,
    is_container: bool | None = None,
    voted_for: list[Mock] | None = None,
) -> Mock:
    """Build a mock IdentityRefWithVote matching the Azure DevOps SDK.

    Attributes use snake_case (``display_name``, ``is_container``, etc.)
    and ``voted_for`` items are Mock objects with an ``.id`` attribute.
    """
    return Mock(
        display_name=display_name,
        unique_name=unique_name,
        id=reviewer_id,
        vote=vote,
        is_container=is_container,
        voted_for=voted_for,
        spec=["display_name", "unique_name", "id", "vote", "is_container", "voted_for"],
    )


def _make_commit(*, author_date: datetime) -> Mock:
    """Build a mock commit with an author.date attribute."""
    commit = Mock()
    commit.author.date = author_date
    return commit


def _mock_full_client(
    *,
    pr_title: str = "Fix widget rendering",
    pr_author: str = "alice@example.com",
    pr_url: str = "https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42",
    creation_date: datetime | None = None,
    reviewers: list[Mock] | None = None,
    commits: list[Mock] | None = None,
    pr_properties: dict[str, object] | None = None,
    vote_threads: list[Mock] | None = None,
    policy_evaluations: list[Mock] | None = None,
) -> Mock:
    """Build a fully-wired mock AdoClient for get_review_status tests.

    Configures git.get_pull_request_by_id, git.get_pull_request_commits,
    git.get_pull_request_properties, git.get_threads, and
    policy.get_policy_evaluations.
    """
    client = Mock()

    # PR details
    pr = Mock()
    pr.title = pr_title
    pr.created_by.unique_name = pr_author
    pr.url = pr_url
    pr.creation_date = creation_date or datetime(2025, 6, 1, tzinfo=UTC)
    pr.reviewers = reviewers or []
    client.git.get_pull_request_by_id.return_value = pr

    # Commits
    client.git.get_pull_request_commits.return_value = commits or []

    # PR properties — SDK returns a plain dict {"count": N, "value": {...}}
    client.git.get_pull_request_properties.return_value = {
        "value": pr_properties or {},
    }

    # Threads (for vote timestamps)
    client.git.get_threads.return_value = vote_threads or []

    # Policy evaluations
    client.policy.get_policy_evaluations.return_value = policy_evaluations or []

    return client


# ---------------------------------------------------------------------------
# TestFetchRequiredApprovals
# ---------------------------------------------------------------------------


class TestFetchRequiredApprovals:
    """
    REQUIREMENT: fetch_required_approvals() returns the minimum reviewer count
    configured via branch policies for a PR.

    WHO: get_review_status() for approval calculations.
    WHAT: Calls client.policy.get_policy_evaluations(project, artifact_id) where
          artifact_id = "vstfs:///CodeReview/CodeReviewId/{project}/{pr_id}".
          Searches evaluation results for a policy whose
          configuration.type.display_name contains "Minimum number of reviewers".
          Extracts configuration.settings["minimumApproverCount"].
          Returns default_required_approvals if no matching policy is found.
          Raises ActionableError if the API call fails, so the caller can
          decide whether to use the default and surface the failure.
    WHY: Different repositories have different branch policies. A configurable
         default handles repos without policies, while the SDK call handles
         repos with them.

    MOCK BOUNDARY:
        Mock:  client.policy.get_policy_evaluations
        Real:  fetch_required_approvals, AdoClient (the real dataclass),
               policy artifact ID construction
        Never: determine_vote_status, deduplicate_team_containers
    """

    def test_matching_policy_returns_configured_count(self) -> None:
        """
        Given policy evaluations contain "Minimum number of reviewers"
              with minimumApproverCount=3
        When fetch_required_approvals is called
        Then it returns 3
        """
        # Given: a policy evaluation with minimumApproverCount=3
        evaluation = _make_policy_evaluation(min_approver_count=3)
        client = _mock_policy_client([evaluation])

        # When: fetch_required_approvals is called
        result = fetch_required_approvals(client, "MyProject", 42)

        # Then: the configured count is returned
        assert result == 3, f"Expected 3 from policy, got {result}"

    def test_no_reviewer_policy_returns_default(self) -> None:
        """
        Given policy evaluations contain no reviewer policy
        When fetch_required_approvals is called
        Then it returns default_required_approvals
        """
        # Given: a policy evaluation that does NOT match "Minimum number of reviewers"
        evaluation = _make_policy_evaluation(display_name="Build validation")
        client = _mock_policy_client([evaluation])

        # When: fetch_required_approvals is called
        result = fetch_required_approvals(client, "MyProject", 42)

        # Then: the default is returned
        assert result == 2, f"Expected default 2 when no matching policy, got {result}"

    def test_empty_evaluations_returns_default(self) -> None:
        """
        Given policy evaluations is an empty list
        When fetch_required_approvals is called
        Then it returns default_required_approvals
        """
        # Given: empty policy evaluations
        client = _mock_policy_client([])

        # When: fetch_required_approvals is called
        result = fetch_required_approvals(client, "MyProject", 42)

        # Then: the default is returned
        assert result == 2, f"Expected default 2 for empty evaluations, got {result}"

    def test_api_exception_raises_actionable_error(self) -> None:
        """
        Given the policy API raises an exception
        When fetch_required_approvals is called
        Then it raises ActionableError so the caller can decide on fallback
        """
        # Given: the policy API raises RuntimeError
        client = Mock()
        client.policy.get_policy_evaluations.side_effect = RuntimeError("API down")

        # When/Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            fetch_required_approvals(client, "MyProject", 42)

        assert "API down" in str(exc_info.value), (
            f"Expected raw error in message, got: {exc_info.value}"
        )

    def test_custom_default_returned_when_no_policy(self) -> None:
        """
        Given default_required_approvals=4 and no matching policy
        When fetch_required_approvals is called
        Then it returns 4
        """
        # Given: no matching policy, custom default of 4
        client = _mock_policy_client([])

        # When: called with default_required_approvals=4
        result = fetch_required_approvals(
            client,
            "MyProject",
            42,
            default_required_approvals=4,
        )

        # Then: the custom default is returned
        assert result == 4, f"Expected custom default 4, got {result}"

    def test_multiple_evaluations_extracts_matching_one(self) -> None:
        """
        Given multiple policy evaluations, one matching "Minimum number of reviewers"
        When fetch_required_approvals is called
        Then it extracts minimumApproverCount from the matching evaluation
        """
        # Given: two policies — one non-matching, one matching with count=5
        non_matching = _make_policy_evaluation(display_name="Build validation")
        matching = _make_policy_evaluation(min_approver_count=5)
        client = _mock_policy_client([non_matching, matching])

        # When: fetch_required_approvals is called
        result = fetch_required_approvals(client, "MyProject", 42)

        # Then: the value from the matching policy is returned
        assert result == 5, f"Expected 5 from matching policy among multiple, got {result}"


# ---------------------------------------------------------------------------
# TestFetchVoteTimestamps
# ---------------------------------------------------------------------------


class TestFetchVoteTimestamps:
    """
    REQUIREMENT: fetch_vote_timestamps() extracts per-reviewer vote datetimes
    from PR thread properties.

    WHO: get_review_status() for tier-2 staleness detection.
    WHAT: Calls client.git.get_threads(repository, pr_id, project=project).
          For each thread, checks thread.properties for a
          CodeReviewVotedByIdentity key. The ``$value`` is a thread-local
          identity reference number resolved via ``thread.identities`` to
          obtain the actual reviewer GUID. Uses thread.published_date as
          the vote timestamp.
          Returns {reviewer_guid: vote_datetime} mapping.
    WHY: ADO has no public API for "when did reviewer X vote". The thread-based
         approach is the only reliable method. Documented as fragile.

    MOCK BOUNDARY:
        Mock:  client.git.get_threads
        Real:  fetch_vote_timestamps, thread property parsing, datetime extraction
        Never: determine_vote_status
    """

    def test_threads_with_vote_properties_return_mapping(self) -> None:
        """
        Given threads with CodeReviewVotedByIdentity properties
        When fetch_vote_timestamps is called
        Then it returns {reviewer_id: timestamp} mapping
        """
        # Given: two threads each with a different reviewer vote
        ts1 = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 16, 14, 30, 0, tzinfo=UTC)
        thread1 = _make_vote_thread(reviewer_id="guid-a", published_date=ts1)
        thread2 = _make_vote_thread(reviewer_id="guid-b", published_date=ts2)
        client = _mock_thread_client([thread1, thread2])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: both reviewers are in the mapping with correct timestamps
        assert result == {"guid-a": ts1, "guid-b": ts2}, (
            f"Expected mapping for guid-a and guid-b, got {result}"
        )

    def test_threads_without_vote_properties_return_empty(self) -> None:
        """
        Given threads with no CodeReviewVotedByIdentity
        When fetch_vote_timestamps is called
        Then it returns an empty dict
        """
        # Given: a thread with no vote properties
        thread = Mock()
        thread.properties = {"SomeOtherProperty": {"$value": "whatever"}}
        thread.published_date = datetime(2025, 6, 15, tzinfo=UTC)
        client = _mock_thread_client([thread])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict
        assert result == {}, (
            f"Expected empty dict for threads without vote properties, got {result}"
        )

    def test_empty_thread_list_returns_empty(self) -> None:
        """
        Given an empty thread list
        When fetch_vote_timestamps is called
        Then it returns an empty dict
        """
        # Given: no threads
        client = _mock_thread_client([])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict
        assert result == {}, f"Expected empty dict for no threads, got {result}"

    def test_malformed_properties_skipped(self) -> None:
        """
        Given a thread with malformed properties
        When fetch_vote_timestamps is called
        Then it skips that thread and returns others
        """
        # Given: one malformed thread (properties is None) and one valid thread
        malformed = Mock()
        malformed.properties = None
        malformed.published_date = datetime(2025, 6, 15, tzinfo=UTC)

        ts_valid = datetime(2025, 6, 16, 10, 0, 0, tzinfo=UTC)
        valid = _make_vote_thread(reviewer_id="guid-ok", published_date=ts_valid)

        client = _mock_thread_client([malformed, valid])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: only the valid thread is in the result
        assert result == {"guid-ok": ts_valid}, f"Expected only guid-ok, got {result}"

    def test_multiple_votes_same_reviewer_keeps_latest(self) -> None:
        """
        Given multiple vote threads for the same reviewer
        When fetch_vote_timestamps is called
        Then it keeps the latest timestamp
        """
        # Given: two threads for the same reviewer, different timestamps
        ts_old = datetime(2025, 6, 10, 8, 0, 0, tzinfo=UTC)
        ts_new = datetime(2025, 6, 20, 16, 0, 0, tzinfo=UTC)
        thread_old = _make_vote_thread(reviewer_id="guid-x", published_date=ts_old)
        thread_new = _make_vote_thread(reviewer_id="guid-x", published_date=ts_new)
        client = _mock_thread_client([thread_old, thread_new])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: the latest timestamp is kept
        assert result == {"guid-x": ts_new}, f"Expected latest timestamp {ts_new}, got {result}"

    def test_empty_value_in_vote_property_skipped(self) -> None:
        """
        Given a thread whose CodeReviewVotedByIdentity has an empty $value
        When fetch_vote_timestamps is called
        Then that thread is skipped
        """
        # Given: a thread with empty $value in the vote property
        thread = Mock()
        thread.published_date = datetime(2025, 6, 15, tzinfo=UTC)
        thread.properties = {
            "CodeReviewVotedByIdentity": {"$type": "System.String", "$value": ""},
        }
        client = _mock_thread_client([thread])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict (thread was skipped)
        assert result == {}, f"Expected empty dict for empty $value, got {result}"

    def test_none_published_date_skipped(self) -> None:
        """
        Given a thread with a valid vote property but None published_date
        When fetch_vote_timestamps is called
        Then that thread is skipped
        """
        # Given: a thread with valid vote property but None published_date
        thread = Mock()
        thread.published_date = None
        thread.properties = {
            "CodeReviewVotedByIdentity": {
                "$type": "System.String",
                "$value": "1",
            },
        }
        thread.identities = {"1": Mock(id="guid-no-date")}
        client = _mock_thread_client([thread])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict (thread was skipped)
        assert result == {}, f"Expected empty dict for None published_date, got {result}"

    def test_identity_ref_not_in_identities_skipped(self) -> None:
        """
        Given a thread whose identity ref number is not in thread.identities
        When fetch_vote_timestamps is called
        Then that thread is skipped
        """
        # Given: $value is "1" but identities dict doesn't contain key "1"
        thread = Mock()
        thread.published_date = datetime(2025, 6, 15, tzinfo=UTC)
        thread.properties = {
            "CodeReviewVotedByIdentity": {
                "$type": "System.String",
                "$value": "1",
            },
        }
        thread.identities = {"2": Mock(id="guid-other")}
        client = _mock_thread_client([thread])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict (identity ref not resolved)
        assert result == {}, f"Expected empty dict when identity ref not found, got {result}"

    def test_identity_with_empty_id_skipped(self) -> None:
        """
        Given a thread whose resolved identity has an empty id
        When fetch_vote_timestamps is called
        Then that thread is skipped
        """
        # Given: identity ref resolves but the identity object has empty id
        thread = Mock()
        thread.published_date = datetime(2025, 6, 15, tzinfo=UTC)
        thread.properties = {
            "CodeReviewVotedByIdentity": {
                "$type": "System.String",
                "$value": "1",
            },
        }
        thread.identities = {"1": Mock(id="")}
        client = _mock_thread_client([thread])

        # When: fetch_vote_timestamps is called
        result = fetch_vote_timestamps(client, "MyRepo", 42, "MyProject")

        # Then: empty dict (identity has no GUID)
        assert result == {}, f"Expected empty dict for empty identity id, got {result}"


# ---------------------------------------------------------------------------
# TestGetReviewStatus
# ---------------------------------------------------------------------------


class TestGetReviewStatus:
    """
    REQUIREMENT: get_review_status() computes the full review status for a PR,
    including approval calculations, staleness detection, and human-readable summary.

    WHO: MCP tools, CI integrations, any consumer needing PR review state.
    WHAT: Orchestrates: fetch PR details, fetch commits, fetch PR properties for
          stale voter IDs, fetch vote timestamps from threads, classify each
          reviewer's vote via determine_vote_status, deduplicate team containers,
          fetch required approvals, compute approval status and build summary.
          Returns a ReviewStatus dataclass.
          Raises ActionableError for unrecoverable errors (PR not found, auth).
          Catches recoverable enrichment failures (policy lookup, PR properties)
          and surfaces them as ActionableError instances in ReviewStatus.warnings.
    WHY: This is the primary read operation for PR review workflows. PDP's
         version used 20 subprocess calls; this uses 4-5 SDK calls. Surfacing
         warnings rather than logging them ensures consumers can report degradation.

    MOCK BOUNDARY:
        Mock:  client.git.get_pull_request_by_id,
               client.git.get_pull_request_commits,
               client.git.get_pull_request_properties,
               client.git.get_threads,
               client.policy.get_policy_evaluations
        Real:  get_review_status, determine_vote_status,
               deduplicate_team_containers, fetch_required_approvals,
               fetch_vote_timestamps, all dataclasses
        Never: N/A — all system layers run for real; only SDK I/O is mocked
    """

    def test_two_approved_reviewers_meets_required_count(self) -> None:
        """
        Given a PR with 2 approved reviewers and required=2
        When get_review_status is called
        Then ReviewStatus.approval_status.is_approved is True and summary
             contains "Ready to merge"
        """
        # Given: 2 approved reviewers, policy requires 2
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
            _make_reviewer(
                display_name="Bob",
                reviewer_id="guid-b",
                vote=10,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=2)
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: approved with correct summary
        assert result.approval_status.is_approved is True, (
            f"Expected is_approved=True, got {result.approval_status}"
        )
        assert "Ready to merge" in result.summary, (
            f"Expected 'Ready to merge' in summary, got: {result.summary}"
        )

    def test_one_approved_reviewer_needs_more(self) -> None:
        """
        Given a PR with 1 approved reviewer and required=2
        When get_review_status is called
        Then needs_approvals_count=1 and summary contains "Needs 1 approval(s)"
        """
        # Given: 1 approved reviewer, policy requires 2
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=2)
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: needs 1 more approval
        assert result.approval_status.needs_approvals_count == 1, (
            f"Expected needs_approvals_count=1, got {result.approval_status.needs_approvals_count}"
        )
        assert "Needs 1 approval(s)" in result.summary, (
            f"Expected 'Needs 1 approval(s)' in summary, got: {result.summary}"
        )

    def test_rejecting_reviewer_blocks_pr(self) -> None:
        """
        Given a PR with a rejecting reviewer
        When get_review_status is called
        Then has_rejection is True and summary contains "BLOCKED: Rejected by"
        """
        # Given: one rejecting reviewer
        reviewers = [
            _make_reviewer(
                display_name="Carol",
                reviewer_id="guid-c",
                vote=-10,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_full_client(
            reviewers=reviewers,
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: rejection flagged
        assert result.approval_status.has_rejection is True, (
            f"Expected has_rejection=True, got {result.approval_status}"
        )
        assert "BLOCKED: Rejected by" in result.summary, (
            f"Expected 'BLOCKED: Rejected by' in summary, got: {result.summary}"
        )

    def test_stale_approval_from_pr_properties(self) -> None:
        """
        Given a PR with invalidated approvals (stale voter IDs from properties)
        When get_review_status is called
        Then invalidated_approvers is populated
        """
        # Given: reviewer approved, but their ID appears in stale voter IDs
        # from PR properties (OneReviewPolicyPilot tier-1 staleness)
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        # PR properties: OneReviewPolicyPilot contains stale voter IDs
        pr_properties: dict[str, object] = {
            "OneReviewPolicyPilot": {
                "$type": "System.String",
                "$value": '{"staleBecauseOfPush":["guid-a"]}',
            },
        }
        policy = _make_policy_evaluation(min_approver_count=1)
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            pr_properties=pr_properties,
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: the approval is invalidated
        assert len(result.approval_status.invalidated_approvers) == 1, (
            f"Expected 1 invalidated approver, got "
            f"{len(result.approval_status.invalidated_approvers)}"
        )
        assert result.approval_status.invalidated_approvers[0].name == "Alice", (
            f"Expected invalidated approver 'Alice', got "
            f"{result.approval_status.invalidated_approvers[0].name}"
        )

    def test_fallback_staleness_from_vote_timestamp(self) -> None:
        """
        Given a PR where a reviewer's vote timestamp is before the last commit
        When get_review_status is called
        Then that reviewer's approval is invalidated via tier-2 detection
        """
        # Given: reviewer voted before the latest commit (tier-2 staleness)
        reviewers = [
            _make_reviewer(
                display_name="Bob",
                reviewer_id="guid-b",
                vote=10,
            ),
        ]
        # Latest commit is June 20; Bob voted June 10
        commit = _make_commit(
            author_date=datetime(2025, 6, 20, tzinfo=UTC),
        )
        vote_thread = _make_vote_thread(
            reviewer_id="guid-b",
            published_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        policy = _make_policy_evaluation(min_approver_count=1)
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            vote_threads=[vote_thread],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: Bob's approval is invalidated via tier-2
        assert len(result.approval_status.invalidated_approvers) == 1, (
            f"Expected 1 invalidated approver via tier-2, got "
            f"{len(result.approval_status.invalidated_approvers)}"
        )
        assert result.approval_status.invalidated_approvers[0].name == "Bob", (
            f"Expected 'Bob' invalidated, got "
            f"{result.approval_status.invalidated_approvers[0].name}"
        )

    def test_waiting_for_author_reviewer(self) -> None:
        """
        Given a PR with a waiting-for-author reviewer
        When get_review_status is called
        Then waiting_reviewers is populated and summary mentions
             "Waiting for author"
        """
        # Given: one reviewer with vote=-5 (waiting for author)
        reviewers = [
            _make_reviewer(
                display_name="Dave",
                reviewer_id="guid-d",
                vote=-5,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_full_client(
            reviewers=reviewers,
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: waiting_reviewers populated
        assert len(result.approval_status.waiting_reviewers) == 1, (
            f"Expected 1 waiting reviewer, got {len(result.approval_status.waiting_reviewers)}"
        )
        assert "Waiting for author" in result.summary, (
            f"Expected 'Waiting for author' in summary, got: {result.summary}"
        )

    def test_no_reviewers_needs_all_approvals(self) -> None:
        """
        Given a PR with no reviewers
        When get_review_status is called
        Then pending_reviewers is empty and needs_approvals_count equals required
        """
        # Given: no reviewers, policy requires 2
        policy = _make_policy_evaluation(min_approver_count=2)
        client = _mock_full_client(
            reviewers=[],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: needs all approvals
        assert result.approval_status.needs_approvals_count == 2, (
            f"Expected needs_approvals_count=2, got {result.approval_status.needs_approvals_count}"
        )
        assert result.approval_status.is_approved is False, (
            "Expected is_approved=False with no reviewers"
        )

    def test_team_containers_deduplicated(self) -> None:
        """
        Given a PR with team containers duplicating individual votes
        When get_review_status is called
        Then containers are deduplicated and approval count excludes duplicates
        """
        # Given: individual Alice (approved, votedFor team-1) + container team-1 (approved)
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
                voted_for=[Mock(id="team-1")],
            ),
            _make_reviewer(
                display_name="Team Alpha",
                reviewer_id="team-1",
                vote=10,
                is_container=True,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=1)
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: only 1 valid approver (container deduplicated)
        assert len(result.approval_status.valid_approvers) == 1, (
            f"Expected 1 valid approver after dedup, got "
            f"{len(result.approval_status.valid_approvers)}"
        )
        assert result.approval_status.valid_approvers[0].name == "Alice", (
            f"Expected 'Alice' as valid approver, got "
            f"{result.approval_status.valid_approvers[0].name}"
        )

    def test_pr_not_found_raises_actionable_error(self) -> None:
        """
        Given the SDK raises an exception for PR not found
        When get_review_status is called
        Then it raises ActionableError
        """
        # Given: SDK raises on get_pull_request_by_id
        client = Mock()
        client.git.get_pull_request_by_id.side_effect = Exception(
            "TF401180: Pull request 999 was not found",
        )

        # When/Then: ActionableError is raised
        with pytest.raises(ActionableError) as exc_info:
            get_review_status(client, 999, "MyProject", "MyRepo")

        assert "999" in str(exc_info.value), (
            f"Expected PR ID in error message, got: {exc_info.value}"
        )

    def test_no_commits_sets_last_commit_date_none(self) -> None:
        """
        Given a PR with no commits
        When get_review_status is called
        Then last_commit_date is None and no staleness detection errors
        """
        # Given: no commits
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        policy = _make_policy_evaluation(min_approver_count=1)
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: last_commit_date is None, runs without error
        assert result.last_commit_date is None, (
            f"Expected last_commit_date=None, got {result.last_commit_date}"
        )

    def test_all_fields_populated_in_result(self) -> None:
        """
        Given a PR with all fields populated
        When get_review_status is called
        Then ReviewStatus contains correct pr_id, title, author, url, days_open
        """
        # Given: a fully-configured PR
        creation = datetime(2025, 5, 1, tzinfo=UTC)
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        policy = _make_policy_evaluation(min_approver_count=1)
        client = _mock_full_client(
            pr_title="Add widget tests",
            pr_author="alice@example.com",
            pr_url="https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42",
            creation_date=creation,
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[policy],
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: all metadata fields are correct
        assert result.pr_id == 42, f"Expected pr_id=42, got {result.pr_id}"
        assert result.title == "Add widget tests", (
            f"Expected title='Add widget tests', got {result.title!r}"
        )
        assert result.author == "alice@example.com", (
            f"Expected author='alice@example.com', got {result.author!r}"
        )
        assert result.url == "https://dev.azure.com/Org/Proj/_git/Repo/pullrequest/42", (
            f"Expected correct URL, got {result.url!r}"
        )
        assert result.days_open >= 0, f"Expected days_open >= 0, got {result.days_open}"

    def test_properties_fetch_failure_skips_staleness(self) -> None:
        """
        Given the SDK raises an exception when fetching PR properties
        When get_review_status is called
        Then it still returns a result without stale voter detection
        """
        # Given: an approved reviewer, but properties fetch fails
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        policy = _make_policy_evaluation(min_approver_count=1)
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[policy],
        )
        # Simulate properties fetch failure
        client.git.get_pull_request_properties.side_effect = Exception(
            "Network error",
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: the approval should be valid (no stale detection)
        assert result.approval_status.is_approved is True, (
            "Expected approval to succeed despite properties failure"
        )
        assert len(result.approval_status.invalidated_approvers) == 0, (
            "Expected no invalidated approvers when properties unavailable"
        )
        # And: the failure is surfaced as a warning
        assert len(result.warnings) == 1, (
            f"Expected 1 warning for properties failure, got {len(result.warnings)}"
        )
        assert "get_pull_request_properties" in str(result.warnings[0]), (
            f"Expected properties operation in warning, got: {result.warnings[0]}"
        )

    def test_policy_api_failure_produces_warning(self) -> None:
        """
        Given policy API fails during enrichment
        When get_review_status is called
        Then ReviewStatus.warnings contains an ActionableError,
             approval uses default count
        """
        # Given: a PR with 1 approved reviewer, but policy API fails
        reviewers = [
            _make_reviewer(
                display_name="Alice",
                reviewer_id="guid-a",
                vote=10,
            ),
        ]
        commit = _make_commit(
            author_date=datetime(2025, 6, 10, tzinfo=UTC),
        )
        client = _mock_full_client(
            reviewers=reviewers,
            commits=[commit],
            policy_evaluations=[],  # not used — will fail first
        )
        # Simulate policy API failure
        client.policy.get_policy_evaluations.side_effect = RuntimeError(
            "Policy service unavailable",
        )

        # When: get_review_status is called
        result = get_review_status(client, 42, "MyProject", "MyRepo")

        # Then: approval uses default count (2), so 1 approver is not enough
        assert result.approval_status.is_approved is False, (
            "Expected not approved when using default of 2 with only 1 approver"
        )
        assert result.approval_status.needs_approvals_count == 1, (
            f"Expected needs_approvals_count=1, got {result.approval_status.needs_approvals_count}"
        )
        # And: the failure is surfaced as a warning
        assert len(result.warnings) == 1, (
            f"Expected 1 warning for policy failure, got {len(result.warnings)}"
        )
        assert "fetch_required_approvals" in str(result.warnings[0]), (
            f"Expected fetch_required_approvals in warning, got: {result.warnings[0]}"
        )

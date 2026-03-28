"""
BDD tests for ado_workflows.votes — vote classification and team deduplication.

Covers:
- TestVoteTextMapping: VOTE_TEXT constant maps vote integers to human-readable text
- TestDetermineVoteStatus: classify reviewer votes with two-tier staleness detection
- TestDeduplicateTeamContainers: remove team containers represented by individual voters

Public API surface (from src/ado_workflows/votes.py):
    determine_vote_status(
        reviewer: Any,
        stale_voter_ids: set[str] | None = None,
        vote_timestamps: dict[str, datetime] | None = None,
        latest_commit_date: datetime | None = None,
    ) -> VoteStatus

    deduplicate_team_containers(vote_statuses: list[VoteStatus]) -> list[VoteStatus]

Public API surface (from src/ado_workflows/models.py):
    VOTE_TEXT: dict[int, str]
    VoteStatus(name, email, vote, vote_text, vote_invalidated, invalidated_by_commit,
               is_container, voted_for_ids, reviewer_id)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

from ado_workflows.models import VOTE_TEXT, VoteStatus
from ado_workflows.votes import deduplicate_team_containers, determine_vote_status


def _reviewer(
    *,
    display_name: str | None = "Unknown",
    unique_name: str | None = "",
    reviewer_id: str = "default-id",
    vote: int = 0,
    is_container: bool | None = None,
    voted_for: list[Mock] | None = None,
) -> Mock:
    """
    Build a mock IdentityRefWithVote matching the Azure DevOps SDK.

    Attributes use snake_case (``display_name``, ``is_container``, etc.)
    and ``voted_for`` items are Mock objects with an ``.id`` attribute,
    matching ``IdentityRefWithVote`` nesting.
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


class TestVoteTextMapping:
    """
    REQUIREMENT: Vote integers map to human-readable text via VOTE_TEXT constant.

    WHO: Any consumer that needs to display vote status to humans or LLMs.
    WHAT: (1) all known vote integers map to their expected human-readable text
          (2) unknown vote values produce "Unknown vote: {value}" in
              determine_vote_status
    WHY: Vote integers are an ADO API implementation detail. Every consumer needs
         human-readable text, and centralizing the mapping prevents inconsistency.

    MOCK BOUNDARY:
        Mock:  nothing — pure constant access
        Real:  VOTE_TEXT
        Never: N/A
    """

    def test_all_known_vote_values_map_to_expected_text(self) -> None:
        """
        Given the VOTE_TEXT constant
        When accessed with each known vote integer
        Then returns the expected human-readable text
        """
        # Given: the expected mapping
        expected = {
            10: "Approved",
            5: "Approved with suggestions",
            0: "No vote",
            -5: "Waiting for author",
            -10: "Rejected",
        }

        # When / Then: each key maps to the expected text
        for vote_value, expected_text in expected.items():
            assert VOTE_TEXT[vote_value] == expected_text, (
                f"Expected VOTE_TEXT[{vote_value}] to be '{expected_text}', "
                f"got '{VOTE_TEXT[vote_value]}'"
            )

    def test_unknown_vote_value_produces_unknown_text_in_vote_status(self) -> None:
        """
        Given a reviewer with an unknown vote integer (e.g., 99)
        When determine_vote_status is called
        Then vote_text is "Unknown vote: 99"
        """
        # Given: a reviewer dict with an unknown vote value
        reviewer = _reviewer(
            display_name="Test User",
            unique_name="test@contoso.com",
            reviewer_id="user-1",
            vote=99,
        )

        # When: determine_vote_status classifies the vote
        status = determine_vote_status(reviewer)

        # Then: the vote_text indicates the unknown value
        assert status.vote_text == "Unknown vote: 99", (
            f"Expected vote_text 'Unknown vote: 99', got '{status.vote_text}'"
        )


class TestDetermineVoteStatus:
    """
    REQUIREMENT: determine_vote_status() classifies a single reviewer's raw ADO
    API dict into a typed VoteStatus, applying two-tier staleness detection for
    approvals.

    WHO: Phase 6c functions get_pr_review_status() and send_pr_review_reminders().
    WHAT: (1) an approved vote without staleness data has vote_invalidated=False
          (2) an approved vote in stale_voter_ids has vote_invalidated=True
          (3) an approved vote whose timestamp predates the latest commit
              has vote_invalidated=True via timestamp fallback
          (4) staleness from both policy and timestamp still produces
              vote_invalidated=True
          (5) an approved vote whose timestamp follows the latest commit
              has vote_invalidated=False (fresh)
          (6) a no-vote (vote=0) is not affected by staleness data
          (7) a rejected vote is classified correctly without invalidation
          (8) a waiting-for-author vote is classified correctly without
              invalidation
          (9) a container with null voted_for has voted_for_ids=[]
          (10) an individual with voted_for IDs has those IDs extracted
          (11) missing display_name defaults to "Unknown"
    WHY: Centralizes vote classification so every consumer gets consistent
         staleness detection. PDP had send_pr_review_reminders skip this entirely.

    MOCK BOUNDARY:
        Mock:  nothing — pure function
        Real:  determine_vote_status, VoteStatus, VOTE_TEXT
        Never: N/A
    """

    def test_approved_vote_without_staleness_data(self) -> None:
        """
        Given a reviewer with vote=10 and no staleness data
        When determine_vote_status is called
        Then VoteStatus has vote_text="Approved" and vote_invalidated=False
        """
        # Given: an approved reviewer with no staleness parameters
        reviewer = _reviewer(
            display_name="Alice Smith",
            unique_name="alice@contoso.com",
            reviewer_id="alice-id",
            vote=10,
        )

        # When: the vote is classified
        status = determine_vote_status(reviewer)

        # Then: approved without invalidation
        assert status.vote_text == "Approved", (
            f"Expected vote_text 'Approved', got '{status.vote_text}'"
        )
        assert status.vote_invalidated is False, (
            f"Expected vote_invalidated False, got {status.vote_invalidated}"
        )
        assert status.name == "Alice Smith", f"Expected name 'Alice Smith', got '{status.name}'"
        assert status.email == "alice@contoso.com", (
            f"Expected email 'alice@contoso.com', got '{status.email}'"
        )

    def test_approved_vote_stale_by_policy(self) -> None:
        """
        Given a reviewer with vote=10 and reviewer_id in stale_voter_ids
        When determine_vote_status is called
        Then vote_invalidated=True and invalidated_by_commit=True
        """
        # Given: an approved reviewer marked stale by ADO policy
        reviewer = _reviewer(
            display_name="Alice Smith",
            unique_name="alice@contoso.com",
            reviewer_id="alice-id",
            vote=10,
        )
        stale_ids = {"alice-id"}

        # When: the vote is classified with staleness data
        status = determine_vote_status(reviewer, stale_voter_ids=stale_ids)

        # Then: vote is invalidated by the policy
        assert status.vote_invalidated is True, (
            f"Expected vote_invalidated True (in stale_voter_ids), got {status.vote_invalidated}"
        )
        assert status.invalidated_by_commit is True, (
            f"Expected invalidated_by_commit True, got {status.invalidated_by_commit}"
        )

    def test_approved_vote_stale_by_timestamp_fallback(self) -> None:
        """
        Given a reviewer with vote=10 whose vote timestamp is before latest commit
        When determine_vote_status is called
        Then vote_invalidated=True via timestamp fallback
        """
        # Given: an approved reviewer whose vote predates the latest commit
        reviewer = _reviewer(
            display_name="Bob Jones",
            unique_name="bob@contoso.com",
            reviewer_id="bob-id",
            vote=10,
        )
        vote_timestamps = {"bob-id": datetime(2026, 3, 1, 10, 0, 0)}
        latest_commit = datetime(2026, 3, 2, 14, 0, 0)

        # When: the vote is classified with timestamp data
        status = determine_vote_status(
            reviewer,
            vote_timestamps=vote_timestamps,
            latest_commit_date=latest_commit,
        )

        # Then: vote is invalidated by the timestamp fallback
        assert status.vote_invalidated is True, (
            f"Expected vote_invalidated True (timestamp fallback), got {status.vote_invalidated}"
        )
        assert status.invalidated_by_commit is True, (
            f"Expected invalidated_by_commit True, got {status.invalidated_by_commit}"
        )

    def test_approved_vote_stale_by_both_policy_and_timestamp(self) -> None:
        """
        Given a reviewer with vote=10, in stale_voter_ids AND timestamp stale
        When determine_vote_status is called
        Then vote_invalidated=True (primary takes precedence)
        """
        # Given: a reviewer stale by both detection methods
        reviewer = _reviewer(
            display_name="Alice Smith",
            unique_name="alice@contoso.com",
            reviewer_id="alice-id",
            vote=10,
        )
        stale_ids = {"alice-id"}
        vote_timestamps = {"alice-id": datetime(2026, 3, 1, 10, 0, 0)}
        latest_commit = datetime(2026, 3, 2, 14, 0, 0)

        # When: both staleness sources agree
        status = determine_vote_status(
            reviewer,
            stale_voter_ids=stale_ids,
            vote_timestamps=vote_timestamps,
            latest_commit_date=latest_commit,
        )

        # Then: vote is invalidated (primary takes precedence but both agree)
        assert status.vote_invalidated is True, (
            f"Expected vote_invalidated True, got {status.vote_invalidated}"
        )

    def test_approved_vote_fresh_by_timestamp(self) -> None:
        """
        Given a reviewer with vote=10 whose vote timestamp is after the latest commit
        When determine_vote_status is called
        Then vote_invalidated=False (vote is fresh)
        """
        # Given: an approved reviewer whose vote postdates the latest commit
        reviewer = _reviewer(
            display_name="Grace Hopper",
            unique_name="grace@contoso.com",
            reviewer_id="grace-id",
            vote=10,
        )
        vote_timestamps = {"grace-id": datetime(2026, 3, 5, 10, 0, 0)}
        latest_commit = datetime(2026, 3, 2, 14, 0, 0)

        # When: the vote is classified with timestamp data (no stale IDs)
        status = determine_vote_status(
            reviewer,
            vote_timestamps=vote_timestamps,
            latest_commit_date=latest_commit,
        )

        # Then: vote is NOT invalidated — it's fresh
        assert status.vote_invalidated is False, (
            f"Expected vote_invalidated False (vote is after commit), "
            f"got {status.vote_invalidated}"
        )
        assert status.vote_text == "Approved", (
            f"Expected vote_text 'Approved', got '{status.vote_text}'"
        )

    def test_no_vote_not_affected_by_staleness(self) -> None:
        """
        Given a reviewer with vote=0 and reviewer_id in stale_voter_ids
        When determine_vote_status is called
        Then vote_invalidated=False (staleness only applies to approvals)
        """
        # Given: a pending reviewer whose ID is in the stale list
        reviewer = _reviewer(
            display_name="Charlie Brown",
            unique_name="charlie@contoso.com",
            reviewer_id="charlie-id",
            vote=0,
        )
        stale_ids = {"charlie-id"}

        # When: classified with staleness data
        status = determine_vote_status(reviewer, stale_voter_ids=stale_ids)

        # Then: staleness doesn't apply to non-approval votes
        assert status.vote_invalidated is False, (
            f"Expected vote_invalidated False for vote=0 "
            f"(staleness only applies to approvals), got {status.vote_invalidated}"
        )
        assert status.vote_text == "No vote", (
            f"Expected vote_text 'No vote', got '{status.vote_text}'"
        )

    def test_rejected_vote_classified_correctly(self) -> None:
        """
        Given a reviewer with vote=-10
        When determine_vote_status is called
        Then vote_text="Rejected" and vote_invalidated=False
        """
        # Given: a rejecting reviewer
        reviewer = _reviewer(
            display_name="Diana Prince",
            unique_name="diana@contoso.com",
            reviewer_id="diana-id",
            vote=-10,
        )

        # When: classified
        status = determine_vote_status(reviewer)

        # Then: rejection is not subject to staleness
        assert status.vote_text == "Rejected", (
            f"Expected vote_text 'Rejected', got '{status.vote_text}'"
        )
        assert status.vote_invalidated is False, (
            f"Expected vote_invalidated False for rejection, got {status.vote_invalidated}"
        )

    def test_waiting_for_author_classified_correctly(self) -> None:
        """
        Given a reviewer with vote=-5
        When determine_vote_status is called
        Then vote_text="Waiting for author" and vote_invalidated=False
        """
        # Given: a waiting-for-author reviewer
        reviewer = _reviewer(
            display_name="Eve Wilson",
            unique_name="eve@contoso.com",
            reviewer_id="eve-id",
            vote=-5,
        )

        # When: classified
        status = determine_vote_status(reviewer)

        # Then: waiting-for-author is not subject to staleness
        assert status.vote_text == "Waiting for author", (
            f"Expected vote_text 'Waiting for author', got '{status.vote_text}'"
        )
        assert status.vote_invalidated is False, (
            f"Expected vote_invalidated False for waiting, got {status.vote_invalidated}"
        )

    def test_container_with_null_voted_for(self) -> None:
        """
        Given a reviewer with isContainer=True and votedFor=null
        When determine_vote_status is called
        Then is_container=True and voted_for_ids=[]
        """
        # Given: a team container with no votedFor data
        reviewer = _reviewer(
            display_name="Payments Team",
            unique_name="payments@contoso.com",
            reviewer_id="team-payments",
            vote=0,
            is_container=True,
            voted_for=None,
        )

        # When: classified
        status = determine_vote_status(reviewer)

        # Then: container flag is set, voted_for_ids defaults to empty
        assert status.is_container is True, (
            f"Expected is_container True, got {status.is_container}"
        )
        assert status.voted_for_ids == [], (
            f"Expected voted_for_ids [] for null votedFor, got {status.voted_for_ids}"
        )

    def test_individual_with_voted_for_ids(self) -> None:
        """
        Given a reviewer with votedFor=[{id: "team-1"}, {id: "team-2"}]
        When determine_vote_status is called
        Then voted_for_ids=["team-1", "team-2"]
        """
        # Given: an individual who voted on behalf of two teams
        reviewer = _reviewer(
            display_name="Frank Castle",
            unique_name="frank@contoso.com",
            reviewer_id="frank-id",
            vote=10,
            voted_for=[Mock(id="team-1"), Mock(id="team-2")],
        )

        # When: classified
        status = determine_vote_status(reviewer)

        # Then: voted_for_ids captures both team IDs
        assert status.voted_for_ids == ["team-1", "team-2"], (
            f"Expected voted_for_ids ['team-1', 'team-2'], got {status.voted_for_ids}"
        )

    def test_missing_display_name_defaults_to_unknown(self) -> None:
        """
        Given a reviewer with no displayName or uniqueName fields
        When determine_vote_status is called
        Then defaults to name="Unknown" and email=""
        """
        # Given: a minimal reviewer with no identity fields
        reviewer = _reviewer(
            reviewer_id="anon-id",
            vote=0,
            display_name=None,
            unique_name=None,
        )

        # When: classified
        status = determine_vote_status(reviewer)

        # Then: defaults are applied
        assert status.name == "Unknown", f"Expected name 'Unknown' as default, got '{status.name}'"
        assert status.email == "", f"Expected email '' as default, got '{status.email}'"


def _vote_status(
    *,
    reviewer_id: str = "default-id",
    name: str = "Default",
    is_container: bool = False,
    voted_for_ids: list[str] | None = None,
    vote: int = 10,
) -> VoteStatus:
    """Helper to build VoteStatus instances for deduplication tests."""
    return VoteStatus(
        name=name,
        email=f"{name.lower()}@contoso.com",
        vote=vote,
        vote_text="Approved",
        vote_invalidated=False,
        invalidated_by_commit=False,
        is_container=is_container,
        voted_for_ids=voted_for_ids if voted_for_ids is not None else [],
        reviewer_id=reviewer_id,
    )


class TestDeduplicateTeamContainers:
    """
    REQUIREMENT: deduplicate_team_containers() removes team containers that are
    already represented by an individual voter.

    WHO: Phase 6c get_pr_review_status() and send_pr_review_reminders().
    WHAT: (1) a container satisfied by an individual voter is removed
          (2) a container for a different team remains
          (3) a container without a matching individual remains
          (4) multiple individuals each satisfying different containers
              removes all matched containers
          (5) an empty list returns an empty list
          (6) all individuals and no containers returns the same list
    WHY: When an individual votes on a PR, ADO also marks their team container
         as having voted. Without dedup, the same approval shows up twice —
         inflating approval counts and confusing status reports. PDP had this fix
         in get_pr_review_status but not in send_pr_review_reminders.

    MOCK BOUNDARY:
        Mock:  nothing — pure function, list in → list out
        Real:  deduplicate_team_containers, VoteStatus
        Never: N/A
    """

    def test_container_satisfied_by_individual_is_removed(self) -> None:
        """
        Given [individual(voted_for=["team-1"]), container(id="team-1")]
        When deduplicate_team_containers is called
        Then the container is removed
        """
        # Given: an individual who voted for team-1, and the team-1 container
        individual = _vote_status(
            reviewer_id="alice-id",
            name="Alice",
            voted_for_ids=["team-1"],
        )
        container = _vote_status(
            reviewer_id="team-1",
            name="Payments Team",
            is_container=True,
        )

        # When: deduplication is applied
        result = deduplicate_team_containers([individual, container])

        # Then: container is removed, individual remains
        assert len(result) == 1, (
            f"Expected 1 entry after dedup (container removed), got {len(result)}"
        )
        assert result[0].reviewer_id == "alice-id", (
            f"Expected remaining entry to be 'alice-id', got '{result[0].reviewer_id}'"
        )

    def test_container_for_different_team_remains(self) -> None:
        """
        Given [individual(voted_for=["team-1"]), container(id="team-2")]
        When deduplicate_team_containers is called
        Then the container remains (different team)
        """
        # Given: individual voted for team-1, but container is team-2
        individual = _vote_status(
            reviewer_id="alice-id",
            name="Alice",
            voted_for_ids=["team-1"],
        )
        container = _vote_status(
            reviewer_id="team-2",
            name="Backend Team",
            is_container=True,
        )

        # When: deduplication is applied
        result = deduplicate_team_containers([individual, container])

        # Then: both remain — different teams
        assert len(result) == 2, (
            f"Expected 2 entries (different teams, no dedup), got {len(result)}"
        )

    def test_container_without_individual_remains(self) -> None:
        """
        Given [container(id="team-1")] only, no individual
        When deduplicate_team_containers is called
        Then the container remains
        """
        # Given: only a container, no individual to satisfy it
        container = _vote_status(
            reviewer_id="team-1",
            name="Payments Team",
            is_container=True,
        )

        # When: deduplication is applied
        result = deduplicate_team_containers([container])

        # Then: container remains — nothing to dedup against
        assert len(result) == 1, f"Expected 1 entry (container alone), got {len(result)}"
        assert result[0].is_container is True, (
            f"Expected remaining entry to be a container, "
            f"got is_container={result[0].is_container}"
        )

    def test_multiple_individuals_satisfy_multiple_containers(self) -> None:
        """
        Given [individual_A(voted_for=["team-1"]), individual_B(voted_for=["team-2"]),
               container("team-1"), container("team-2")]
        When deduplicate_team_containers is called
        Then both containers are removed
        """
        # Given: two individuals each satisfying one container
        alice = _vote_status(
            reviewer_id="alice-id",
            name="Alice",
            voted_for_ids=["team-1"],
        )
        bob = _vote_status(
            reviewer_id="bob-id",
            name="Bob",
            voted_for_ids=["team-2"],
        )
        team_1 = _vote_status(
            reviewer_id="team-1",
            name="Payments Team",
            is_container=True,
        )
        team_2 = _vote_status(
            reviewer_id="team-2",
            name="Backend Team",
            is_container=True,
        )

        # When: deduplication is applied
        result = deduplicate_team_containers([alice, bob, team_1, team_2])

        # Then: both containers removed, both individuals remain
        assert len(result) == 2, f"Expected 2 entries (both containers removed), got {len(result)}"
        result_ids = {vs.reviewer_id for vs in result}
        assert result_ids == {"alice-id", "bob-id"}, (
            f"Expected only individuals to remain, got IDs: {result_ids}"
        )

    def test_empty_list_returns_empty_list(self) -> None:
        """
        Given an empty list
        When deduplicate_team_containers is called
        Then returns an empty list
        """
        # Given: no vote statuses
        # When: deduplication is applied
        result = deduplicate_team_containers([])

        # Then: empty in, empty out
        assert result == [], f"Expected empty list, got {result}"

    def test_all_individuals_no_containers_returns_same_list(self) -> None:
        """
        Given all individuals and no containers
        When deduplicate_team_containers is called
        Then returns the same list unchanged
        """
        # Given: only individuals, no containers to dedup
        alice = _vote_status(reviewer_id="alice-id", name="Alice")
        bob = _vote_status(reviewer_id="bob-id", name="Bob")

        # When: deduplication is applied
        result = deduplicate_team_containers([alice, bob])

        # Then: all individuals remain
        assert len(result) == 2, f"Expected 2 entries (no containers to remove), got {len(result)}"
        result_ids = {vs.reviewer_id for vs in result}
        assert result_ids == {"alice-id", "bob-id"}, (
            f"Expected both individuals, got IDs: {result_ids}"
        )

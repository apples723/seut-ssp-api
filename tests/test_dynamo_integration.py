"""Integration tests for DynamoRepository against DynamoDB Local.

These tests require DynamoDB Local to be running at http://localhost:8042.
Start it with: docker compose -f docker-compose.local.yml -f docker-compose.dynamodb.yml up dynamodb-local

Tests are marked with @pytest.mark.integration and will be skipped gracefully
when DynamoDB Local is not available.

Validates: Requirements 4.1, 4.2, 6.1, 7.1, 7.2, 8.2, 12.1, 15.3, 17.1
"""

import os
import sys
import time
import socket

import pytest

# Set environment before importing DynamoRepository
os.environ["DYNAMODB_ENDPOINT"] = "http://localhost:8042"
os.environ["STORAGE_BACKEND"] = "dynamodb"
os.environ.setdefault("DB_PATH", "/tmp/test_sessions.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _dynamodb_local_available() -> bool:
    """Check if DynamoDB Local is reachable at localhost:8042."""
    try:
        sock = socket.create_connection(("localhost", 8042), timeout=2)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _dynamodb_local_available(),
        reason="DynamoDB Local not available at localhost:8042",
    ),
]


@pytest.fixture()
def dynamo_repo():
    """Create a fresh DynamoRepository instance with clean tables.

    Deletes all items from both tables before each test to ensure isolation.
    The DynamoRepository constructor auto-creates tables if they don't exist.
    """
    from src.dynamo_repository import DynamoRepository
    from src.dynamo_helpers import session_pk

    repo = DynamoRepository()

    # Clean all items from ssp-sessions table
    _clear_table(repo._session_table)
    # Clean all items from ssp-scenarios table
    _clear_table(repo._scenario_table)

    return repo


def _clear_table(table):
    """Delete all items from a DynamoDB table."""
    scan = table.scan(ProjectionExpression="PK, SK")
    items = scan.get("Items", [])
    while scan.get("LastEvaluatedKey"):
        scan = table.scan(
            ProjectionExpression="PK, SK",
            ExclusiveStartKey=scan["LastEvaluatedKey"],
        )
        items.extend(scan.get("Items", []))

    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})


@pytest.fixture()
def seed_scenario(dynamo_repo):
    """Seed a test scenario into the scenarios table for use in create_session."""
    scenario_data = {
        "id": "test-integration-scenario",
        "title": "Integration Test Scenario",
        "description": "A scenario for integration testing",
        "waitingRoomMessage": "Please wait for the facilitator",
        "presentModeInstructions": "Welcome to the simulation",
        "settings": {"timerEnabled": True},
        "roles": [
            {
                "id": "cto",
                "name": "CTO",
                "roleType": "player",
                "description": "Chief Technology Officer",
                "privateInfo": "Your secret: budget is limited",
            },
            {
                "id": "ceo",
                "name": "CEO",
                "roleType": "player",
                "description": "Chief Executive Officer",
                "privateInfo": "Your secret: board is unhappy",
            },
        ],
        "sprints": [
            {
                "id": "sprint-1",
                "name": "Sprint 1",
                "description": "First sprint",
                "timerEnabled": True,
                "timerSeconds": 300,
                "responseBoxes": ["Ethics", "Business"],
                "sprintQuestion": {
                    "prompt": "Should we prioritize growth?",
                    "choices": ["Yes", "No"],
                },
                "headToHead": {
                    "prompt": "Who argued better?",
                    "roleA": "cto",
                    "roleB": "ceo",
                },
            },
            {
                "id": "sprint-2",
                "name": "Sprint 2",
                "description": "Second sprint",
                "timerEnabled": True,
                "timerSeconds": 420,
                "responseBoxes": None,
                "sprintQuestion": {
                    "prompt": "Should we expand?",
                    "choices": ["Yes", "No"],
                },
                "headToHead": {
                    "prompt": "Who presented better?",
                    "roleA": "cto",
                    "roleB": "ceo",
                },
            },
        ],
        "voterTypes": [
            {"id": "board", "name": "Board Member", "description": "Board representative"},
            {"id": "ethics", "name": "Ethics Committee", "description": "Ethics member"},
            {"id": "finance", "name": "Finance Team", "description": "Finance representative"},
        ],
    }

    dynamo_repo._scenario_repo.create_scenario(scenario_data, created_by="test")
    return scenario_data


# ==============================================================================
# Test 1: Full CRUD Cycle
# Validates: Requirements 4.1, 4.2, 8.2, 12.1, 15.3
# ==============================================================================


class TestFullCRUDCycle:
    """Full lifecycle: create → participants → responses → vote → sprints → score → end."""

    def test_full_session_lifecycle(self, dynamo_repo, seed_scenario):
        """Exercise the complete session lifecycle end-to-end."""
        # 1. Create session
        session = dynamo_repo.create_session(
            facilitator_name="Dr. Smith",
            scenario_id="test-integration-scenario",
            facilitator_pin="1234",
        )
        assert session is not None
        assert session.status == "active"
        assert session.scenario_name == "Integration Test Scenario"
        assert session.version == 1
        session_code = session.session_id

        # 2. Start session
        started = dynamo_repo.start_session(session_code)
        assert started is True

        # 3. Add participants (voters with voter types for scoring)
        voter1 = dynamo_repo.add_participant(
            session_code, "Alice", "voter",
            voter_type_id="board", voter_type_name="Board Member",
        )
        voter2 = dynamo_repo.add_participant(
            session_code, "Bob", "voter",
            voter_type_id="ethics", voter_type_name="Ethics Committee",
        )
        voter3 = dynamo_repo.add_participant(
            session_code, "Carol", "voter",
            voter_type_id="finance", voter_type_name="Finance Team",
        )
        player1 = dynamo_repo.add_participant(
            session_code, "Dave", "player",
            assigned_role_name="CTO", role_type="player",
            role_description="Chief Technology Officer",
        )
        player2 = dynamo_repo.add_participant(
            session_code, "Eve", "player",
            assigned_role_name="CEO", role_type="player",
            role_description="Chief Executive Officer",
        )

        # Verify participants are retrievable
        participants = dynamo_repo.get_session_participants(session_code)
        # 5 added + 1 facilitator = 6
        assert len(participants) == 6

        # 4. Submit responses
        resp1 = dynamo_repo.add_response_to_session(
            session_code, "Alice", "We need ethical guidelines",
            response_box="Ethics", participant_role_name="Board Member",
        )
        resp2 = dynamo_repo.add_response_to_session(
            session_code, "Bob", "Revenue must grow",
            response_box="Business", participant_role_name="Ethics Committee",
        )
        assert resp1 is not None
        assert resp2 is not None
        assert resp1.sprint_id == "sprint-1"

        # 5. Vote on response (response upvote)
        vote_result = dynamo_repo.add_vote(
            session_code, resp1.id, voter2.id,
            "Bob", "voter",
            voter_type_id="ethics", voter_type_name="Ethics Committee",
        )
        assert vote_result is True

        # 6. Submit sprint question votes (engineer votes)
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", player1.id, "Dave", 0, "Yes",
        )
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", player2.id, "Eve", 1, "No",
        )

        # Submit stakeholder votes (voter sprint question votes)
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", voter1.id, "Alice", 0, "Yes",
            event_name="stakeholder_vote_submitted",
        )
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", voter2.id, "Bob", 0, "Yes",
            event_name="stakeholder_vote_submitted",
        )
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", voter3.id, "Carol", 1, "No",
            event_name="stakeholder_vote_submitted",
        )

        # Submit head-to-head votes
        dynamo_repo.submit_head_to_head_vote(
            session_code, "sprint-1", voter1.id, "Alice",
            "board", "Board Member", "cto",
        )
        dynamo_repo.submit_head_to_head_vote(
            session_code, "sprint-1", voter2.id, "Bob",
            "ethics", "Ethics Committee", "cto",
        )
        dynamo_repo.submit_head_to_head_vote(
            session_code, "sprint-1", voter3.id, "Carol",
            "finance", "Finance Team", "ceo",
        )

        # 7. Calculate scores
        dynamo_repo.calculate_and_store_scores(session_code, "sprint-1")

        # Verify scores exist in session
        session_after = dynamo_repo.get_session_by_code(session_code)
        assert session_after is not None
        assert len(session_after.scores) > 0

        # 8. Advance sprint
        next_session = dynamo_repo.next_sprint_for_session(session_code)
        assert next_session is not None
        assert next_session.current_sprint_index == 1
        assert next_session.current_sprint.id == "sprint-2"

        # 9. End session
        ended = dynamo_repo.end_session(session_code)
        assert ended is True

        # Verify session is ended
        final_session = dynamo_repo.get_session_by_code(session_code)
        assert final_session is not None
        assert final_session.status == "ended"


# ==============================================================================
# Test 2: GSI Queries - list_sessions
# Validates: Requirements 7.1, 7.2, 8.2
# ==============================================================================


class TestGSIQueries:
    """Verify list_sessions returns correct summaries for active/ended sessions."""

    def test_list_sessions_active_and_ended(self, dynamo_repo, seed_scenario):
        """list_sessions returns both active and ended session summaries."""
        # Create two sessions
        session1 = dynamo_repo.create_session(
            facilitator_name="Facilitator A",
            scenario_id="test-integration-scenario",
        )
        session2 = dynamo_repo.create_session(
            facilitator_name="Facilitator B",
            scenario_id="test-integration-scenario",
        )

        # End one session
        dynamo_repo.end_session(session1.session_id)

        # Query list_sessions
        sessions = dynamo_repo.list_sessions()
        assert len(sessions) == 2

        # Verify both are present with correct statuses
        session_map = {s["sessionId"]: s for s in sessions}
        assert session_map[session1.session_id]["status"] == "ended"
        assert session_map[session2.session_id]["status"] == "active"

        # Verify expected fields in summaries
        for s in sessions:
            assert "sessionId" in s
            assert "scenarioName" in s
            assert "status" in s
            assert "participantCount" in s
            assert "createdAt" in s
            assert "currentSprintIndex" in s
            assert "totalSprints" in s

    def test_list_sessions_empty(self, dynamo_repo):
        """list_sessions returns empty list when no sessions exist."""
        sessions = dynamo_repo.list_sessions()
        assert sessions == []

    def test_list_sessions_ordered_by_created_at_descending(self, dynamo_repo, seed_scenario):
        """list_sessions results are sorted by createdAt descending."""
        # Create sessions with small delay between them
        session1 = dynamo_repo.create_session(
            facilitator_name="First",
            scenario_id="test-integration-scenario",
        )
        time.sleep(0.1)
        session2 = dynamo_repo.create_session(
            facilitator_name="Second",
            scenario_id="test-integration-scenario",
        )

        sessions = dynamo_repo.list_sessions()
        assert len(sessions) == 2
        # Most recent first
        assert sessions[0]["sessionId"] == session2.session_id
        assert sessions[1]["sessionId"] == session1.session_id


# ==============================================================================
# Test 3: TTL Attribute After end_session
# Validates: Requirements 7.1, 7.2
# ==============================================================================


class TestTTLAttribute:
    """Verify that expiresAt is set on all items after end_session."""

    def test_expires_at_set_on_all_items(self, dynamo_repo, seed_scenario):
        """After end_session, all items in the partition have expiresAt."""
        from src.dynamo_helpers import session_pk, query_all_pages
        from boto3.dynamodb.conditions import Key

        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id

        # Add some data
        dynamo_repo.add_participant(session_code, "Alice", "voter")
        dynamo_repo.add_response_to_session(
            session_code, "Alice", "Test response",
        )

        # End the session
        dynamo_repo.end_session(session_code)

        # Query all items directly
        pk = session_pk(session_code)
        all_items = query_all_pages(
            dynamo_repo._session_table, Key("PK").eq(pk)
        )

        assert len(all_items) > 0
        now_epoch = int(time.time())
        for item in all_items:
            assert "expiresAt" in item, f"Missing expiresAt on item with SK={item.get('SK')}"
            # expiresAt should be roughly now + 604800 (7 days), give or take a few seconds
            expires_at = int(item["expiresAt"])
            expected = now_epoch + 604800
            assert abs(expires_at - expected) < 10, (
                f"expiresAt {expires_at} not within 10s of expected {expected}"
            )


# ==============================================================================
# Test 4: Response Sprint Filtering
# Validates: Requirements 4.4, 17.1
# ==============================================================================


class TestSprintFiltering:
    """Verify response filtering across sprint navigation."""

    def test_responses_filtered_to_current_sprint(self, dynamo_repo, seed_scenario):
        """After advancing sprint, get_session_by_code returns only current sprint responses."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id

        # Add response to sprint 1
        resp_s1 = dynamo_repo.add_response_to_session(
            session_code, "Alice", "Sprint 1 response",
        )
        assert resp_s1 is not None
        assert resp_s1.sprint_id == "sprint-1"

        # Verify sprint 1 response is visible
        session_s1 = dynamo_repo.get_session_by_code(session_code)
        assert len(session_s1.responses) == 1
        assert session_s1.responses[0].text == "Sprint 1 response"

        # Advance to sprint 2
        dynamo_repo.next_sprint_for_session(session_code)

        # Add response to sprint 2
        resp_s2 = dynamo_repo.add_response_to_session(
            session_code, "Bob", "Sprint 2 response",
        )
        assert resp_s2 is not None
        assert resp_s2.sprint_id == "sprint-2"

        # Verify only sprint 2 response is visible now
        session_s2 = dynamo_repo.get_session_by_code(session_code)
        assert session_s2.current_sprint_index == 1
        assert len(session_s2.responses) == 1
        assert session_s2.responses[0].text == "Sprint 2 response"

        # Go back to sprint 1
        dynamo_repo.prev_sprint_for_session(session_code)

        # Verify sprint 1 response is visible again
        session_back = dynamo_repo.get_session_by_code(session_code)
        assert session_back.current_sprint_index == 0
        assert len(session_back.responses) == 1
        assert session_back.responses[0].text == "Sprint 1 response"

    def test_empty_responses_for_new_sprint(self, dynamo_repo, seed_scenario):
        """A sprint with no responses returns empty responses list."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id

        # Advance to sprint 2 without adding any responses
        dynamo_repo.next_sprint_for_session(session_code)

        session_s2 = dynamo_repo.get_session_by_code(session_code)
        assert session_s2.current_sprint_index == 1
        assert session_s2.responses == []


# ==============================================================================
# Test 5: Vote Uniqueness Enforcement
# Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5
# ==============================================================================


class TestVoteUniqueness:
    """Verify vote uniqueness enforcement for all vote types."""

    def test_response_vote_duplicate_returns_false(self, dynamo_repo, seed_scenario):
        """Second response vote on same response by same participant returns False."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id
        voter = dynamo_repo.add_participant(session_code, "Alice", "voter")
        resp = dynamo_repo.add_response_to_session(
            session_code, "Someone", "A response",
        )

        # First vote succeeds
        result1 = dynamo_repo.add_vote(
            session_code, resp.id, voter.id, "Alice", "voter",
        )
        assert result1 is True

        # Second vote on same response returns False (no exception)
        result2 = dynamo_repo.add_vote(
            session_code, resp.id, voter.id, "Alice", "voter",
        )
        assert result2 is False

    def test_sprint_question_vote_upsert(self, dynamo_repo, seed_scenario):
        """Sprint question vote uses upsert semantics — second call overwrites."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id
        player = dynamo_repo.add_participant(
            session_code, "Dave", "player",
            assigned_role_name="CTO", role_type="player",
        )

        # First vote: choice 0
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", player.id, "Dave", 0, "Yes",
        )

        # Verify via get_session_by_code
        s1 = dynamo_repo.get_session_by_code(session_code, participant_id=player.id)
        assert s1.engineer_vote_choice == 0

        # Second vote: choice 1 (overwrites)
        dynamo_repo.submit_engineer_vote(
            session_code, "sprint-1", player.id, "Dave", 1, "No",
        )

        # Verify overwritten
        s2 = dynamo_repo.get_session_by_code(session_code, participant_id=player.id)
        assert s2.engineer_vote_choice == 1

    def test_head_to_head_vote_upsert(self, dynamo_repo, seed_scenario):
        """Head-to-head vote uses upsert semantics — second call overwrites."""
        from src.dynamo_helpers import session_pk, h2h_vote_sk
        from boto3.dynamodb.conditions import Key

        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id
        voter = dynamo_repo.add_participant(
            session_code, "Alice", "voter",
            voter_type_id="board", voter_type_name="Board Member",
        )

        # First vote: choose cto
        dynamo_repo.submit_head_to_head_vote(
            session_code, "sprint-1", voter.id, "Alice",
            "board", "Board Member", "cto",
        )

        # Second vote: choose ceo (overwrites)
        dynamo_repo.submit_head_to_head_vote(
            session_code, "sprint-1", voter.id, "Alice",
            "board", "Board Member", "ceo",
        )

        # Verify only one H2H vote item exists, with ceo
        pk = session_pk(session_code)
        sk = h2h_vote_sk("sprint-1", voter.id)
        response = dynamo_repo._session_table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        assert item is not None
        assert item["chosenRoleId"] == "ceo"


# ==============================================================================
# Test 6: to_dict() Output Structure
# Validates: Requirements 4.2, 15.3
# ==============================================================================


class TestToDictStructure:
    """Verify to_dict() output structure matches expected format."""

    def test_to_dict_has_expected_keys(self, dynamo_repo, seed_scenario):
        """Session.to_dict() contains all expected top-level keys."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
            facilitator_pin="9999",
        )
        session_code = session.session_id

        # Add some data for a richer to_dict output
        dynamo_repo.add_participant(session_code, "Alice", "voter")

        session_obj = dynamo_repo.get_session_by_code(session_code)
        d = session_obj.to_dict()

        # Top-level keys
        expected_keys = {
            "sessionId", "scenarioName", "scenarioDescription",
            "waitingRoomMessage", "presentModeInstructions",
            "currentSprintIndex", "totalSprints",
            "timerVisible", "timerSeconds", "timerRunning",
            "status", "started", "hasFacilitatorPin", "createdAt",
            "version", "votingEnabled", "votingShowVoters",
            "rolePinsEnabled", "sprintPhase", "scoreboardVisible",
            "engineerVoteChoice", "scores", "voterTypes",
            "participant", "currentSprint", "responses",
            "chatMessages", "participants",
        }
        assert expected_keys.issubset(set(d.keys())), (
            f"Missing keys: {expected_keys - set(d.keys())}"
        )

        # Verify participant sub-structure
        p = d["participant"]
        assert "id" in p
        assert "name" in p
        assert "role" in p
        assert "assignedRoleName" in p
        assert "roleType" in p
        assert "voterTypeId" in p
        assert "voterTypeName" in p

        # Verify currentSprint sub-structure
        sprint = d["currentSprint"]
        assert "id" in sprint
        assert "name" in sprint
        assert "description" in sprint
        assert "timerEnabled" in sprint
        assert "timerSeconds" in sprint
        assert "responseBoxes" in sprint
        assert "sprintQuestion" in sprint
        assert "headToHead" in sprint

        # Verify sprintQuestion structure when present
        if sprint["sprintQuestion"] is not None:
            assert "prompt" in sprint["sprintQuestion"]
            assert "choices" in sprint["sprintQuestion"]

        # Verify headToHead structure when present
        if sprint["headToHead"] is not None:
            assert "prompt" in sprint["headToHead"]
            assert "roleA" in sprint["headToHead"]
            assert "roleB" in sprint["headToHead"]

        # Verify hasFacilitatorPin is True when pin is set
        assert d["hasFacilitatorPin"] is True

        # Verify voterTypes structure
        assert isinstance(d["voterTypes"], list)
        if d["voterTypes"]:
            vt = d["voterTypes"][0]
            assert "id" in vt
            assert "name" in vt

    def test_to_dict_response_structure(self, dynamo_repo, seed_scenario):
        """Response items in to_dict have correct structure."""
        session = dynamo_repo.create_session(
            facilitator_name="Facilitator",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id

        dynamo_repo.add_response_to_session(
            session_code, "Alice", "Important point",
            response_box="Ethics", participant_role_name="Board Member",
        )

        session_obj = dynamo_repo.get_session_by_code(session_code)
        d = session_obj.to_dict()

        assert len(d["responses"]) == 1
        r = d["responses"][0]
        assert "id" in r
        assert "participantName" in r
        assert "participantRoleName" in r
        assert "text" in r
        assert "sprintId" in r
        assert "createdAt" in r
        assert "responseBox" in r
        assert "voteCount" in r
        assert "currentUserVoted" in r

        assert r["participantName"] == "Alice"
        assert r["text"] == "Important point"
        assert r["responseBox"] == "Ethics"
        assert r["voteCount"] == 0
        assert r["currentUserVoted"] is False

    def test_to_dict_participants_structure(self, dynamo_repo, seed_scenario):
        """Participants list in to_dict has correct structure."""
        session = dynamo_repo.create_session(
            facilitator_name="Dr. Smith",
            scenario_id="test-integration-scenario",
        )
        session_code = session.session_id

        dynamo_repo.add_participant(
            session_code, "Alice", "voter",
            voter_type_id="board", voter_type_name="Board Member",
        )

        session_obj = dynamo_repo.get_session_by_code(session_code)
        d = session_obj.to_dict()

        # Should have facilitator + Alice
        assert len(d["participants"]) == 2
        for p in d["participants"]:
            assert "id" in p
            assert "name" in p
            assert "role" in p
            assert "assignedRoleName" in p
            assert "voterTypeId" in p
            assert "voterTypeName" in p

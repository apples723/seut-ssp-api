"""Unit tests for DynamoRepository.create_session."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest

from apps.api.src.models import Session, Participant, Sprint, VoterType


@pytest.fixture
def dynamo_repo():
    """Create a DynamoRepository instance with mocked boto3 resources."""
    with patch("apps.api.src.dynamo_repository.boto3") as mock_boto3:
        mock_resource = MagicMock()
        mock_boto3.resource.return_value = mock_resource
        # Simulate tables already exist
        mock_resource.meta.client.list_tables.return_value = {
            "TableNames": ["ssp-sessions", "ssp-scenarios"]
        }

        # Create distinct mock tables so they don't share state
        mock_session_table = MagicMock()
        mock_session_table.table_name = "ssp-sessions"
        mock_scenario_table = MagicMock()
        mock_scenario_table.table_name = "ssp-scenarios"

        def table_factory(name):
            if name == "ssp-sessions":
                return mock_session_table
            elif name == "ssp-scenarios":
                return mock_scenario_table
            return MagicMock()

        mock_resource.Table.side_effect = table_factory

        with patch.dict("os.environ", {"DYNAMODB_ENDPOINT": "http://localhost:8042"}):
            from apps.api.src.dynamo_repository import DynamoRepository

            repo = DynamoRepository()

    return repo


@pytest.fixture
def sample_scenario_item():
    """A sample scenario LATEST item as it would be stored in DynamoDB."""
    return {
        "PK": "SCEN#mental-health-app",
        "SK": "LATEST",
        "scenarioId": "mental-health-app",
        "title": "Mental Health App Role-Play",
        "description": "A scenario about mental health app decisions.",
        "waitingRoomMessage": "Welcome! Please wait for the facilitator.",
        "presentModeInstructions": "Watch the screen.",
        "settingsJson": json.dumps({"timerEnabled": True}),
        "rolesJson": json.dumps([
            {"id": "cto", "name": "CTO", "roleType": "player", "description": "Chief Technology Officer"},
            {"id": "cfo", "name": "CFO", "roleType": "player", "description": "Chief Financial Officer"},
            {"id": "board-1", "name": "Board Member 1", "roleType": "voter", "description": "Board member"},
        ]),
        "sprintsJson": json.dumps([
            {
                "id": "sprint-1",
                "name": "Sprint 1",
                "description": "First sprint",
                "timerEnabled": True,
                "timerSeconds": 480,
                "responseBoxes": ["Ethics", "Engineering"],
                "sprintQuestion": {"prompt": "Should we launch?", "choices": ["Yes", "No"]},
                "headToHead": {"prompt": "Who argued better?", "roleA": "cto", "roleB": "cfo"},
            },
            {
                "id": "sprint-2",
                "name": "Sprint 2",
                "description": "Second sprint",
                "timerEnabled": False,
                "timerSeconds": 300,
            },
        ]),
        "voterTypesJson": json.dumps([
            {"id": "board", "name": "Board Member", "description": "Board rep"},
        ]),
        "version": 3,
        "isLatest": True,
        "isDeleted": False,
        "enabled": True,
        "createdAt": "2024-01-15T10:00:00Z",
    }


class TestCreateSession:
    """Tests for DynamoRepository.create_session."""

    def test_returns_session_object(self, dynamo_repo, sample_scenario_item):
        """create_session should return a Session dataclass instance."""
        # Mock scenario lookup
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        # Mock session code uniqueness check
        dynamo_repo._session_table.get_item.return_value = {}  # No existing session
        # Mock batch_write_items
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
            facilitator_pin="1234",
            ip_address="192.168.1.1",
            role_pins_enabled=False,
        )

        assert isinstance(result, Session)
        assert result.scenario_name == "Mental Health App Role-Play"
        assert result.scenario_description == "A scenario about mental health app decisions."
        assert result.status == "active"
        assert result.started is False
        assert result.version == 1
        assert result.timer_visible is True
        assert result.timer_seconds == 480
        assert result.current_sprint_index == 0
        assert result.total_sprints == 2
        assert result.facilitator_pin == "1234"
        assert result.voting_enabled is True
        assert result.voting_show_voters is False
        assert result.sprint_question_phase == "inactive"
        assert result.scoreboard_visible is False
        assert result.role_pins_enabled is False

    def test_session_code_format(self, dynamo_repo, sample_scenario_item):
        """Session code should be in XXXX-NNNN format."""
        import re

        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        assert re.match(r'^[A-Z]{4}-\d{4}$', result.session_id)

    def test_facilitator_participant(self, dynamo_repo, sample_scenario_item):
        """The returned session should have the facilitator as a participant."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
            ip_address="10.0.0.1",
        )

        assert result.participant.name == "Alice"
        assert result.participant.role == "facilitator"
        assert result.participant.assigned_role_name == "Facilitator"
        assert result.participant.role_type == "Facilitator"
        assert result.participant.ip_address == "10.0.0.1"
        assert len(result.participants) == 1
        assert result.participants[0].name == "Alice"

    def test_sprints_parsed_correctly(self, dynamo_repo, sample_scenario_item):
        """Sprints from the scenario should be parsed and returned."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        assert len(result.sprints) == 2
        assert result.sprints[0].id == "sprint-1"
        assert result.sprints[0].name == "Sprint 1"
        assert result.sprints[0].response_boxes == ["Ethics", "Engineering"]
        assert result.sprints[0].sprint_question is not None
        assert result.sprints[0].sprint_question.prompt == "Should we launch?"
        assert result.sprints[0].head_to_head is not None
        assert result.sprints[0].head_to_head.role_a == "cto"

        assert result.current_sprint.id == "sprint-1"

    def test_voter_types_returned(self, dynamo_repo, sample_scenario_item):
        """Voter types from the scenario should be returned as VoterType objects."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        assert len(result.voter_types) == 1
        assert result.voter_types[0].id == "board"
        assert result.voter_types[0].name == "Board Member"

    def test_meta_item_written_correctly(self, dynamo_repo, sample_scenario_item):
        """The META item should have correct fields including GSI attributes."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
            facilitator_pin="9999",
        )

        # Check batch_write_item was called
        batch_call = dynamo_repo._session_table.meta.client.batch_write_item.call_args
        request_items = batch_call[1]["RequestItems"]
        table_name = dynamo_repo._session_table.table_name
        items = [req["PutRequest"]["Item"] for req in request_items[table_name]]

        # Find META item
        meta_items = [i for i in items if i["SK"] == "META"]
        assert len(meta_items) == 1
        meta = meta_items[0]

        assert meta["PK"] == f"SESS#{result.session_id}"
        assert meta["sessionId"] == result.session_id
        assert meta["scenarioId"] == "mental-health-app"
        assert meta["scenarioVersion"] == 3
        assert meta["scenarioName"] == "Mental Health App Role-Play"
        assert meta["version"] == 1
        assert meta["status"] == "active"
        assert meta["started"] is False
        assert meta["gsiStatusPK"] == "STATUS#active"
        assert meta["gsiStatusSK"] == f"SESS#{result.session_id}"
        assert meta["facilitatorPin"] == "9999"
        assert meta["participantCount"] == 0

    def test_sprint_items_written(self, dynamo_repo, sample_scenario_item):
        """SPRINT items should be written for each sprint in the scenario."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        batch_call = dynamo_repo._session_table.meta.client.batch_write_item.call_args
        request_items = batch_call[1]["RequestItems"]
        table_name = dynamo_repo._session_table.table_name
        items = [req["PutRequest"]["Item"] for req in request_items[table_name]]

        sprint_items = [i for i in items if i["SK"].startswith("SPRINT#")]
        assert len(sprint_items) == 2
        sprint_ids = {i["sprintId"] for i in sprint_items}
        assert sprint_ids == {"sprint-1", "sprint-2"}

    def test_facilitator_participant_item_written(self, dynamo_repo, sample_scenario_item):
        """A PARTICIPANT item should be written for the facilitator."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Bob",
            scenario_id="mental-health-app",
            ip_address="10.0.0.5",
        )

        batch_call = dynamo_repo._session_table.meta.client.batch_write_item.call_args
        request_items = batch_call[1]["RequestItems"]
        table_name = dynamo_repo._session_table.table_name
        items = [req["PutRequest"]["Item"] for req in request_items[table_name]]

        participant_items = [i for i in items if i["SK"].startswith("PARTICIPANT#")]
        assert len(participant_items) == 1
        p = participant_items[0]
        assert p["name"] == "Bob"
        assert p["role"] == "facilitator"
        assert p["assignedRoleName"] == "Facilitator"
        assert p["ipAddress"] == "10.0.0.5"

    def test_role_pins_generated_when_enabled(self, dynamo_repo, sample_scenario_item):
        """ROLE_PIN items should be written when role_pins_enabled=True."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
            role_pins_enabled=True,
        )

        batch_call = dynamo_repo._session_table.meta.client.batch_write_item.call_args
        request_items = batch_call[1]["RequestItems"]
        table_name = dynamo_repo._session_table.table_name
        items = [req["PutRequest"]["Item"] for req in request_items[table_name]]

        role_pin_items = [i for i in items if i["SK"].startswith("ROLE_PIN#")]
        # Only player roles get PINs: cto and cfo
        assert len(role_pin_items) == 2
        role_ids = {i["roleId"] for i in role_pin_items}
        assert role_ids == {"cto", "cfo"}

        # All PINs should be unique 4-digit strings
        pins = [i["pin"] for i in role_pin_items]
        assert len(set(pins)) == 2  # unique
        for pin in pins:
            assert len(pin) == 4
            assert pin.isdigit()

    def test_role_pins_not_generated_when_disabled(self, dynamo_repo, sample_scenario_item):
        """No ROLE_PIN items when role_pins_enabled=False."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
            role_pins_enabled=False,
        )

        batch_call = dynamo_repo._session_table.meta.client.batch_write_item.call_args
        request_items = batch_call[1]["RequestItems"]
        table_name = dynamo_repo._session_table.table_name
        items = [req["PutRequest"]["Item"] for req in request_items[table_name]]

        role_pin_items = [i for i in items if i["SK"].startswith("ROLE_PIN#")]
        assert len(role_pin_items) == 0

    def test_scenario_not_found_uses_defaults(self, dynamo_repo):
        """When scenario is not found, use default sprint and metadata."""
        dynamo_repo._scenario_table.get_item.return_value = {}  # No item
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="nonexistent-scenario",
        )

        assert result.scenario_name == "Unknown Scenario"
        assert result.total_sprints == 1
        assert result.current_sprint.id == "s1"
        assert result.voting_enabled is False

    def test_waiting_room_and_present_mode_fields(self, dynamo_repo, sample_scenario_item):
        """Waiting room message and present mode instructions should be set."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        dynamo_repo._session_table.get_item.return_value = {}
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        assert result.waiting_room_message == "Welcome! Please wait for the facilitator."
        assert result.present_mode_instructions == "Watch the screen."

    def test_session_code_uniqueness_retry(self, dynamo_repo, sample_scenario_item):
        """If a session code already exists, retry with a new code."""
        dynamo_repo._scenario_table.get_item.return_value = {"Item": sample_scenario_item}
        # First call: session exists; second call: no session
        dynamo_repo._session_table.get_item.side_effect = [
            {"Item": {"PK": "SESS#AAAA-1111"}},  # exists
            {},  # doesn't exist
        ]
        dynamo_repo._session_table.meta.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        result = dynamo_repo.create_session(
            facilitator_name="Alice",
            scenario_id="mental-health-app",
        )

        # Should have called get_item twice
        assert dynamo_repo._session_table.get_item.call_count == 2
        assert isinstance(result, Session)

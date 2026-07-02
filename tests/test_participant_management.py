"""Unit tests for DynamoRepository participant management methods."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest

from apps.api.src.models import Session, Participant, Sprint, VoterType, RolePin


@pytest.fixture
def dynamo_repo():
    """Create a DynamoRepository instance with mocked boto3 resources."""
    with patch("apps.api.src.dynamo_repository.boto3") as mock_boto3:
        mock_resource = MagicMock()
        mock_boto3.resource.return_value = mock_resource
        mock_resource.meta.client.list_tables.return_value = {
            "TableNames": ["ssp-sessions", "ssp-scenarios"]
        }

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


class TestAddParticipant:
    """Tests for DynamoRepository.add_participant."""

    def test_returns_participant_with_generated_id(self, dynamo_repo):
        """add_participant should return a Participant with a UUID id."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo.add_participant("ABCD-1234", "Jordan", "player")

        assert isinstance(result, Participant)
        assert result.name == "Jordan"
        assert result.role == "player"
        assert len(result.id) == 36  # UUID length
        assert result.joined_at is not None

    def test_player_defaults(self, dynamo_repo):
        """Player role should get default role_type='Player' and no role name."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo.add_participant("ABCD-1234", "Alex", "player")

        assert result.assigned_role_name is None
        assert result.role_type == "Player"
        assert result.role_description is None

    def test_facilitator_defaults(self, dynamo_repo):
        """Facilitator should get appropriate defaults."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo.add_participant("ABCD-1234", "Fac", "facilitator")

        assert result.assigned_role_name == "Facilitator"
        assert result.role_type == "Facilitator"
        assert result.role_description == "Controls sprint progression and settings."

    def test_voter_type_stored(self, dynamo_repo):
        """Voter type fields should be passed through."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo.add_participant(
            "ABCD-1234", "Sam", "voter",
            voter_type_id="board", voter_type_name="Board Member",
        )

        assert result.voter_type_id == "board"
        assert result.voter_type_name == "Board Member"

    def test_put_item_called(self, dynamo_repo):
        """PutItem should be called for the participant."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        dynamo_repo.add_participant("ABCD-1234", "Jordan", "player")

        dynamo_repo._session_table.put_item.assert_called_once()
        item = dynamo_repo._session_table.put_item.call_args[1]["Item"]
        assert item["PK"] == "SESS#ABCD-1234"
        assert item["SK"].startswith("PARTICIPANT#")
        assert item["name"] == "Jordan"
        assert item["role"] == "player"

    def test_participant_count_incremented(self, dynamo_repo):
        """META participantCount should be incremented."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        dynamo_repo.add_participant("ABCD-1234", "Jordan", "player")

        # update_item is called twice: once for participantCount, once for version bump
        assert dynamo_repo._session_table.update_item.call_count == 2

    def test_custom_role_assignment(self, dynamo_repo):
        """When role assignment fields are provided, they should be used."""
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo.add_participant(
            "ABCD-1234", "Jordan", "player",
            assigned_role_name="CTO",
            role_type="player",
            role_description="Chief Technology Officer",
            private_info="Secret briefing",
        )

        assert result.assigned_role_name == "CTO"
        assert result.role_type == "player"
        assert result.role_description == "Chief Technology Officer"
        assert result.private_info == "Secret briefing"


class TestGetSessionParticipants:
    """Tests for DynamoRepository.get_session_participants."""

    def test_returns_list_of_participants(self, dynamo_repo):
        """Should return Participant objects from DynamoDB items."""
        dynamo_repo._session_table.query.return_value = {
            "Items": [
                {
                    "PK": "SESS#ABCD-1234",
                    "SK": "PARTICIPANT#p-123",
                    "participantId": "p-123",
                    "name": "Jordan",
                    "role": "player",
                    "assignedRoleName": "CTO",
                    "roleType": "player",
                    "roleDescription": "Chief Technology Officer",
                    "privateInfo": None,
                    "ipAddress": "10.0.0.1",
                    "joinedAt": "2024-01-15T10:00:00+00:00",
                    "voterTypeId": None,
                    "voterTypeName": None,
                },
                {
                    "PK": "SESS#ABCD-1234",
                    "SK": "PARTICIPANT#p-456",
                    "participantId": "p-456",
                    "name": "Sam",
                    "role": "voter",
                    "assignedRoleName": None,
                    "roleType": "Voter",
                    "roleDescription": "",
                    "privateInfo": None,
                    "ipAddress": None,
                    "joinedAt": "2024-01-15T10:01:00+00:00",
                    "voterTypeId": "board",
                    "voterTypeName": "Board Member",
                },
            ],
            "Count": 2,
        }

        result = dynamo_repo.get_session_participants("ABCD-1234")

        assert len(result) == 2
        assert result[0].id == "p-123"
        assert result[0].name == "Jordan"
        assert result[0].role == "player"
        assert result[1].voter_type_id == "board"

    def test_empty_session_returns_empty_list(self, dynamo_repo):
        """Should return empty list when no participants."""
        dynamo_repo._session_table.query.return_value = {"Items": [], "Count": 0}

        result = dynamo_repo.get_session_participants("ABCD-1234")

        assert result == []


class TestUpdateParticipantRole:
    """Tests for DynamoRepository.update_participant_role."""

    def test_success_returns_true(self, dynamo_repo):
        """Should return True and bump version on successful update."""
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("3")}
        }

        result = dynamo_repo.update_participant_role(
            "p-123", "CTO", "Chief Technology Officer", "Secret info", "ABCD-1234"
        )

        assert result is True

    def test_nonexistent_participant_returns_false(self, dynamo_repo):
        """Should return False when participant doesn't exist."""
        from botocore.exceptions import ClientError

        dynamo_repo._session_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": ""}},
            "UpdateItem",
        )

        result = dynamo_repo.update_participant_role(
            "p-nonexistent", "CTO", "desc", None, "ABCD-1234"
        )

        assert result is False


class TestRemoveParticipant:
    """Tests for DynamoRepository.remove_participant."""

    def test_existing_participant_returns_true(self, dynamo_repo):
        """Should return True and bump version when participant existed."""
        dynamo_repo._session_table.delete_item.return_value = {
            "Attributes": {
                "PK": "SESS#ABCD-1234",
                "SK": "PARTICIPANT#p-123",
                "participantId": "p-123",
                "name": "Jordan",
                "role": "player",
            }
        }
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("3")}
        }

        result = dynamo_repo.remove_participant("p-123", "ABCD-1234")

        assert result is True

    def test_nonexistent_participant_returns_false(self, dynamo_repo):
        """Should return False when participant didn't exist."""
        dynamo_repo._session_table.delete_item.return_value = {}

        result = dynamo_repo.remove_participant("p-nonexistent", "ABCD-1234")

        assert result is False


class TestHasFacilitator:
    """Tests for DynamoRepository.has_facilitator."""

    def test_returns_true_when_facilitator_exists(self, dynamo_repo):
        """Should return True when a facilitator participant exists."""
        dynamo_repo._session_table.query.return_value = {
            "Items": [
                {
                    "PK": "SESS#ABCD-1234",
                    "SK": "PARTICIPANT#p-fac",
                    "participantId": "p-fac",
                    "name": "Alice",
                    "role": "facilitator",
                },
            ],
            "Count": 1,
        }

        assert dynamo_repo.has_facilitator("ABCD-1234") is True

    def test_returns_false_when_no_facilitator(self, dynamo_repo):
        """Should return False when no facilitator participant."""
        dynamo_repo._session_table.query.return_value = {
            "Items": [
                {
                    "PK": "SESS#ABCD-1234",
                    "SK": "PARTICIPANT#p-123",
                    "participantId": "p-123",
                    "name": "Bob",
                    "role": "player",
                },
            ],
            "Count": 1,
        }

        assert dynamo_repo.has_facilitator("ABCD-1234") is False

    def test_returns_false_when_no_participants(self, dynamo_repo):
        """Should return False when session has no participants."""
        dynamo_repo._session_table.query.return_value = {"Items": [], "Count": 0}

        assert dynamo_repo.has_facilitator("ABCD-1234") is False


class TestCheckFacilitatorPin:
    """Tests for DynamoRepository.check_facilitator_pin."""

    def test_correct_pin_returns_true(self, dynamo_repo):
        """Should return True when PIN matches."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"facilitatorPin": "1234"}
        }

        assert dynamo_repo.check_facilitator_pin("ABCD-1234", "1234") is True

    def test_wrong_pin_returns_false(self, dynamo_repo):
        """Should return False when PIN doesn't match."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"facilitatorPin": "1234"}
        }

        assert dynamo_repo.check_facilitator_pin("ABCD-1234", "5678") is False

    def test_no_pin_set_returns_false(self, dynamo_repo):
        """Should return False when no PIN is set."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"facilitatorPin": None}
        }

        assert dynamo_repo.check_facilitator_pin("ABCD-1234", "1234") is False

    def test_missing_session_returns_false(self, dynamo_repo):
        """Should return False when session doesn't exist."""
        dynamo_repo._session_table.get_item.return_value = {}

        assert dynamo_repo.check_facilitator_pin("XXXX-0000", "1234") is False


class TestReplaceFacilitator:
    """Tests for DynamoRepository.replace_facilitator."""

    def test_deletes_old_and_adds_new(self, dynamo_repo):
        """Should delete existing facilitator and add new one."""
        # Query returns existing facilitator
        dynamo_repo._session_table.query.return_value = {
            "Items": [
                {
                    "PK": "SESS#ABCD-1234",
                    "SK": "PARTICIPANT#p-old-fac",
                    "participantId": "p-old-fac",
                    "name": "OldFac",
                    "role": "facilitator",
                },
            ],
            "Count": 1,
        }
        dynamo_repo._session_table.delete_item.return_value = {}
        dynamo_repo._session_table.put_item.return_value = {}
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("3")}
        }

        result = dynamo_repo.replace_facilitator("ABCD-1234", "NewFac", "10.0.0.1")

        assert isinstance(result, Participant)
        assert result.name == "NewFac"
        assert result.role == "facilitator"
        # Should have called delete_item for the old facilitator
        dynamo_repo._session_table.delete_item.assert_called_once()


class TestIsFacilitator:
    """Tests for DynamoRepository.is_facilitator."""

    def test_returns_true_for_facilitator(self, dynamo_repo):
        """Should return True when participant is facilitator."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"role": "facilitator"}
        }

        assert dynamo_repo.is_facilitator("ABCD-1234", "p-fac") is True

    def test_returns_false_for_non_facilitator(self, dynamo_repo):
        """Should return False when participant is not facilitator."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"role": "player"}
        }

        assert dynamo_repo.is_facilitator("ABCD-1234", "p-player") is False

    def test_returns_false_for_missing_participant(self, dynamo_repo):
        """Should return False when participant doesn't exist."""
        dynamo_repo._session_table.get_item.return_value = {}

        assert dynamo_repo.is_facilitator("ABCD-1234", "p-missing") is False


class TestGetSessionScenarioInfo:
    """Tests for DynamoRepository.get_session_scenario_info."""

    def test_returns_info_dict(self, dynamo_repo):
        """Should return scenario_id and scenario_version."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"scenarioId": "mental-health-app", "scenarioVersion": Decimal("3")}
        }

        result = dynamo_repo.get_session_scenario_info("ABCD-1234")

        assert result == {"scenario_id": "mental-health-app", "scenario_version": Decimal("3")}

    def test_returns_none_for_missing_session(self, dynamo_repo):
        """Should return None when session doesn't exist."""
        dynamo_repo._session_table.get_item.return_value = {}

        assert dynamo_repo.get_session_scenario_info("XXXX-0000") is None


class TestGetVoterTypesForSession:
    """Tests for DynamoRepository.get_voter_types_for_session."""

    def test_returns_voter_types(self, dynamo_repo):
        """Should return VoterType list from META voterTypes."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {
                "voterTypes": [
                    {"id": "board", "name": "Board Member", "description": "A board member"},
                    {"id": "ethics", "name": "Ethics Committee"},
                ]
            }
        }

        result = dynamo_repo.get_voter_types_for_session("ABCD-1234")

        assert len(result) == 2
        assert isinstance(result[0], VoterType)
        assert result[0].id == "board"
        assert result[1].name == "Ethics Committee"

    def test_returns_empty_when_no_voter_types(self, dynamo_repo):
        """Should return empty list when no voter types configured."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"voterTypes": []}
        }

        assert dynamo_repo.get_voter_types_for_session("ABCD-1234") == []


class TestIsRolePinsEnabled:
    """Tests for DynamoRepository.is_role_pins_enabled."""

    def test_returns_true_when_enabled(self, dynamo_repo):
        """Should return True when rolePinsEnabled is True."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"rolePinsEnabled": True}
        }

        assert dynamo_repo.is_role_pins_enabled("ABCD-1234") is True

    def test_returns_false_when_disabled(self, dynamo_repo):
        """Should return False when rolePinsEnabled is False."""
        dynamo_repo._session_table.get_item.return_value = {
            "Item": {"rolePinsEnabled": False}
        }

        assert dynamo_repo.is_role_pins_enabled("ABCD-1234") is False

    def test_returns_false_for_missing_session(self, dynamo_repo):
        """Should return False when session doesn't exist."""
        dynamo_repo._session_table.get_item.return_value = {}

        assert dynamo_repo.is_role_pins_enabled("XXXX-0000") is False

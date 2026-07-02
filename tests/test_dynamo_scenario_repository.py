"""Unit tests for DynamoScenarioRepository.

Tests the scenario repository logic using mocked DynamoDB table interactions.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from apps.api.src.dynamo_scenario_repository import DynamoScenarioRepository
from apps.api.src.scenario_repository import ScenarioVersion
from apps.api.src.dynamo_helpers import scenario_pk, version_sk


@pytest.fixture
def mock_table():
    """Create a mock DynamoDB Table resource."""
    table = MagicMock()
    table.table_name = "ssp-scenarios"
    table.meta.client = MagicMock()
    return table


@pytest.fixture
def repo(mock_table):
    """Create a DynamoScenarioRepository with a mock table."""
    return DynamoScenarioRepository(mock_table)


@pytest.fixture
def sample_scenario_data():
    """Sample scenario data for testing."""
    return {
        "id": "test-scenario",
        "title": "Test Scenario",
        "description": "A test scenario",
        "waitingRoomMessage": "Please wait...",
        "presentModeInstructions": "Welcome!",
        "settings": {"timerEnabled": True},
        "roles": [{"id": "cto", "name": "CTO"}],
        "sprints": [{"id": "sprint-1", "name": "Sprint 1"}],
        "voterTypes": [{"id": "board", "name": "Board"}],
        "enabled": True,
    }


class TestGetScenario:
    """Tests for get_scenario method."""

    def test_get_latest_returns_scenario(self, repo, mock_table):
        """get_scenario with no version returns LATEST item."""
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "SCEN#test-scenario",
                "SK": "LATEST",
                "scenarioId": "test-scenario",
                "version": 2,
                "title": "Test Scenario",
                "description": "A test",
                "settingsJson": "{}",
                "rolesJson": "[]",
                "sprintsJson": "[]",
                "voterTypesJson": None,
                "isLatest": True,
                "isDeleted": False,
                "enabled": True,
                "createdAt": "2024-01-15T10:00:00+00:00",
                "createdBy": "admin",
            }
        }

        result = repo.get_scenario("test-scenario")

        mock_table.get_item.assert_called_once_with(
            Key={"PK": "SCEN#test-scenario", "SK": "LATEST"}
        )
        assert result is not None
        assert result.id == "test-scenario"
        assert result.version == 2
        assert result.title == "Test Scenario"
        assert result.is_latest is True

    def test_get_specific_version(self, repo, mock_table):
        """get_scenario with version number returns VER# item."""
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "SCEN#test-scenario",
                "SK": "VER#000001",
                "scenarioId": "test-scenario",
                "version": 1,
                "title": "Test Scenario v1",
                "settingsJson": "{}",
                "rolesJson": "[]",
                "sprintsJson": "[]",
                "isLatest": False,
                "isDeleted": False,
                "enabled": True,
                "createdAt": "2024-01-14T10:00:00+00:00",
            }
        }

        result = repo.get_scenario("test-scenario", version=1)

        mock_table.get_item.assert_called_once_with(
            Key={"PK": "SCEN#test-scenario", "SK": "VER#000001"}
        )
        assert result is not None
        assert result.version == 1

    def test_get_nonexistent_returns_none(self, repo, mock_table):
        """get_scenario returns None when item not found."""
        mock_table.get_item.return_value = {}

        result = repo.get_scenario("nonexistent")
        assert result is None


class TestListScenarios:
    """Tests for list_scenarios method."""

    def test_list_filters_deleted(self, repo, mock_table):
        """list_scenarios excludes deleted by default."""
        mock_table.query.return_value = {
            "Items": [
                {
                    "PK": "SCEN#active",
                    "SK": "LATEST",
                    "scenarioId": "active",
                    "version": 1,
                    "title": "Active",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isDeleted": False,
                    "enabled": True,
                    "GSI1PK": "ALL_SCENARIOS",
                    "GSI1SK": "SCEN#active",
                },
                {
                    "PK": "SCEN#deleted",
                    "SK": "LATEST",
                    "scenarioId": "deleted",
                    "version": 1,
                    "title": "Deleted",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isDeleted": True,
                    "enabled": True,
                    "GSI1PK": "ALL_SCENARIOS",
                    "GSI1SK": "SCEN#deleted",
                },
            ]
        }

        result = repo.list_scenarios(include_deleted=False)
        assert len(result) == 1
        assert result[0].id == "active"

    def test_list_includes_deleted_when_requested(self, repo, mock_table):
        """list_scenarios includes deleted when include_deleted=True."""
        mock_table.query.return_value = {
            "Items": [
                {
                    "PK": "SCEN#active",
                    "SK": "LATEST",
                    "scenarioId": "active",
                    "version": 1,
                    "title": "Active",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isDeleted": False,
                    "enabled": True,
                    "GSI1PK": "ALL_SCENARIOS",
                    "GSI1SK": "SCEN#active",
                },
                {
                    "PK": "SCEN#deleted",
                    "SK": "LATEST",
                    "scenarioId": "deleted",
                    "version": 1,
                    "title": "Deleted",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isDeleted": True,
                    "enabled": True,
                    "GSI1PK": "ALL_SCENARIOS",
                    "GSI1SK": "SCEN#deleted",
                },
            ]
        }

        result = repo.list_scenarios(include_deleted=True)
        assert len(result) == 2


class TestGetScenarioHistory:
    """Tests for get_scenario_history method."""

    def test_returns_versions_newest_first(self, repo, mock_table):
        """get_scenario_history returns versions sorted descending."""
        mock_table.query.return_value = {
            "Items": [
                {
                    "PK": "SCEN#test",
                    "SK": "VER#000001",
                    "scenarioId": "test",
                    "version": 1,
                    "title": "v1",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isLatest": False,
                    "isDeleted": False,
                    "enabled": True,
                },
                {
                    "PK": "SCEN#test",
                    "SK": "VER#000002",
                    "scenarioId": "test",
                    "version": 2,
                    "title": "v2",
                    "settingsJson": "{}",
                    "rolesJson": "[]",
                    "sprintsJson": "[]",
                    "isLatest": True,
                    "isDeleted": False,
                    "enabled": True,
                },
            ]
        }

        result = repo.get_scenario_history("test")
        assert len(result) == 2
        assert result[0].version == 2
        assert result[1].version == 1


class TestCreateScenario:
    """Tests for create_scenario method."""

    def test_creates_version_and_latest(self, repo, mock_table, sample_scenario_data):
        """create_scenario writes VER#000001 and LATEST atomically."""
        result = repo.create_scenario(sample_scenario_data, created_by="admin")

        assert result.id == "test-scenario"
        assert result.version == 1
        assert result.title == "Test Scenario"
        assert result.is_latest is True
        assert result.is_deleted is False
        assert result.enabled is True
        assert result.created_by == "admin"

        # Verify TransactWriteItems was called
        mock_table.meta.client.transact_write_items.assert_called_once()
        call_args = mock_table.meta.client.transact_write_items.call_args
        transact_items = call_args[1]["TransactItems"]
        assert len(transact_items) == 2


class TestUpdateScenario:
    """Tests for update_scenario method."""

    def test_update_nonexistent_returns_none(self, repo, mock_table, sample_scenario_data):
        """update_scenario returns None for non-existent scenario."""
        mock_table.get_item.return_value = {}

        result = repo.update_scenario("nonexistent", sample_scenario_data)
        assert result is None

    def test_update_creates_new_version(self, repo, mock_table, sample_scenario_data):
        """update_scenario creates a new version with incremented version number."""
        # Mock get_scenario (called internally to check existence)
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "SCEN#test-scenario",
                "SK": "LATEST",
                "scenarioId": "test-scenario",
                "version": 1,
                "title": "Old Title",
                "settingsJson": "{}",
                "rolesJson": "[]",
                "sprintsJson": "[]",
                "isLatest": True,
                "isDeleted": False,
                "enabled": True,
                "createdAt": "2024-01-14T10:00:00+00:00",
            }
        }

        result = repo.update_scenario("test-scenario", sample_scenario_data, created_by="admin")

        assert result is not None
        assert result.version == 2
        assert result.title == "Test Scenario"
        mock_table.meta.client.transact_write_items.assert_called_once()


class TestDeleteRestoreEnableDisable:
    """Tests for delete, restore, enable, disable operations."""

    def test_delete_existing_returns_true(self, repo, mock_table):
        """delete_scenario returns True when scenario exists."""
        mock_table.update_item.return_value = {}

        result = repo.delete_scenario("test-scenario")
        assert result is True

        mock_table.update_item.assert_called_once()
        call_kwargs = mock_table.update_item.call_args[1]
        assert call_kwargs["Key"] == {"PK": "SCEN#test-scenario", "SK": "LATEST"}

    def test_delete_nonexistent_returns_false(self, repo, mock_table):
        """delete_scenario returns False when scenario doesn't exist."""
        error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        mock_table.update_item.side_effect = ClientError(
            error_response, "UpdateItem"
        )

        result = repo.delete_scenario("nonexistent")
        assert result is False

    def test_restore_existing_returns_true(self, repo, mock_table):
        """restore_scenario returns True when scenario exists."""
        mock_table.update_item.return_value = {}
        result = repo.restore_scenario("test-scenario")
        assert result is True

    def test_restore_nonexistent_returns_false(self, repo, mock_table):
        """restore_scenario returns False when scenario doesn't exist."""
        error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        mock_table.update_item.side_effect = ClientError(
            error_response, "UpdateItem"
        )
        result = repo.restore_scenario("nonexistent")
        assert result is False

    def test_enable_existing_returns_true(self, repo, mock_table):
        """enable_scenario returns True when scenario exists."""
        mock_table.update_item.return_value = {}
        result = repo.enable_scenario("test-scenario")
        assert result is True

    def test_disable_existing_returns_true(self, repo, mock_table):
        """disable_scenario returns True when scenario exists."""
        mock_table.update_item.return_value = {}
        result = repo.disable_scenario("test-scenario")
        assert result is True

    def test_enable_nonexistent_returns_false(self, repo, mock_table):
        """enable_scenario returns False when scenario doesn't exist."""
        error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        mock_table.update_item.side_effect = ClientError(
            error_response, "UpdateItem"
        )
        result = repo.enable_scenario("nonexistent")
        assert result is False

    def test_disable_nonexistent_returns_false(self, repo, mock_table):
        """disable_scenario returns False when scenario doesn't exist."""
        error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        mock_table.update_item.side_effect = ClientError(
            error_response, "UpdateItem"
        )
        result = repo.disable_scenario("nonexistent")
        assert result is False


class TestDiffScenario:
    """Tests for diff_scenario method."""

    def test_diff_nonexistent_returns_error(self, repo, mock_table):
        """diff_scenario returns error dict for non-existent scenario."""
        mock_table.get_item.return_value = {}

        result = repo.diff_scenario("nonexistent", {"title": "New"})
        assert result["error"] == "Scenario not found"
        assert result["isNew"] is True

    def test_diff_detects_changes(self, repo, mock_table):
        """diff_scenario correctly detects field changes."""
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "SCEN#test",
                "SK": "LATEST",
                "scenarioId": "test",
                "version": 1,
                "title": "Old Title",
                "description": "Old desc",
                "settingsJson": "{}",
                "rolesJson": "[]",
                "sprintsJson": "[]",
                "isLatest": True,
                "isDeleted": False,
                "enabled": True,
            }
        }

        result = repo.diff_scenario("test", {"title": "New Title", "description": "Old desc"})
        assert result["hasChanges"] is True
        assert any(c["field"] == "title" for c in result["changes"])

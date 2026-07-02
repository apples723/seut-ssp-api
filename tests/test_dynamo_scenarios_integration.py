"""Integration tests for DynamoScenarioRepository against DynamoDB Local.

These tests require DynamoDB Local to be running on http://localhost:8042.
Start it with: docker compose -f docker-compose.local.yml -f docker-compose.dynamodb.yml up dynamodb-local

Tests are skipped gracefully when DynamoDB Local is not available.

Validates Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 14.2, 14.3
"""
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Generator

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from apps.api.src.dynamo_scenario_repository import DynamoScenarioRepository
from apps.api.src.scenario_repository import ScenarioVersion

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DYNAMODB_ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT", "http://localhost:8042")
SCENARIO_TABLE_NAME = "ssp-scenarios-test"


# ---------------------------------------------------------------------------
# Connection check — skip all tests if DynamoDB Local is not reachable
# ---------------------------------------------------------------------------


def _dynamodb_local_available() -> bool:
    """Check if DynamoDB Local is reachable."""
    try:
        client = boto3.client(
            "dynamodb",
            endpoint_url=DYNAMODB_ENDPOINT,
            region_name="us-east-1",
            aws_access_key_id="fakeKey",
            aws_secret_access_key="fakeSecret",
        )
        client.list_tables()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _dynamodb_local_available(),
        reason="DynamoDB Local not available at " + DYNAMODB_ENDPOINT,
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dynamodb_resource():
    """Create a boto3 DynamoDB resource connected to DynamoDB Local."""
    return boto3.resource(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="fakeKey",
        aws_secret_access_key="fakeSecret",
    )


@pytest.fixture(autouse=True)
def scenario_table(dynamodb_resource) -> Generator[Any, None, None]:
    """Create a fresh scenario table for each test, then tear it down."""
    # Create the table
    try:
        dynamodb_resource.create_table(
            TableName=SCENARIO_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi-scenarios-list",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Wait for table to be active
        dynamodb_resource.meta.client.get_waiter("table_exists").wait(
            TableName=SCENARIO_TABLE_NAME
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            # Table already exists — delete and recreate
            dynamodb_resource.Table(SCENARIO_TABLE_NAME).delete()
            dynamodb_resource.meta.client.get_waiter("table_not_exists").wait(
                TableName=SCENARIO_TABLE_NAME
            )
            dynamodb_resource.create_table(
                TableName=SCENARIO_TABLE_NAME,
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                    {"AttributeName": "GSI1PK", "AttributeType": "S"},
                    {"AttributeName": "GSI1SK", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "gsi-scenarios-list",
                        "KeySchema": [
                            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    },
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            dynamodb_resource.meta.client.get_waiter("table_exists").wait(
                TableName=SCENARIO_TABLE_NAME
            )
        else:
            raise

    table = dynamodb_resource.Table(SCENARIO_TABLE_NAME)
    yield table

    # Teardown: delete table after test
    table.delete()
    dynamodb_resource.meta.client.get_waiter("table_not_exists").wait(
        TableName=SCENARIO_TABLE_NAME
    )


@pytest.fixture
def repo(scenario_table) -> DynamoScenarioRepository:
    """Create a DynamoScenarioRepository with a real DynamoDB Local table."""
    return DynamoScenarioRepository(scenario_table)


@pytest.fixture
def sample_scenario_data() -> dict:
    """Sample scenario data for testing."""
    return {
        "id": "integration-test-scenario",
        "title": "Integration Test Scenario",
        "description": "A scenario used for integration testing",
        "waitingRoomMessage": "Please wait for the facilitator...",
        "presentModeInstructions": "Welcome to the simulation!",
        "settings": {"timerEnabled": True, "timerSeconds": 480},
        "roles": [
            {"id": "cto", "name": "CTO", "description": "Chief Technology Officer"},
            {"id": "cfo", "name": "CFO", "description": "Chief Financial Officer"},
        ],
        "sprints": [
            {
                "id": "sprint-1",
                "name": "Sprint 1",
                "description": "First sprint",
                "responseBoxes": ["Ethics", "Business"],
            },
            {
                "id": "sprint-2",
                "name": "Sprint 2",
                "description": "Second sprint",
            },
        ],
        "voterTypes": [
            {"id": "board", "name": "Board Member"},
            {"id": "ethics", "name": "Ethics Committee"},
        ],
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# Test: Create a scenario and verify it appears in list_scenarios
# ---------------------------------------------------------------------------


class TestCreateAndList:
    """Test creating scenarios and listing them."""

    def test_create_scenario_appears_in_list(self, repo, sample_scenario_data):
        """Create a scenario and verify it appears in list_scenarios."""
        created = repo.create_scenario(sample_scenario_data, created_by="test-user")

        assert created.id == "integration-test-scenario"
        assert created.version == 1
        assert created.title == "Integration Test Scenario"
        assert created.enabled is True
        assert created.is_deleted is False
        assert created.created_by == "test-user"

        # Verify it appears in listing
        scenarios = repo.list_scenarios()
        assert len(scenarios) == 1
        assert scenarios[0].id == "integration-test-scenario"
        assert scenarios[0].title == "Integration Test Scenario"

    def test_create_multiple_scenarios_all_listed(self, repo):
        """Multiple created scenarios all appear in list."""
        for i in range(3):
            repo.create_scenario(
                {"id": f"scenario-{i}", "title": f"Scenario {i}"},
                created_by="test",
            )

        scenarios = repo.list_scenarios()
        assert len(scenarios) == 3
        ids = {s.id for s in scenarios}
        assert ids == {"scenario-0", "scenario-1", "scenario-2"}


# ---------------------------------------------------------------------------
# Test: Update a scenario and verify version increments
# ---------------------------------------------------------------------------


class TestUpdateScenario:
    """Test updating scenarios and version management."""

    def test_update_increments_version(self, repo, sample_scenario_data):
        """Updating a scenario increments its version number."""
        repo.create_scenario(sample_scenario_data, created_by="creator")

        updated_data = {**sample_scenario_data, "title": "Updated Title"}
        result = repo.update_scenario(
            "integration-test-scenario", updated_data, created_by="updater"
        )

        assert result is not None
        assert result.version == 2
        assert result.title == "Updated Title"
        assert result.created_by == "updater"

    def test_update_latest_matches_newest_version(self, repo, sample_scenario_data):
        """After update, LATEST item contains the newest version data."""
        repo.create_scenario(sample_scenario_data, created_by="creator")

        updated_data = {**sample_scenario_data, "title": "Version 2 Title"}
        repo.update_scenario(
            "integration-test-scenario", updated_data, created_by="v2-author"
        )

        latest = repo.get_scenario("integration-test-scenario")
        assert latest is not None
        assert latest.version == 2
        assert latest.title == "Version 2 Title"
        assert latest.created_by == "v2-author"

    def test_update_nonexistent_returns_none(self, repo):
        """Updating a non-existent scenario returns None."""
        result = repo.update_scenario("nonexistent", {"title": "Won't work"})
        assert result is None

    def test_multiple_updates_produce_sequential_versions(
        self, repo, sample_scenario_data
    ):
        """Multiple updates produce version 1, 2, 3, ...."""
        repo.create_scenario(sample_scenario_data)

        for i in range(2, 6):
            result = repo.update_scenario(
                "integration-test-scenario",
                {**sample_scenario_data, "title": f"Version {i}"},
            )
            assert result is not None
            assert result.version == i

        latest = repo.get_scenario("integration-test-scenario")
        assert latest is not None
        assert latest.version == 5


# ---------------------------------------------------------------------------
# Test: Get scenario history
# ---------------------------------------------------------------------------


class TestScenarioHistory:
    """Test version history retrieval."""

    def test_history_returns_all_versions_newest_first(
        self, repo, sample_scenario_data
    ):
        """get_scenario_history returns all versions sorted newest-first."""
        repo.create_scenario(sample_scenario_data)
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "V2"},
        )
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "V3"},
        )

        history = repo.get_scenario_history("integration-test-scenario")
        assert len(history) == 3
        assert history[0].version == 3
        assert history[1].version == 2
        assert history[2].version == 1

    def test_history_each_version_has_correct_data(self, repo, sample_scenario_data):
        """Each version in history preserves its original data."""
        repo.create_scenario(sample_scenario_data)
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "Updated Title"},
        )

        history = repo.get_scenario_history("integration-test-scenario")
        assert history[0].title == "Updated Title"
        assert history[1].title == "Integration Test Scenario"


# ---------------------------------------------------------------------------
# Test: Soft-delete and restore
# ---------------------------------------------------------------------------


class TestSoftDeleteRestore:
    """Test soft-delete and restore functionality."""

    def test_soft_delete_excludes_from_list(self, repo, sample_scenario_data):
        """Soft-deleted scenarios are excluded from list_scenarios."""
        repo.create_scenario(sample_scenario_data)

        result = repo.delete_scenario("integration-test-scenario")
        assert result is True

        scenarios = repo.list_scenarios(include_deleted=False)
        assert len(scenarios) == 0

    def test_soft_delete_still_retrievable_directly(self, repo, sample_scenario_data):
        """Soft-deleted scenarios can still be retrieved by get_scenario."""
        repo.create_scenario(sample_scenario_data)
        repo.delete_scenario("integration-test-scenario")

        scenario = repo.get_scenario("integration-test-scenario")
        assert scenario is not None
        assert scenario.is_deleted is True

    def test_soft_delete_included_when_requested(self, repo, sample_scenario_data):
        """Soft-deleted scenarios appear in list when include_deleted=True."""
        repo.create_scenario(sample_scenario_data)
        repo.delete_scenario("integration-test-scenario")

        scenarios = repo.list_scenarios(include_deleted=True)
        assert len(scenarios) == 1
        assert scenarios[0].is_deleted is True

    def test_restore_reappears_in_list(self, repo, sample_scenario_data):
        """Restored scenarios reappear in list_scenarios."""
        repo.create_scenario(sample_scenario_data)
        repo.delete_scenario("integration-test-scenario")

        result = repo.restore_scenario("integration-test-scenario")
        assert result is True

        scenarios = repo.list_scenarios(include_deleted=False)
        assert len(scenarios) == 1
        assert scenarios[0].id == "integration-test-scenario"
        assert scenarios[0].is_deleted is False

    def test_delete_nonexistent_returns_false(self, repo):
        """Deleting a non-existent scenario returns False."""
        result = repo.delete_scenario("nonexistent")
        assert result is False

    def test_restore_nonexistent_returns_false(self, repo):
        """Restoring a non-existent scenario returns False."""
        result = repo.restore_scenario("nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# Test: Enable and disable
# ---------------------------------------------------------------------------


class TestEnableDisable:
    """Test enable/disable functionality."""

    def test_disable_scenario(self, repo, sample_scenario_data):
        """Disabling a scenario sets enabled=False."""
        repo.create_scenario(sample_scenario_data)

        result = repo.disable_scenario("integration-test-scenario")
        assert result is True

        scenario = repo.get_scenario("integration-test-scenario")
        assert scenario is not None
        assert scenario.enabled is False

    def test_enable_scenario(self, repo, sample_scenario_data):
        """Enabling a disabled scenario sets enabled=True."""
        repo.create_scenario({**sample_scenario_data, "enabled": False})
        repo.disable_scenario("integration-test-scenario")

        result = repo.enable_scenario("integration-test-scenario")
        assert result is True

        scenario = repo.get_scenario("integration-test-scenario")
        assert scenario is not None
        assert scenario.enabled is True

    def test_enable_nonexistent_returns_false(self, repo):
        """Enabling a non-existent scenario returns False."""
        result = repo.enable_scenario("nonexistent")
        assert result is False

    def test_disable_nonexistent_returns_false(self, repo):
        """Disabling a non-existent scenario returns False."""
        result = repo.disable_scenario("nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# Test: Get specific version
# ---------------------------------------------------------------------------


class TestGetSpecificVersion:
    """Test retrieving specific versions of a scenario."""

    def test_get_specific_version_returns_correct_data(
        self, repo, sample_scenario_data
    ):
        """Getting a specific version returns that version's data."""
        repo.create_scenario(sample_scenario_data)
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "Version 2"},
        )

        v1 = repo.get_scenario("integration-test-scenario", version=1)
        assert v1 is not None
        assert v1.version == 1
        assert v1.title == "Integration Test Scenario"

        v2 = repo.get_scenario("integration-test-scenario", version=2)
        assert v2 is not None
        assert v2.version == 2
        assert v2.title == "Version 2"

    def test_get_nonexistent_version_returns_none(self, repo, sample_scenario_data):
        """Getting a version that doesn't exist returns None."""
        repo.create_scenario(sample_scenario_data)

        result = repo.get_scenario("integration-test-scenario", version=99)
        assert result is None


# ---------------------------------------------------------------------------
# Test: Non-existent scenarios
# ---------------------------------------------------------------------------


class TestNonExistentScenarios:
    """Test behavior with non-existent scenarios."""

    def test_get_nonexistent_returns_none(self, repo):
        """get_scenario returns None for non-existent scenario."""
        result = repo.get_scenario("does-not-exist")
        assert result is None

    def test_history_nonexistent_returns_empty(self, repo):
        """get_scenario_history returns empty list for non-existent scenario."""
        result = repo.get_scenario_history("does-not-exist")
        assert result == []

    def test_update_nonexistent_returns_none(self, repo):
        """update_scenario returns None for non-existent scenario."""
        result = repo.update_scenario("does-not-exist", {"title": "X"})
        assert result is None


# ---------------------------------------------------------------------------
# Test: GSI listing returns only latest, non-deleted scenarios
# ---------------------------------------------------------------------------


class TestGSIListing:
    """Test that GSI queries return correct results."""

    def test_gsi_returns_only_latest_versions(self, repo, sample_scenario_data):
        """list_scenarios (via GSI) returns only latest version per scenario."""
        repo.create_scenario(sample_scenario_data)
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "Latest Title"},
        )

        scenarios = repo.list_scenarios()
        assert len(scenarios) == 1
        assert scenarios[0].title == "Latest Title"
        assert scenarios[0].version == 2

    def test_gsi_excludes_deleted(self, repo):
        """list_scenarios excludes soft-deleted scenarios by default."""
        repo.create_scenario({"id": "active-1", "title": "Active"})
        repo.create_scenario({"id": "deleted-1", "title": "Will Delete"})
        repo.delete_scenario("deleted-1")

        scenarios = repo.list_scenarios(include_deleted=False)
        assert len(scenarios) == 1
        assert scenarios[0].id == "active-1"

    def test_gsi_includes_deleted_when_requested(self, repo):
        """list_scenarios includes soft-deleted when include_deleted=True."""
        repo.create_scenario({"id": "active-1", "title": "Active"})
        repo.create_scenario({"id": "deleted-1", "title": "Will Delete"})
        repo.delete_scenario("deleted-1")

        scenarios = repo.list_scenarios(include_deleted=True)
        assert len(scenarios) == 2


# ---------------------------------------------------------------------------
# Test: TransactWriteItems atomicity (LATEST always matches latest version)
# ---------------------------------------------------------------------------


class TestTransactionAtomicity:
    """Test that TransactWriteItems ensures LATEST is always consistent."""

    def test_latest_always_matches_newest_version_after_create(
        self, repo, sample_scenario_data
    ):
        """After create, LATEST matches version 1."""
        repo.create_scenario(sample_scenario_data)

        latest = repo.get_scenario("integration-test-scenario")
        v1 = repo.get_scenario("integration-test-scenario", version=1)

        assert latest is not None
        assert v1 is not None
        assert latest.version == v1.version
        assert latest.title == v1.title

    def test_latest_always_matches_newest_version_after_updates(
        self, repo, sample_scenario_data
    ):
        """After multiple updates, LATEST matches the highest version."""
        repo.create_scenario(sample_scenario_data)

        for i in range(2, 5):
            repo.update_scenario(
                "integration-test-scenario",
                {**sample_scenario_data, "title": f"Version {i}"},
            )

        latest = repo.get_scenario("integration-test-scenario")
        assert latest is not None
        assert latest.version == 4
        assert latest.title == "Version 4"

        # Verify specific version matches
        v4 = repo.get_scenario("integration-test-scenario", version=4)
        assert v4 is not None
        assert v4.title == latest.title

    def test_latest_preserves_enabled_state_across_updates(
        self, repo, sample_scenario_data
    ):
        """Enabled state is preserved through version updates."""
        repo.create_scenario(sample_scenario_data)
        repo.disable_scenario("integration-test-scenario")

        # Update should preserve disabled state
        repo.update_scenario(
            "integration-test-scenario",
            {**sample_scenario_data, "title": "New Version"},
        )

        latest = repo.get_scenario("integration-test-scenario")
        assert latest is not None
        assert latest.enabled is False
        assert latest.title == "New Version"


# ---------------------------------------------------------------------------
# Test: Migration script end-to-end
# ---------------------------------------------------------------------------


class TestMigrationScript:
    """Test the migration script against DynamoDB Local."""

    def test_migration_from_json_source(self, scenario_table):
        """Migration script reads JSON source and writes to DynamoDB."""
        # Create a temporary scenarios directory with test data
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a scenario JSON file
            scenario_dir = os.path.join(tmp_dir, "test-migration-scenario")
            os.makedirs(scenario_dir)
            scenario_data = {
                "id": "test-migration-scenario",
                "title": "Migration Test",
                "description": "Testing migration script",
                "settings": {"timerEnabled": True},
                "roles": [{"id": "role-a", "name": "Role A"}],
                "sprints": [{"id": "s1", "name": "Sprint 1", "description": "First"}],
                "voterTypes": [{"id": "vt1", "name": "Voter Type 1"}],
            }
            with open(os.path.join(scenario_dir, "scenario.json"), "w") as f:
                json.dump(scenario_data, f)

            # Run migration script
            env = os.environ.copy()
            env["DYNAMODB_ENDPOINT"] = DYNAMODB_ENDPOINT
            env["SCENARIOS_DIR"] = tmp_dir
            env["SCENARIO_TABLE_NAME"] = SCENARIO_TABLE_NAME

            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(
                        os.path.dirname(__file__), "..", "migrate_scenarios.py"
                    ),
                    "--source",
                    "json",
                ],
                env=env,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(__file__),
            )

            assert result.returncode == 0, (
                f"Migration failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # Verify data was written correctly
            repo = DynamoScenarioRepository(scenario_table)
            scenario = repo.get_scenario("test-migration-scenario")
            assert scenario is not None
            assert scenario.title == "Migration Test"
            assert scenario.version == 1
            assert scenario.description == "Testing migration script"

            # Verify version item exists
            v1 = repo.get_scenario("test-migration-scenario", version=1)
            assert v1 is not None
            assert v1.title == "Migration Test"

    def test_migration_idempotency(self, scenario_table):
        """Running migration twice produces no duplicates."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create scenario file
            scenario_dir = os.path.join(tmp_dir, "idempotency-scenario")
            os.makedirs(scenario_dir)
            scenario_data = {
                "id": "idempotency-scenario",
                "title": "Idempotency Test",
                "description": "Should not duplicate",
                "settings": {},
                "roles": [],
                "sprints": [],
            }
            with open(os.path.join(scenario_dir, "scenario.json"), "w") as f:
                json.dump(scenario_data, f)

            env = os.environ.copy()
            env["DYNAMODB_ENDPOINT"] = DYNAMODB_ENDPOINT
            env["SCENARIOS_DIR"] = tmp_dir
            env["SCENARIO_TABLE_NAME"] = SCENARIO_TABLE_NAME

            migrate_script = os.path.join(
                os.path.dirname(__file__), "..", "migrate_scenarios.py"
            )

            # Run migration first time
            result1 = subprocess.run(
                [sys.executable, migrate_script, "--source", "json"],
                env=env,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(__file__),
            )
            assert result1.returncode == 0, (
                f"First migration failed:\nstderr: {result1.stderr}"
            )

            # Run migration second time — should skip existing
            result2 = subprocess.run(
                [sys.executable, migrate_script, "--source", "json"],
                env=env,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(__file__),
            )
            assert result2.returncode == 0, (
                f"Second migration failed:\nstderr: {result2.stderr}"
            )
            assert "already exists" in result2.stderr or "Skipped" in result2.stderr

            # Verify only one version exists (no duplicates)
            repo = DynamoScenarioRepository(scenario_table)
            history = repo.get_scenario_history("idempotency-scenario")
            assert len(history) == 1
            assert history[0].version == 1

            # Verify LATEST is correct
            latest = repo.get_scenario("idempotency-scenario")
            assert latest is not None
            assert latest.version == 1
            assert latest.title == "Idempotency Test"

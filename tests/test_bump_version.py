"""Unit tests for DynamoRepository._bump_version helper."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def dynamo_repo():
    """Create a DynamoRepository instance with mocked boto3 resources."""
    with patch("apps.api.src.dynamo_repository.boto3") as mock_boto3:
        mock_resource = MagicMock()
        mock_boto3.resource.return_value = mock_resource
        # Simulate table already exists so _ensure_tables is a no-op
        mock_resource.meta.client.list_tables.return_value = {
            "TableNames": ["ssp-sessions", "ssp-scenarios"]
        }

        with patch.dict("os.environ", {"DYNAMODB_ENDPOINT": "http://localhost:8042"}):
            from apps.api.src.dynamo_repository import DynamoRepository

            repo = DynamoRepository()

    return repo


class TestBumpVersion:
    """Tests for the _bump_version private helper."""

    def test_returns_new_version_as_int(self, dynamo_repo):
        """_bump_version should return the new version as a plain int."""
        # boto3 returns Decimal values from DynamoDB
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("2")}
        }

        result = dynamo_repo._bump_version("ABCD-1234")

        assert result == 2
        assert isinstance(result, int)

    def test_uses_correct_key(self, dynamo_repo):
        """_bump_version should use the correct PK and SK for the META item."""
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("5")}
        }

        dynamo_repo._bump_version("TEST-9999")

        call_kwargs = dynamo_repo._session_table.update_item.call_args[1]
        assert call_kwargs["Key"] == {"PK": "SESS#TEST-9999", "SK": "META"}

    def test_uses_atomic_increment_expression(self, dynamo_repo):
        """_bump_version should use SET version = version + :one."""
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("3")}
        }

        dynamo_repo._bump_version("ABCD-1234")

        call_kwargs = dynamo_repo._session_table.update_item.call_args[1]
        assert call_kwargs["UpdateExpression"] == "SET version = version + :one"
        assert call_kwargs["ExpressionAttributeValues"] == {":one": 1}

    def test_requests_updated_new_return_values(self, dynamo_repo):
        """_bump_version should request UPDATED_NEW to get the new version."""
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("1")}
        }

        dynamo_repo._bump_version("ABCD-1234")

        call_kwargs = dynamo_repo._session_table.update_item.call_args[1]
        assert call_kwargs["ReturnValues"] == "UPDATED_NEW"

    def test_converts_decimal_to_int(self, dynamo_repo):
        """_bump_version should convert DynamoDB Decimal to Python int."""
        dynamo_repo._session_table.update_item.return_value = {
            "Attributes": {"version": Decimal("42")}
        }

        result = dynamo_repo._bump_version("ABCD-1234")

        assert result == 42
        assert type(result) is int

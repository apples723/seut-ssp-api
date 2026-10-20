import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from src.dynamo_tutorial_repository import DynamoTutorialRepository
from src.tutorial_repository import TutorialConflictError


class RecordingClient:
    def __init__(self) -> None:
        self.calls = []

    def transact_write_items(self, **kwargs) -> None:
        self.calls.append(kwargs)


class FakeTable:
    table_name = "ssp-scenarios"

    def __init__(self, head: dict, versions: dict[int, dict] | None = None) -> None:
        self.head = head
        self.versions = versions or {}
        self.client = RecordingClient()
        self.meta = SimpleNamespace(client=self.client)

    def get_item(self, Key, ConsistentRead=True):
        if Key["SK"] == "HEAD":
            return {"Item": self.head} if self.head else {}
        version = int(Key["SK"].split("#", 1)[1])
        item = self.versions.get(version)
        return {"Item": item} if item else {}


def version_item(version: int = 1) -> dict:
    return {
        "PK": "TUTOR#default",
        "SK": f"VER#{version:06d}",
        "tutorialId": "default",
        "version": version,
        "status": "draft",
        "configJson": json.dumps({"id": "default", "title": "Tutorial"}),
        "createdAt": "2025-01-01T00:00:00+00:00",
    }


def head(**overrides) -> dict:
    value = {
        "PK": "TUTOR#default",
        "SK": "HEAD",
        "tutorialId": "default",
        "latestVersion": 1,
        "currentDraftVersion": 1,
        "revision": 4,
        "disabled": True,
    }
    value.update(overrides)
    return value


def test_update_conditions_on_revision_and_preserves_disabled_state() -> None:
    table = FakeTable(head())
    repository = DynamoTutorialRepository(table)

    saved = repository.update_tutorial(
        "default", {"id": "default", "title": "Changed"},
        expected_revision=4, created_by="admin",
    )

    assert saved is not None
    assert saved.version == 2
    assert saved.revision == 5
    transaction = table.client.calls[0]["TransactItems"]
    head_update = transaction[1]["Update"]
    assert "revision = :expected" in head_update["ConditionExpression"]
    assert "revision = :revision" in head_update["UpdateExpression"]
    assert "disabled" not in head_update["UpdateExpression"]


def test_publish_and_disable_advance_the_same_revision_token() -> None:
    current_head = head(disabled=False)
    table = FakeTable(current_head, {1: version_item()})
    repository = DynamoTutorialRepository(table)

    published = repository.publish_tutorial("default", 1, expected_revision=4)
    assert published is not None and published.revision == 5
    publish_head = table.client.calls[0]["TransactItems"][-1]["Update"]
    assert "revision = :expected" in publish_head["ConditionExpression"]

    current_head.update({"revision": 5, "publishedVersion": 1})
    current_head.pop("currentDraftVersion", None)
    disabled = repository.disable_tutorial("default", expected_revision=5)
    assert disabled is not None and disabled.revision == 6
    disable_head = table.client.calls[1]["TransactItems"][-1]["Update"]
    assert "revision = :expected" in disable_head["ConditionExpression"]


def test_stale_revision_fails_before_a_transaction_is_built() -> None:
    table = FakeTable(head(revision=7))
    repository = DynamoTutorialRepository(table)

    with pytest.raises(TutorialConflictError) as stale:
        repository.update_tutorial(
            "default", {"id": "default"}, expected_revision=6, created_by="admin"
        )

    assert stale.value.current_revision == 7
    assert table.client.calls == []


def test_only_conditional_transaction_cancellations_map_to_conflicts() -> None:
    conditional = ClientError({
        "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
        "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
    }, "TransactWriteItems")
    throttled = ClientError({
        "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
        "CancellationReasons": [{"Code": "ProvisionedThroughputExceeded"}],
    }, "TransactWriteItems")

    assert DynamoTutorialRepository._is_conditional(conditional)
    assert not DynamoTutorialRepository._is_conditional(throttled)

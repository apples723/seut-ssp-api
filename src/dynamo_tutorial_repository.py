"""DynamoDB tutorial persistence in the existing scenario table."""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from .dynamo_helpers import query_all_pages, validate_id, version_sk
from .tutorial_repository import (
    TutorialAlreadyExistsError,
    TutorialConflictError,
    TutorialVersion,
    serialize_tutorial_config,
)

GSI_SCENARIOS_LIST = "gsi-scenarios-list"
GSI_TUTORIALS_VALUE = "ALL_TUTORIALS"


def tutorial_pk(tutorial_id: str) -> str:
    return f"TUTOR#{validate_id(tutorial_id)}"


class DynamoTutorialRepository:
    """Stores immutable VER items and one conditionally updated HEAD pointer item."""

    def __init__(self, scenario_table: Any):
        self._table = scenario_table
        self._client = scenario_table.meta.client
        self._serializer = TypeSerializer()

    def list_tutorials(self) -> List[TutorialVersion]:
        heads = query_all_pages(
            self._table,
            Key("GSI1PK").eq(GSI_TUTORIALS_VALUE),
            IndexName=GSI_SCENARIOS_LIST,
        )
        tutorials = []
        for head in heads:
            tutorial = self.get_tutorial(head["tutorialId"], int(head["latestVersion"]))
            if tutorial:
                tutorial.revision = int(head.get("revision", head["latestVersion"]))
                if head.get("disabled", False):
                    tutorial.status = "disabled"
                tutorials.append(tutorial)
        tutorials.sort(key=lambda item: item.config.get("title", "").lower())
        return tutorials

    def get_tutorial(self, tutorial_id: str, version: Optional[int] = None) -> Optional[TutorialVersion]:
        pk = tutorial_pk(tutorial_id)
        head = self._get_head(tutorial_id)
        if not head:
            return None
        if version is None:
            version = int(head.get("currentDraftVersion", head["latestVersion"]))
        response = self._table.get_item(
            Key={"PK": pk, "SK": version_sk(version)}, ConsistentRead=True
        )
        item = response.get("Item")
        tutorial = self._item_to_version(item) if item else None
        if tutorial:
            tutorial.revision = int(head.get("revision", head["latestVersion"]))
        return tutorial

    def get_published_tutorial(self, tutorial_id: str) -> Optional[TutorialVersion]:
        head = self._get_head(tutorial_id)
        if not head or head.get("disabled", False) or head.get("publishedVersion") is None:
            return None
        tutorial = self.get_tutorial(tutorial_id, int(head["publishedVersion"]))
        return tutorial if tutorial and tutorial.status == "published" else None

    def get_tutorial_history(self, tutorial_id: str) -> List[TutorialVersion]:
        head = self._get_head(tutorial_id)
        if not head:
            return []
        items = query_all_pages(
            self._table,
            Key("PK").eq(tutorial_pk(tutorial_id)) & Key("SK").begins_with("VER#"),
            ConsistentRead=True,
        )
        revision = int(head.get("revision", head["latestVersion"]))
        versions = [self._item_to_version(item) for item in items]
        for version in versions:
            version.revision = revision
        versions.sort(key=lambda item: item.version, reverse=True)
        return versions

    def create_tutorial(self, config: Dict[str, Any], created_by: Optional[str]) -> TutorialVersion:
        tutorial_id = config["id"]
        pk = tutorial_pk(tutorial_id)
        now = datetime.now(timezone.utc).isoformat()
        version_item = self._build_version_item(tutorial_id, 1, "draft", config, now, created_by)
        head_item = {
            "PK": pk,
            "SK": "HEAD",
            "tutorialId": tutorial_id,
            "latestVersion": 1,
            "revision": 1,
            "currentDraftVersion": 1,
            "disabled": False,
            "GSI1PK": GSI_TUTORIALS_VALUE,
            "GSI1SK": f"TUTOR#{tutorial_id}",
        }
        try:
            self._client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self._table.table_name, "Item": self._serialize(version_item),
                         "ConditionExpression": "attribute_not_exists(PK)"}},
                {"Put": {"TableName": self._table.table_name, "Item": self._serialize(head_item),
                         "ConditionExpression": "attribute_not_exists(PK)"}},
            ])
        except ClientError as error:
            if self._is_conditional(error):
                raise TutorialAlreadyExistsError(tutorial_id) from error
            raise
        return TutorialVersion(
            tutorial_id, 1, "draft", config, now, created_by, revision=1
        )

    def update_tutorial(
        self, tutorial_id: str, config: Dict[str, Any], expected_revision: int,
        created_by: Optional[str],
    ) -> Optional[TutorialVersion]:
        head = self._get_head(tutorial_id)
        if not head:
            return None
        current_revision = int(head.get("revision", head["latestVersion"]))
        if current_revision != expected_revision:
            raise TutorialConflictError(current_revision)
        new_version = int(head["latestVersion"]) + 1
        new_revision = current_revision + 1
        now = datetime.now(timezone.utc).isoformat()
        item = self._build_version_item(tutorial_id, new_version, "draft", config, now, created_by)
        transactions = [
            {"Put": {"TableName": self._table.table_name, "Item": self._serialize(item),
                     "ConditionExpression": "attribute_not_exists(PK)"}},
            {"Update": {
                "TableName": self._table.table_name,
                "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": "HEAD"}),
                "UpdateExpression": "SET latestVersion = :new, currentDraftVersion = :new, revision = :revision",
                "ConditionExpression": "revision = :expected OR (attribute_not_exists(revision) AND latestVersion = :expected)",
                "ExpressionAttributeValues": self._serialize({
                    ":new": new_version,
                    ":revision": new_revision,
                    ":expected": expected_revision,
                }),
            }},
        ]
        old_draft = head.get("currentDraftVersion")
        if old_draft is not None:
            transactions.append({"Update": {
                "TableName": self._table.table_name,
                "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": version_sk(int(old_draft))}),
                "UpdateExpression": "SET #status = :disabled",
                "ConditionExpression": "#status = :draft",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": self._serialize({":disabled": "disabled", ":draft": "draft"}),
            }})
        self._transact_with_conflict(transactions, tutorial_id)
        return TutorialVersion(
            tutorial_id, new_version, "draft", config, now, created_by,
            revision=new_revision,
        )

    def publish_tutorial(
        self, tutorial_id: str, version: int, expected_revision: int
    ) -> Optional[TutorialVersion]:
        head = self._get_head(tutorial_id)
        if not head:
            return None
        current_revision = int(head.get("revision", head["latestVersion"]))
        if current_revision != expected_revision:
            raise TutorialConflictError(current_revision)
        new_revision = current_revision + 1
        selected = self.get_tutorial(tutorial_id, version)
        if not selected:
            return None
        now = datetime.now(timezone.utc).isoformat()
        transactions = [{"Update": {
            "TableName": self._table.table_name,
            "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": version_sk(version)}),
            "UpdateExpression": "SET #status = :published, publishedAt = :now",
            "ConditionExpression": "attribute_exists(PK)",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": self._serialize({":published": "published", ":now": now}),
        }}]
        old_published = head.get("publishedVersion")
        if old_published is not None and int(old_published) != version:
            transactions.append({"Update": {
                "TableName": self._table.table_name,
                "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": version_sk(int(old_published))}),
                "UpdateExpression": "SET #status = :disabled",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": self._serialize({":disabled": "disabled"}),
            }})
        if head.get("currentDraftVersion") is not None and int(head["currentDraftVersion"]) == version:
            update_expression = (
                "SET publishedVersion = :version, disabled = :false, revision = :revision "
                "REMOVE currentDraftVersion"
            )
        else:
            update_expression = "SET publishedVersion = :version, disabled = :false, revision = :revision"
        transactions.append({"Update": {
            "TableName": self._table.table_name,
            "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": "HEAD"}),
            "UpdateExpression": update_expression,
            "ConditionExpression": "revision = :expected OR (attribute_not_exists(revision) AND latestVersion = :expected)",
            "ExpressionAttributeValues": self._serialize({
                ":version": version,
                ":false": False,
                ":revision": new_revision,
                ":expected": expected_revision,
            }),
        }})
        self._transact_with_conflict(transactions, tutorial_id)
        selected.status = "published"
        selected.published_at = now
        selected.revision = new_revision
        return selected

    def disable_tutorial(self, tutorial_id: str, expected_revision: int) -> Optional[TutorialVersion]:
        head = self._get_head(tutorial_id)
        if not head:
            return None
        current_revision = int(head.get("revision", head["latestVersion"]))
        if current_revision != expected_revision:
            raise TutorialConflictError(current_revision)
        new_revision = current_revision + 1
        published = head.get("publishedVersion")
        if published is None:
            return None
        published = int(published)
        selected = self.get_tutorial(tutorial_id, published)
        if not selected:
            return None
        transactions = [
            {"Update": {
                "TableName": self._table.table_name,
                "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": version_sk(published)}),
                "UpdateExpression": "SET #status = :disabled",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": self._serialize({":disabled": "disabled"}),
            }},
            {"Update": {
                "TableName": self._table.table_name,
                "Key": self._serialize({"PK": tutorial_pk(tutorial_id), "SK": "HEAD"}),
                "UpdateExpression": "SET disabled = :true, revision = :revision REMOVE publishedVersion",
                "ConditionExpression": (
                    "(revision = :expected OR "
                    "(attribute_not_exists(revision) AND latestVersion = :expected)) "
                    "AND publishedVersion = :published"
                ),
                "ExpressionAttributeValues": self._serialize({
                    ":true": True,
                    ":revision": new_revision,
                    ":expected": expected_revision,
                    ":published": published,
                }),
            }},
        ]
        self._transact_with_conflict(transactions, tutorial_id)
        selected.status = "disabled"
        selected.revision = new_revision
        return selected

    def _get_head(self, tutorial_id: str) -> Optional[Dict[str, Any]]:
        response = self._table.get_item(
            Key={"PK": tutorial_pk(tutorial_id), "SK": "HEAD"}, ConsistentRead=True
        )
        return response.get("Item")

    def _transact_with_conflict(self, transactions: List[Dict[str, Any]], tutorial_id: str) -> None:
        try:
            self._client.transact_write_items(TransactItems=transactions)
        except ClientError as error:
            if self._is_conditional(error):
                head = self._get_head(tutorial_id)
                current_revision = int(head.get("revision", head["latestVersion"])) if head else 0
                raise TutorialConflictError(current_revision) from error
            raise

    @staticmethod
    def _is_conditional(error: ClientError) -> bool:
        code = error.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return True
        if code != "TransactionCanceledException":
            return False
        return any(
            reason.get("Code") == "ConditionalCheckFailed"
            for reason in error.response.get("CancellationReasons", [])
        )

    def _build_version_item(
        self, tutorial_id: str, version: int, status: str, config: Dict[str, Any],
        created_at: str, created_by: Optional[str], published_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = {
            "PK": tutorial_pk(tutorial_id), "SK": version_sk(version),
            "tutorialId": tutorial_id, "version": version, "status": status,
            "configJson": serialize_tutorial_config(config), "createdAt": created_at,
            "createdBy": created_by, "publishedAt": published_at,
        }
        return {key: value for key, value in item.items() if value is not None and value != ""}

    def _item_to_version(self, item: Dict[str, Any]) -> TutorialVersion:
        return TutorialVersion(
            id=item["tutorialId"], version=int(item["version"]), status=item["status"],
            config=json.loads(item["configJson"]), created_at=item["createdAt"],
            created_by=item.get("createdBy"), published_at=item.get("publishedAt"),
        )

    def _serialize(self, values: Dict[str, Any]) -> Dict[str, Any]:
        return {key: self._serializer.serialize(value) for key, value in values.items() if value is not None}

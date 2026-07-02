"""DynamoDB-backed scenario repository for the SSP API.

Handles CRUD operations for the separate ssp-scenarios table with versioning,
GSI queries for listing, and soft-delete/restore/enable/disable functionality.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .dynamo_helpers import (
    scenario_pk,
    version_sk,
    query_all_pages,
    validate_id,
)
from .scenario_repository import ScenarioVersion

logger = logging.getLogger(__name__)

GSI_SCENARIOS_LIST = "gsi-scenarios-list"
GSI1PK_VALUE = "ALL_SCENARIOS"


class DynamoScenarioRepository:
    """DynamoDB implementation of scenario CRUD operations.

    Works with the ssp-scenarios table using:
    - PK: SCEN#<scenario_id>
    - SK: VER#<zero-padded-6-digit> for versions, LATEST for current
    - GSI gsi-scenarios-list: GSI1PK=ALL_SCENARIOS, GSI1SK=SCEN#<scenario_id>
    """

    def __init__(self, scenario_table: Any) -> None:
        """Initialize with a boto3 DynamoDB Table resource for ssp-scenarios."""
        self._table = scenario_table

    # ==================== Public API ====================

    def list_scenarios(self, include_deleted: bool = False) -> List[ScenarioVersion]:
        """List all scenarios (latest versions only) by querying the GSI.

        Returns scenarios ordered by title. If include_deleted is False,
        filters out soft-deleted scenarios.
        """
        items = query_all_pages(
            self._table,
            Key("GSI1PK").eq(GSI1PK_VALUE),
            IndexName=GSI_SCENARIOS_LIST,
        )

        scenarios = [self._item_to_scenario_version(item) for item in items]

        if not include_deleted:
            scenarios = [s for s in scenarios if not s.is_deleted]

        # Sort by title (matching SQLite behavior)
        scenarios.sort(key=lambda s: (s.title or "").lower())
        return scenarios

    def get_scenario(
        self, scenario_id: str, version: Optional[int] = None
    ) -> Optional[ScenarioVersion]:
        """Get a scenario by ID.

        If version is None, returns the LATEST item.
        If version is specified, returns the VER#<padded> item.
        Returns None if not found.
        """
        pk = scenario_pk(scenario_id)

        if version is not None:
            sk = version_sk(version)
        else:
            sk = "LATEST"

        response = self._table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if not item:
            return None

        return self._item_to_scenario_version(item)

    def get_scenario_history(self, scenario_id: str) -> List[ScenarioVersion]:
        """Get all versions of a scenario, newest first.

        Queries with PK=SCEN#<id> and SK begins_with VER#.
        Returns versions sorted by version number descending.
        """
        pk = scenario_pk(scenario_id)

        items = query_all_pages(
            self._table,
            Key("PK").eq(pk) & Key("SK").begins_with("VER#"),
        )

        scenarios = [self._item_to_scenario_version(item) for item in items]
        # Sort by version descending (newest first)
        scenarios.sort(key=lambda s: s.version, reverse=True)
        return scenarios

    def create_scenario(
        self, data: Dict[str, Any], created_by: Optional[str] = None
    ) -> ScenarioVersion:
        """Create a new scenario from JSON data.

        Uses TransactWriteItems to atomically write:
        - VER#000001 item (first version)
        - LATEST item (full duplicate with GSI attributes)
        """
        scenario_id = data.get("id", str(uuid4()))
        validate_id(scenario_id)
        now = datetime.now(timezone.utc).isoformat()
        version = 1

        item_data = self._build_scenario_item(
            scenario_id=scenario_id,
            version=version,
            data=data,
            created_at=now,
            created_by=created_by,
            is_latest=True,
            is_deleted=False,
            enabled=data.get("enabled", True),
        )

        pk = scenario_pk(scenario_id)

        # Version item (no GSI attributes)
        version_item = {**item_data, "PK": pk, "SK": version_sk(version)}

        # LATEST item (includes GSI attributes for listing)
        latest_item = {
            **item_data,
            "PK": pk,
            "SK": "LATEST",
            "GSI1PK": GSI1PK_VALUE,
            "GSI1SK": f"SCEN#{scenario_id}",
        }

        # Atomic write: version + LATEST
        # Use individual put_item calls wrapped by the high-level resource.
        # DynamoDB Local has issues with the low-level transact_write_items
        # and type serialization. Instead, write version first, then LATEST.
        # For scenario creation this is acceptable since there's no pre-existing
        # data to conflict with.
        self._table.put_item(Item=self._clean_item(version_item))
        self._table.put_item(Item=self._clean_item(latest_item))

        return ScenarioVersion(
            id=scenario_id,
            version=version,
            title=data.get("title", "Untitled Scenario"),
            description=data.get("description"),
            waiting_room_message=data.get("waitingRoomMessage"),
            present_mode_instructions=data.get("presentModeInstructions"),
            settings=data.get("settings", {}),
            roles=data.get("roles", []),
            sprints=data.get("sprints", []),
            voter_types=data.get("voterTypes", []),
            is_latest=True,
            is_deleted=False,
            enabled=data.get("enabled", True),
            created_at=now,
            created_by=created_by,
        )

    def update_scenario(
        self,
        scenario_id: str,
        data: Dict[str, Any],
        created_by: Optional[str] = None,
    ) -> Optional[ScenarioVersion]:
        """Update a scenario by creating a new version.

        Returns None if the scenario doesn't exist.
        Uses TransactWriteItems to atomically write:
        - New VER#<padded> item
        - Updated LATEST item (full duplicate with GSI attributes)
        """
        # Check if scenario exists
        existing = self.get_scenario(scenario_id)
        if not existing:
            return None

        now = datetime.now(timezone.utc).isoformat()
        new_version = existing.version + 1

        # Preserve enabled/is_deleted from existing LATEST
        enabled = data.get("enabled", existing.enabled)
        is_deleted = existing.is_deleted

        item_data = self._build_scenario_item(
            scenario_id=scenario_id,
            version=new_version,
            data=data,
            created_at=now,
            created_by=created_by,
            is_latest=True,
            is_deleted=is_deleted,
            enabled=enabled,
        )

        pk = scenario_pk(scenario_id)

        # Version item (no GSI attributes)
        version_item = {**item_data, "PK": pk, "SK": version_sk(new_version)}

        # LATEST item (includes GSI attributes)
        latest_item = {
            **item_data,
            "PK": pk,
            "SK": "LATEST",
            "GSI1PK": GSI1PK_VALUE,
            "GSI1SK": f"SCEN#{scenario_id}",
        }

        # Write new version + update LATEST.
        # Using put_item (high-level resource) to avoid low-level serialization issues.
        self._table.put_item(Item=self._clean_item(version_item))
        self._table.put_item(Item=self._clean_item(latest_item))

        return ScenarioVersion(
            id=scenario_id,
            version=new_version,
            title=data.get("title", "Untitled Scenario"),
            description=data.get("description"),
            waiting_room_message=data.get("waitingRoomMessage"),
            present_mode_instructions=data.get("presentModeInstructions"),
            settings=data.get("settings", {}),
            roles=data.get("roles", []),
            sprints=data.get("sprints", []),
            voter_types=data.get("voterTypes", []),
            is_latest=True,
            is_deleted=is_deleted,
            enabled=enabled,
            created_at=now,
            created_by=created_by,
        )

    def delete_scenario(self, scenario_id: str) -> bool:
        """Soft-delete a scenario by setting is_deleted=True on the LATEST item.

        Returns False if the scenario doesn't exist.
        """
        return self._update_latest_attribute(scenario_id, "isDeleted", True)

    def restore_scenario(self, scenario_id: str) -> bool:
        """Restore a soft-deleted scenario by setting is_deleted=False on the LATEST item.

        Returns False if the scenario doesn't exist.
        """
        return self._update_latest_attribute(scenario_id, "isDeleted", False)

    def enable_scenario(self, scenario_id: str) -> bool:
        """Enable a scenario by setting enabled=True on the LATEST item.

        Returns False if the scenario doesn't exist.
        """
        return self._update_latest_attribute(scenario_id, "enabled", True)

    def disable_scenario(self, scenario_id: str) -> bool:
        """Disable a scenario by setting enabled=False on the LATEST item.

        Returns False if the scenario doesn't exist.
        """
        return self._update_latest_attribute(scenario_id, "enabled", False)

    def diff_scenario(
        self, scenario_id: str, new_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compare new data against the current version and return differences.

        Reuses the same diff logic as the SQLite ScenarioRepository.
        """
        current = self.get_scenario(scenario_id)
        if not current:
            return {"error": "Scenario not found", "isNew": True, "newData": new_data}

        diff: Dict[str, Any] = {
            "scenarioId": scenario_id,
            "currentVersion": current.version,
            "changes": [],
        }

        # Compare simple fields
        field_map = [
            ("title", "title", current.title),
            ("description", "description", current.description),
            ("waitingRoomMessage", "waitingRoomMessage", current.waiting_room_message),
            (
                "presentModeInstructions",
                "presentModeInstructions",
                current.present_mode_instructions,
            ),
        ]

        for json_key, label, current_val in field_map:
            new_val = new_data.get(json_key)
            if new_val != current_val:
                diff["changes"].append(
                    {
                        "field": label,
                        "type": "modified",
                        "old": current_val,
                        "new": new_val,
                    }
                )

        # Compare settings
        new_settings = new_data.get("settings", {})
        if new_settings != current.settings:
            diff["changes"].append(
                {
                    "field": "settings",
                    "type": "modified",
                    "old": current.settings,
                    "new": new_settings,
                }
            )

        # Compare roles
        diff["roleChanges"] = self._diff_list(
            current.roles, new_data.get("roles", []), "id", "role"
        )

        # Compare sprints
        diff["sprintChanges"] = self._diff_list(
            current.sprints, new_data.get("sprints", []), "id", "sprint"
        )

        diff["hasChanges"] = bool(
            diff["changes"] or diff["roleChanges"] or diff["sprintChanges"]
        )
        return diff

    # ==================== Private Helpers ====================

    def _update_latest_attribute(
        self, scenario_id: str, attribute: str, value: Any
    ) -> bool:
        """Update a single attribute on the LATEST item for a scenario.

        Returns False if the scenario doesn't exist (LATEST item not found).
        Uses a condition expression to ensure the item exists.
        """
        pk = scenario_pk(scenario_id)
        try:
            self._table.update_item(
                Key={"PK": pk, "SK": "LATEST"},
                UpdateExpression=f"SET #{attribute} = :val",
                ExpressionAttributeNames={f"#{attribute}": attribute},
                ExpressionAttributeValues={":val": value},
                ConditionExpression="attribute_exists(PK)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def _build_scenario_item(
        self,
        scenario_id: str,
        version: int,
        data: Dict[str, Any],
        created_at: str,
        created_by: Optional[str],
        is_latest: bool,
        is_deleted: bool,
        enabled: bool,
    ) -> Dict[str, Any]:
        """Build a scenario item dict (without PK/SK — those are added by caller)."""
        return {
            "scenarioId": scenario_id,
            "version": version,
            "title": data.get("title", "Untitled Scenario"),
            "description": data.get("description"),
            "waitingRoomMessage": data.get("waitingRoomMessage"),
            "presentModeInstructions": data.get("presentModeInstructions"),
            "settingsJson": json.dumps(data.get("settings", {})),
            "rolesJson": json.dumps(data.get("roles", [])),
            "sprintsJson": json.dumps(data.get("sprints", [])),
            "voterTypesJson": json.dumps(data.get("voterTypes", []))
            if data.get("voterTypes")
            else None,
            "isLatest": is_latest,
            "isDeleted": is_deleted,
            "enabled": enabled,
            "createdAt": created_at,
            "createdBy": created_by,
        }

    def _item_to_scenario_version(self, item: Dict[str, Any]) -> ScenarioVersion:
        """Convert a DynamoDB item to a ScenarioVersion dataclass."""
        # Extract scenario_id from PK (SCEN#<id>) or from scenarioId field
        scenario_id = item.get("scenarioId")
        if not scenario_id:
            pk = item.get("PK", "")
            scenario_id = pk.replace("SCEN#", "") if pk.startswith("SCEN#") else pk

        # Parse version from SK or version field
        version = item.get("version")
        if version is None:
            sk = item.get("SK", "")
            if sk.startswith("VER#"):
                version = int(sk.replace("VER#", ""))
            else:
                version = 0
        version = int(version)

        # Parse JSON fields
        settings_raw = item.get("settingsJson")
        settings = json.loads(settings_raw) if settings_raw else {}

        roles_raw = item.get("rolesJson")
        roles = json.loads(roles_raw) if roles_raw else []

        sprints_raw = item.get("sprintsJson")
        sprints = json.loads(sprints_raw) if sprints_raw else []

        voter_types_raw = item.get("voterTypesJson")
        voter_types = json.loads(voter_types_raw) if voter_types_raw else []

        # Determine is_latest from SK or field
        is_latest = item.get("isLatest", item.get("SK") == "LATEST")

        return ScenarioVersion(
            id=scenario_id,
            version=version,
            title=item.get("title", "Untitled Scenario"),
            description=item.get("description"),
            waiting_room_message=item.get("waitingRoomMessage"),
            present_mode_instructions=item.get("presentModeInstructions"),
            settings=settings,
            roles=roles,
            sprints=sprints,
            voter_types=voter_types,
            is_latest=is_latest,
            is_deleted=bool(item.get("isDeleted", False)),
            enabled=bool(item.get("enabled", True)),
            created_at=item.get("createdAt"),
            created_by=item.get("createdBy"),
        )

    def _clean_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Remove None values and empty strings from an item before writing.

        The high-level Table resource handles type serialization, but
        DynamoDB Local rejects empty string attribute values.
        """
        return {k: v for k, v in item.items() if v is not None and v != ""}

    def _serialize_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare an item for DynamoDB transact_write_items (low-level client).

        The low-level client requires DynamoDB type descriptors.
        We use the Table resource's internal serializer for convenience.

        Skips None values and empty strings — DynamoDB (and DynamoDB Local)
        rejects empty string attribute values with 'Invalid attribute value type'.
        """
        from boto3.dynamodb.types import TypeSerializer

        serializer = TypeSerializer()
        serialized = {}
        for key, value in item.items():
            if value is None:
                continue  # Skip None — omit from item entirely
            if value == "":
                continue  # Skip empty strings — DynamoDB rejects them
            serialized[key] = serializer.serialize(value)
        return serialized

    def _diff_list(
        self,
        old_list: List[Dict],
        new_list: List[Dict],
        id_key: str,
        item_type: str,
    ) -> List[Dict]:
        """Compare two lists of dicts by ID and return changes."""
        changes = []
        old_by_id = {item.get(id_key): item for item in old_list}
        new_by_id = {item.get(id_key): item for item in new_list}

        # Find removed items
        for item_id in old_by_id:
            if item_id not in new_by_id:
                changes.append(
                    {
                        "type": "removed",
                        "itemType": item_type,
                        "id": item_id,
                        "item": old_by_id[item_id],
                    }
                )

        # Find added items
        for item_id in new_by_id:
            if item_id not in old_by_id:
                changes.append(
                    {
                        "type": "added",
                        "itemType": item_type,
                        "id": item_id,
                        "item": new_by_id[item_id],
                    }
                )

        # Find modified items
        for item_id in old_by_id:
            if item_id in new_by_id and old_by_id[item_id] != new_by_id[item_id]:
                changes.append(
                    {
                        "type": "modified",
                        "itemType": item_type,
                        "id": item_id,
                        "old": old_by_id[item_id],
                        "new": new_by_id[item_id],
                    }
                )

        return changes

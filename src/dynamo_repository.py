"""DynamoDB-backed repository implementation for the SSP API.

Connects to either AWS DynamoDB (production) or DynamoDB Local (development/testing)
based on the DYNAMODB_ENDPOINT environment variable. Implements RepositoryProtocol
with single-table design for sessions and a separate table for scenarios.
"""

import json
import os
import logging
import random
import string
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .models import (
    Session,
    Participant,
    ResponseItem,
    ChatMessage,
    VoterInfo,
    VoterType,
    RolePin,
    Sprint,
    SprintQuestion,
    HeadToHead,
)
from .scenario_repository import ScenarioVersion
from .dynamo_scenario_repository import DynamoScenarioRepository
from .dynamo_helpers import (
    session_pk,
    meta_sk,
    sprint_sk,
    participant_sk,
    response_sk,
    vote_sk,
    sq_vote_sk,
    h2h_vote_sk,
    score_sk,
    score_lock_sk,
    chat_sk,
    role_pin_sk,
    scenario_pk,
    batch_write_items,
    query_all_pages,
    sprint_to_item,
    participant_to_item,
    response_to_item,
    chat_message_to_item,
    role_pin_to_item,
    score_to_item,
    item_to_sprint,
    item_to_participant,
    item_to_response,
    item_to_chat_message,
    item_to_voter_info,
    item_to_score,
    item_to_role_pin,
)

logger = logging.getLogger(__name__)

SESSION_TABLE_NAME = "ssp-sessions"
SCENARIO_TABLE_NAME = "ssp-scenarios"


class DynamoRepository:
    """DynamoDB implementation of RepositoryProtocol.

    Constructor behavior:
    - Reads DYNAMODB_ENDPOINT env var. If set, connects to that endpoint
      (DynamoDB Local) with dummy credentials.
    - If unset, uses the default boto3 credential chain (IAM role on Lambda).
    - On local mode (endpoint set), auto-creates tables if they don't exist.
    """

    def __init__(self) -> None:
        endpoint_url = os.environ.get("DYNAMODB_ENDPOINT")

        if endpoint_url:
            # Local mode: connect to DynamoDB Local with dummy credentials
            self._resource = boto3.resource(
                "dynamodb",
                endpoint_url=endpoint_url,
                region_name="us-east-1",
                aws_access_key_id="fakeKey",
                aws_secret_access_key="fakeSecret",
            )
            self._local_mode = True
            logger.info(
                "DynamoRepository connecting to DynamoDB Local at %s",
                endpoint_url,
            )
        else:
            # Production mode: use default boto3 credential chain
            self._resource = boto3.resource("dynamodb")
            self._local_mode = False
            logger.info(
                "DynamoRepository connecting to AWS DynamoDB (default credentials)"
            )

        # Auto-create tables in local mode
        if self._local_mode:
            self._ensure_tables()

        # Initialize Table resources
        self._session_table = self._resource.Table(SESSION_TABLE_NAME)
        self._scenario_table = self._resource.Table(SCENARIO_TABLE_NAME)

        # Initialize scenario sub-repository
        self._scenario_repo = DynamoScenarioRepository(self._scenario_table)

    def _ensure_tables(self) -> None:
        """Create DynamoDB tables for local development if they don't already exist.

        Creates:
        - ssp-sessions: PK (String), SK (String), TTL on expiresAt,
          GSI gsi-status (gsiStatusPK, gsiStatusSK)
        - ssp-scenarios: PK (String), SK (String),
          GSI gsi-scenarios-list (GSI1PK, GSI1SK)
        """
        existing_tables = self._resource.meta.client.list_tables()["TableNames"]

        # --- Session table ---
        if SESSION_TABLE_NAME not in existing_tables:
            logger.info("Creating table %s", SESSION_TABLE_NAME)
            self._resource.create_table(
                TableName=SESSION_TABLE_NAME,
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                    {"AttributeName": "gsiStatusPK", "AttributeType": "S"},
                    {"AttributeName": "gsiStatusSK", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "gsi-status",
                        "KeySchema": [
                            {"AttributeName": "gsiStatusPK", "KeyType": "HASH"},
                            {"AttributeName": "gsiStatusSK", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    },
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            # Wait for table to become active
            self._resource.meta.client.get_waiter("table_exists").wait(
                TableName=SESSION_TABLE_NAME
            )
            # Enable TTL on expiresAt
            self._resource.meta.client.update_time_to_live(
                TableName=SESSION_TABLE_NAME,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "expiresAt",
                },
            )
            logger.info("Table %s created with TTL and GSI", SESSION_TABLE_NAME)
        else:
            logger.info("Table %s already exists, skipping creation", SESSION_TABLE_NAME)

        # --- Scenario table ---
        if SCENARIO_TABLE_NAME not in existing_tables:
            logger.info("Creating table %s", SCENARIO_TABLE_NAME)
            self._resource.create_table(
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
            # Wait for table to become active
            self._resource.meta.client.get_waiter("table_exists").wait(
                TableName=SCENARIO_TABLE_NAME
            )
            logger.info("Table %s created with GSI", SCENARIO_TABLE_NAME)
        else:
            logger.info(
                "Table %s already exists, skipping creation", SCENARIO_TABLE_NAME
            )

    # ==================== Private Helpers ====================

    def _build_scores_from_items(self, score_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build scores list grouped by participant with totalPoints and breakdown.

        Matches the output format of SqliteRepository.get_scores():
        [
            {
                'participantId': ...,
                'participantName': ...,
                'assignedRoleId': ...,
                'assignedRoleName': ...,
                'totalPoints': ...,
                'breakdown': [{'sprintId': ..., 'points': ..., 'reason': ...}, ...]
            },
            ...
        ]
        """
        if not score_items:
            return []

        # Group scores by participant_id
        by_participant: Dict[str, Dict[str, Any]] = {}
        for item in score_items:
            pid = item["participantId"]
            if pid not in by_participant:
                by_participant[pid] = {
                    "participantId": pid,
                    "participantName": item["participantName"],
                    "assignedRoleId": item["assignedRoleId"],
                    "assignedRoleName": item["assignedRoleName"],
                    "totalPoints": 0,
                    "breakdown": [],
                }
            points = int(item["points"])
            by_participant[pid]["totalPoints"] += points
            by_participant[pid]["breakdown"].append({
                "sprintId": item["sprintId"],
                "points": points,
                "reason": item["reason"],
            })

        # Sort by totalPoints descending
        result = sorted(by_participant.values(), key=lambda x: x["totalPoints"], reverse=True)
        return result

    def _bump_version(self, session_code: str) -> int:
        """Atomically increment the version attribute on the session META item.

        Uses DynamoDB UpdateItem with SET version = version + :one and
        ReturnValues='UPDATED_NEW' to get the new version in the same request.

        Returns the new version value as a plain int (boto3 returns Decimal).
        """
        from .dynamo_helpers import session_pk, meta_sk

        response = self._session_table.update_item(
            Key={"PK": session_pk(session_code), "SK": meta_sk()},
            UpdateExpression="SET version = version + :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(response["Attributes"]["version"])

    # ==================== Session CRUD ====================

    def get_session(self) -> Session:
        raise NotImplementedError

    def get_session_by_code(
        self, session_code: str, participant_id: Optional[str] = None
    ) -> Optional[Session]:
        """Get a session by its code with full session assembly.

        Queries the entire session partition (PK = SESS#<code>), partitions items
        by SK prefix, filters responses to the current sprint, attaches vote data,
        resolves the requesting participant, and assembles a Session object matching
        SqliteRepository output.
        """
        # Query all items in the session partition, following pagination
        pk = session_pk(session_code)
        all_items = query_all_pages(self._session_table, Key("PK").eq(pk))

        if not all_items:
            return None

        # Partition items by SK prefix
        meta_item: Optional[Dict[str, Any]] = None
        sprint_items: List[Dict[str, Any]] = []
        participant_items: List[Dict[str, Any]] = []
        response_items: List[Dict[str, Any]] = []
        vote_items: List[Dict[str, Any]] = []
        chat_items: List[Dict[str, Any]] = []
        score_items: List[Dict[str, Any]] = []
        role_pin_items: List[Dict[str, Any]] = []
        sq_vote_items: List[Dict[str, Any]] = []
        h2h_vote_items: List[Dict[str, Any]] = []

        for item in all_items:
            sk = item.get("SK", "")
            if sk == "META":
                meta_item = item
            elif sk.startswith("SPRINT#"):
                sprint_items.append(item)
            elif sk.startswith("PARTICIPANT#"):
                participant_items.append(item)
            elif sk.startswith("RESPONSE#"):
                response_items.append(item)
            elif sk.startswith("VOTE#"):
                vote_items.append(item)
            elif sk.startswith("CHAT#"):
                chat_items.append(item)
            elif sk.startswith("SCORE#") and not sk.startswith("SCORE_LOCK#"):
                score_items.append(item)
            elif sk.startswith("ROLE_PIN#"):
                role_pin_items.append(item)
            elif sk.startswith("SQ_VOTE#"):
                sq_vote_items.append(item)
            elif sk.startswith("H2H_VOTE#"):
                h2h_vote_items.append(item)

        # If no META item found, session doesn't really exist
        if meta_item is None:
            return None

        # --- Build sprints ---
        sprints = [item_to_sprint(item) for item in sprint_items]
        # Sort sprints by their id to maintain consistent order
        sprints.sort(key=lambda s: s.id)

        # Determine current sprint
        current_sprint_index = int(meta_item.get("currentSprintIndex", 0))
        if sprints and current_sprint_index < len(sprints):
            current_sprint = sprints[current_sprint_index]
        else:
            current_sprint = Sprint(
                id="s1", name="Sprint 1", description="Default sprint",
                timer_enabled=True, timer_seconds=480
            )

        # --- Build participants ---
        participants = [item_to_participant(item) for item in participant_items]

        # --- Resolve requesting participant ---
        participant: Optional[Participant] = None
        if participant_id:
            participant = next(
                (p for p in participants if p.id == participant_id), None
            )
        if not participant:
            participant = next(
                (p for p in participants if p.role == "facilitator"), None
            )
        if not participant and participants:
            participant = participants[0]
        if not participant:
            participant = Participant(id="unknown", name="Unknown", role="viewer")

        # --- Filter responses to current sprint only ---
        current_sprint_prefix = f"RESPONSE#{current_sprint.id}#"
        current_sprint_responses = [
            item for item in response_items
            if item.get("SK", "").startswith(current_sprint_prefix)
        ]
        responses = [item_to_response(item) for item in current_sprint_responses]
        # Sort responses by created_at descending (newest first, matching SQLite behavior)
        responses.sort(key=lambda r: r.created_at, reverse=True)

        # --- Attach vote data to responses (current sprint only) ---
        if responses:
            # Collect response IDs for the current sprint
            response_id_set = {r.id for r in responses}

            # Filter votes to only those for current-sprint responses
            current_sprint_votes = [
                v for v in vote_items
                if v.get("responseId") in response_id_set
            ]

            # Build vote counts and voter info per response
            vote_counts: Dict[str, int] = {}
            voters_by_response: Dict[str, List[VoterInfo]] = {}
            user_voted_responses: set = set()

            for v in current_sprint_votes:
                response_id = v["responseId"]
                vote_counts[response_id] = vote_counts.get(response_id, 0) + 1

                # Build voter info
                if response_id not in voters_by_response:
                    voters_by_response[response_id] = []
                voters_by_response[response_id].append(item_to_voter_info(v))

                # Check if current user voted on this response
                if participant_id and v.get("participantId") == participant_id:
                    user_voted_responses.add(response_id)

            # Determine whether to show voter names
            voting_show_voters = bool(meta_item.get("votingShowVoters", False))
            show_voter_names = (participant.role == "facilitator") or voting_show_voters

            # Attach vote data to each response
            for r in responses:
                r.vote_count = vote_counts.get(r.id, 0)
                r.current_user_voted = r.id in user_voted_responses

                voters = voters_by_response.get(r.id, [])
                if voters and not show_voter_names:
                    # Anonymize voter names
                    r.voters = [
                        VoterInfo(
                            participant_id=v.participant_id,
                            participant_name="",
                            participant_role=v.participant_role,
                            created_at=v.created_at,
                            vote_id=v.vote_id,
                            voter_type_id=v.voter_type_id,
                            voter_type_name=v.voter_type_name,
                        )
                        for v in voters
                    ]
                else:
                    r.voters = voters if voters else []

        # --- Build chat messages (sorted chronologically) ---
        chat_messages = [item_to_chat_message(item) for item in chat_items]
        chat_messages.sort(key=lambda m: m.created_at)

        # --- Build scores ---
        scores = self._build_scores_from_items(score_items)

        # --- Look up current participant's engineer vote for current sprint ---
        engineer_vote_choice: Optional[int] = None
        if participant_id and current_sprint:
            for sq_item in sq_vote_items:
                if (sq_item.get("participantId") == participant_id
                        and sq_item.get("sprintId") == current_sprint.id):
                    engineer_vote_choice = int(sq_item["choiceIndex"])
                    break

        # --- Build voter types from META ---
        voter_types_raw = meta_item.get("voterTypes", [])
        voter_types: List[VoterType] = []
        if voter_types_raw:
            for vt in voter_types_raw:
                voter_types.append(VoterType(
                    id=vt["id"],
                    name=vt["name"],
                    description=vt.get("description"),
                ))

        # --- Assemble Session ---
        return Session(
            session_id=session_code,
            scenario_name=meta_item.get("scenarioName", "Unknown Scenario"),
            scenario_description=meta_item.get("scenarioDescription"),
            waiting_room_message=meta_item.get("waitingRoomMessage"),
            present_mode_instructions=meta_item.get("presentModeInstructions"),
            current_sprint_index=current_sprint_index,
            total_sprints=int(meta_item.get("totalSprints", len(sprints))),
            timer_visible=bool(meta_item.get("timerVisible", True)),
            timer_seconds=int(meta_item["timerSeconds"]) if meta_item.get("timerSeconds") is not None else None,
            timer_running=bool(meta_item.get("timerRunning", False)),
            facilitator_pin=meta_item.get("facilitatorPin"),
            status=meta_item.get("status", "active"),
            started=bool(meta_item.get("started", False)),
            version=int(meta_item.get("version", 0)),
            created_at=meta_item.get("createdAt"),
            voting_enabled=bool(meta_item.get("votingEnabled", False)),
            voting_show_voters=bool(meta_item.get("votingShowVoters", False)),
            voter_types=voter_types,
            sprint_question_phase=meta_item.get("sprintQuestionPhase", "inactive"),
            scoreboard_visible=bool(meta_item.get("scoreboardVisible", False)),
            engineer_vote_choice=engineer_vote_choice,
            scores=scores,
            role_pins_enabled=bool(meta_item.get("rolePinsEnabled", False)),
            participant=participant,
            current_sprint=current_sprint,
            sprints=sprints,
            responses=responses,
            participants=participants,
            chat_messages=chat_messages,
        )

    def create_session(
        self,
        facilitator_name: str,
        scenario_id: str,
        facilitator_pin: Optional[str] = None,
        ip_address: Optional[str] = None,
        role_pins_enabled: bool = False,
    ) -> Session:
        """Create a new session with a generated code.

        Loads scenario from the scenarios table, generates a unique session code,
        writes META + SPRINT + PARTICIPANT items (and optionally ROLE_PIN items),
        then returns the assembled Session object.
        """
        # Load scenario from the scenarios table (LATEST item)
        scenario_data = self._load_scenario_for_session(scenario_id)
        if scenario_data is None:
            # Fallback: minimal defaults if scenario not found
            sprints: List[Sprint] = [Sprint(id='s1', name='Sprint 1', description='Default sprint', timer_enabled=True, timer_seconds=480)]
            scenario_name = 'Unknown Scenario'
            scenario_description: Optional[str] = None
            waiting_room_message: Optional[str] = None
            present_mode_instructions: Optional[str] = None
            timer_visible = True
            voter_types: List[Dict[str, Any]] = []
            scenario_version_num: Optional[int] = None
            player_roles: List[Dict[str, Any]] = []
        else:
            sprints = self._parse_sprints_from_scenario_data(scenario_data)
            scenario_name = scenario_data.get('title', 'Unknown Scenario')
            scenario_description = scenario_data.get('description')
            waiting_room_message = scenario_data.get('waitingRoomMessage')
            present_mode_instructions = scenario_data.get('presentModeInstructions')
            settings = scenario_data.get('settings', {})
            timer_visible = settings.get('timerEnabled', True)
            voter_types = scenario_data.get('voterTypes', []) or []
            scenario_version_num = scenario_data.get('version')
            roles = scenario_data.get('roles', [])
            player_roles = [r for r in roles if r.get('roleType') == 'player']

        # Generate unique session code (XXXX-NNNN)
        session_code = self._generate_session_code()
        now = datetime.now(timezone.utc).isoformat()

        first_sprint = sprints[0] if sprints else Sprint(id='s1', name='Sprint 1', description='Default', timer_enabled=True, timer_seconds=480)
        voting_enabled = bool(voter_types)

        # Build META item
        pk = session_pk(session_code)
        meta_item: Dict[str, Any] = {
            "PK": pk,
            "SK": meta_sk(),
            "sessionId": session_code,
            "scenarioId": scenario_id,
            "scenarioVersion": scenario_version_num,
            "scenarioName": scenario_name,
            "scenarioDescription": scenario_description,
            "waitingRoomMessage": waiting_room_message,
            "presentModeInstructions": present_mode_instructions,
            "currentSprintIndex": 0,
            "totalSprints": len(sprints),
            "timerVisible": timer_visible,
            "timerSeconds": first_sprint.timer_seconds or 480,
            "timerRunning": False,
            "facilitatorPin": facilitator_pin,
            "status": "active",
            "started": False,
            "version": 1,
            "votingEnabled": voting_enabled,
            "votingShowVoters": False,
            "sprintQuestionPhase": "inactive",
            "scoreboardVisible": False,
            "rolePinsEnabled": role_pins_enabled,
            "voterTypes": voter_types,
            "createdAt": now,
            "gsiStatusPK": "STATUS#active",
            "gsiStatusSK": f"SESS#{session_code}",
            "participantCount": 0,
        }

        # Build SPRINT items
        sprint_items = [sprint_to_item(s, session_code) for s in sprints]

        # Build facilitator PARTICIPANT item
        facilitator_id = str(uuid4())
        facilitator = Participant(
            id=facilitator_id,
            name=facilitator_name,
            role='facilitator',
            assigned_role_name='Facilitator',
            role_type='Facilitator',
            role_description='Controls sprint progression and settings.',
            private_info=None,
            ip_address=ip_address,
            joined_at=now,
            voter_type_id=None,
            voter_type_name=None,
        )
        facilitator_item = participant_to_item(facilitator, session_code)

        # Collect all items to write
        all_items: List[Dict[str, Any]] = [meta_item] + sprint_items + [facilitator_item]

        # Generate ROLE_PIN items if enabled
        if role_pins_enabled and player_roles:
            existing_pins: set = set()
            for role in player_roles:
                pin = self._generate_unique_pin(existing_pins)
                existing_pins.add(pin)
                role_pin = RolePin(
                    session_id=session_code,
                    role_id=role.get('id', ''),
                    role_name=role.get('name', ''),
                    pin=pin,
                )
                all_items.append(role_pin_to_item(role_pin, session_code))

        # Write all items in batch
        batch_write_items(self._session_table, all_items)

        # Assemble voter types as VoterType dataclass list
        voter_type_objects = [
            VoterType(id=vt['id'], name=vt['name'], description=vt.get('description'))
            for vt in voter_types
        ]

        # Return assembled Session object
        return Session(
            session_id=session_code,
            scenario_name=scenario_name,
            current_sprint_index=0,
            total_sprints=len(sprints),
            timer_visible=timer_visible,
            timer_seconds=first_sprint.timer_seconds or 480,
            participant=facilitator,
            current_sprint=first_sprint,
            responses=[],
            participants=[facilitator],
            chat_messages=[],
            sprints=sprints,
            facilitator_pin=facilitator_pin,
            status='active',
            started=False,
            scenario_description=scenario_description,
            waiting_room_message=waiting_room_message,
            present_mode_instructions=present_mode_instructions,
            created_at=now,
            timer_running=False,
            version=1,
            voting_enabled=voting_enabled,
            voting_show_voters=False,
            voter_types=voter_type_objects,
            sprint_question_phase='inactive',
            scoreboard_visible=False,
            engineer_vote_choice=None,
            scores=[],
            role_pins_enabled=role_pins_enabled,
        )

    def _generate_session_code(self) -> str:
        """Generate a unique session code in XXXX-NNNN format.

        Checks the session table to ensure no collision exists.
        """
        while True:
            letters = ''.join(random.choices(string.ascii_uppercase, k=4))
            digits = ''.join(random.choices(string.digits, k=4))
            code = f'{letters}-{digits}'
            # Check if session already exists
            response = self._session_table.get_item(
                Key={'PK': session_pk(code), 'SK': meta_sk()},
                ProjectionExpression='PK',
            )
            if 'Item' not in response:
                return code

    def _generate_unique_pin(self, existing_pins: set) -> str:
        """Generate a 4-digit zero-padded PIN not in the existing set."""
        while True:
            pin = f"{random.randint(0, 9999):04d}"
            if pin not in existing_pins:
                return pin

    def _load_scenario_for_session(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        """Load the LATEST scenario from the scenarios table via DynamoScenarioRepository.

        Returns the scenario data as a dict, or None if not found.
        """
        scenario = self._scenario_repo.get_scenario(scenario_id)
        if not scenario:
            return None
        # Convert ScenarioVersion to dict for use by _parse_sprints_from_scenario_data
        return {
            'id': scenario.id,
            'version': scenario.version,
            'title': scenario.title,
            'description': scenario.description,
            'waitingRoomMessage': scenario.waiting_room_message,
            'presentModeInstructions': scenario.present_mode_instructions,
            'settings': scenario.settings,
            'roles': scenario.roles,
            'sprints': scenario.sprints,
            'voterTypes': scenario.voter_types,
        }

    def _parse_sprints_from_scenario_data(self, scenario_data: Dict[str, Any]) -> List[Sprint]:
        """Parse sprint data from a scenario item into Sprint dataclass objects."""
        sprints_raw = scenario_data.get('sprints', [])
        if not sprints_raw:
            return []

        # If sprints are stored as a JSON string, parse them
        if isinstance(sprints_raw, str):
            sprints_raw = json.loads(sprints_raw)

        sprints: List[Sprint] = []
        for sprint_data in sprints_raw:
            # Parse sprintQuestion
            sq_data = sprint_data.get('sprintQuestion')
            sprint_question: Optional[SprintQuestion] = None
            if sq_data:
                if isinstance(sq_data, str):
                    sq_data = json.loads(sq_data)
                sprint_question = SprintQuestion(
                    prompt=sq_data['prompt'],
                    choices=sq_data['choices'],
                )

            # Parse headToHead
            h2h_data = sprint_data.get('headToHead')
            head_to_head: Optional[HeadToHead] = None
            if h2h_data:
                if isinstance(h2h_data, str):
                    h2h_data = json.loads(h2h_data)
                head_to_head = HeadToHead(
                    prompt=h2h_data['prompt'],
                    role_a=h2h_data.get('roleA', ''),
                    role_b=h2h_data.get('roleB', ''),
                )

            sprints.append(Sprint(
                id=sprint_data['id'],
                name=sprint_data['name'],
                description=sprint_data['description'],
                timer_enabled=sprint_data.get('timerEnabled', False),
                timer_seconds=sprint_data.get('timerSeconds'),
                response_boxes=sprint_data.get('responseBoxes'),
                sprint_question=sprint_question,
                head_to_head=head_to_head,
            ))
        return sprints

    def list_sessions(self) -> list:
        """Return a summary list of all sessions for admin view.

        Queries GSI gsi-status for both STATUS#active and STATUS#ended partitions,
        combines the results, and returns summaries sorted by createdAt descending.
        Returns an empty list when no sessions exist (Req 8.4).
        """
        # Query active sessions from GSI (Req 8.1, 8.2)
        active_items = query_all_pages(
            self._session_table,
            Key("gsiStatusPK").eq("STATUS#active"),
            IndexName="gsi-status",
        )

        # Query ended sessions from GSI
        ended_items = query_all_pages(
            self._session_table,
            Key("gsiStatusPK").eq("STATUS#ended"),
            IndexName="gsi-status",
        )

        # Combine and build summaries
        all_items = active_items + ended_items
        result = []
        for item in all_items:
            result.append({
                "sessionId": item.get("sessionId", ""),
                "scenarioName": item.get("scenarioName", ""),
                "status": item.get("status", ""),
                "participantCount": int(item.get("participantCount", 0)),
                "responseCount": int(item.get("responseCount", 0)),
                "currentSprintIndex": int(item.get("currentSprintIndex", 0)),
                "totalSprints": int(item.get("totalSprints", 0)),
                "createdAt": item.get("createdAt", ""),
            })

        # Sort by createdAt descending (Req 8.2)
        result.sort(key=lambda x: x["createdAt"], reverse=True)
        return result

    def has_facilitator(self, session_code: str) -> bool:
        """Check if a session has a facilitator participant."""
        pk = session_pk(session_code)
        # Query participants and look for one with role == 'facilitator'
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("PARTICIPANT#"),
        )
        return any(item.get("role") == "facilitator" for item in items)

    def check_facilitator_pin(self, session_code: str, pin: str) -> bool:
        """Check if the provided PIN matches the session's facilitator PIN."""
        pk = session_pk(session_code)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="facilitatorPin",
        )
        item = response.get("Item")
        if not item or not item.get("facilitatorPin"):
            return False
        return item["facilitatorPin"] == pin

    def replace_facilitator(
        self, session_code: str, name: str, ip_address: Optional[str] = None
    ) -> Optional[Participant]:
        """Replace the current facilitator with a new one.

        Deletes the existing facilitator participant and adds a new one.
        """
        pk = session_pk(session_code)
        # Find and delete existing facilitator
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("PARTICIPANT#"),
        )
        for item in items:
            if item.get("role") == "facilitator":
                self._session_table.delete_item(
                    Key={"PK": pk, "SK": item["SK"]},
                )
                break

        return self.add_participant(session_code, name, "facilitator", ip_address)

    def end_session(self, session_code: str) -> bool:
        """End a session by updating status and setting TTL on all partition items.

        1. Query all items in the session partition
        2. Update META item: status='ended', gsiStatusPK='STATUS#ended', expiresAt (Req 8.3)
        3. Set expiresAt on all other items via BatchWriteItem in groups of 25 (Req 7.1, 7.3)
        4. Retry failed batches up to 3 times (Req 7.4)
        5. Bump version after successful update
        Returns True if session was ended, False if session doesn't exist.
        """
        pk = session_pk(session_code)

        # Query all items in the session partition
        all_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk),
        )

        if not all_items:
            return False

        # Calculate expiresAt: Unix epoch + 604800 seconds (7 days) (Req 7.1)
        expires_at = int(time.time()) + 604800

        # Update the META item atomically: status, gsiStatusPK, expiresAt (Req 8.3)
        self._session_table.update_item(
            Key={"PK": pk, "SK": meta_sk()},
            UpdateExpression="SET #status = :ended, gsiStatusPK = :gsi_pk, expiresAt = :expires",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":ended": "ended",
                ":gsi_pk": "STATUS#ended",
                ":expires": expires_at,
            },
        )

        # Set expiresAt on all non-META items using BatchWriteItem (Req 7.3)
        non_meta_items = [item for item in all_items if item.get("SK") != "META"]
        if non_meta_items:
            # Add expiresAt to each item and write back via batch (overwrites with full item)
            items_to_write = []
            for item in non_meta_items:
                item["expiresAt"] = expires_at
                items_to_write.append(item)

            # batch_write_items handles groups of 25 and retries (Req 7.3, 7.4)
            batch_write_items(self._session_table, items_to_write)

        # Bump version
        self._bump_version(session_code)
        return True

    def start_session(self, session_code: str) -> bool:
        """Start a session by setting started=True on the META item.

        Updates the META item's started attribute and bumps the version.
        Returns True if session was started, False if session doesn't exist.
        """
        pk = session_pk(session_code)

        try:
            self._session_table.update_item(
                Key={"PK": pk, "SK": meta_sk()},
                UpdateExpression="SET started = :val",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={":val": True},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

        self._bump_version(session_code)
        return True

    def get_session_scenario_info(self, session_code: str) -> Optional[dict]:
        """Return the scenario_id and scenario_version for a session."""
        pk = session_pk(session_code)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="scenarioId, scenarioVersion",
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "scenario_id": item.get("scenarioId"),
            "scenario_version": item.get("scenarioVersion"),
        }

    def get_voter_types_for_session(self, session_code: str) -> List[VoterType]:
        """Return the voter types configured for a session."""
        pk = session_pk(session_code)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="voterTypes",
        )
        item = response.get("Item")
        if not item or not item.get("voterTypes"):
            return []
        voter_types_raw = item["voterTypes"]
        return [
            VoterType(id=vt["id"], name=vt["name"], description=vt.get("description"))
            for vt in voter_types_raw
        ]

    def is_role_pins_enabled(self, session_code: str) -> bool:
        """Check whether role PIN-based assignment is enabled for a session."""
        pk = session_pk(session_code)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="rolePinsEnabled",
        )
        item = response.get("Item")
        if not item:
            return False
        return bool(item.get("rolePinsEnabled", False))

    # ==================== Participant Management ====================

    def add_participant(
        self,
        session_code: str,
        name: str,
        role: str,
        ip_address: Optional[str] = None,
        voter_type_id: Optional[str] = None,
        voter_type_name: Optional[str] = None,
        assigned_role_name: Optional[str] = None,
        role_type: Optional[str] = None,
        role_description: Optional[str] = None,
        private_info: Optional[str] = None,
    ) -> Participant:
        """Add a participant to a session and return the participant object.

        PutItem with PARTICIPANT SK, update META participantCount, bump version.
        """
        from .dynamo_helpers import participant_to_item

        now = datetime.now(timezone.utc).isoformat()
        participant_id = str(uuid4())

        # Use provided role assignment fields if given, otherwise use defaults
        if assigned_role_name is None:
            if role == "player":
                assigned_role_name = None
                role_type = "Player"
                role_description = None
                private_info = None
            else:
                role_names = {"facilitator": "Facilitator", "viewer": "Viewer", "voter": "Voter"}
                role_descriptions = {
                    "facilitator": "Controls sprint progression and settings.",
                    "viewer": "Observes the session without submitting responses.",
                }
                assigned_role_name = role_names.get(role, role)
                role_type = role.capitalize()
                role_description = role_descriptions.get(role, "")
                private_info = None

        participant = Participant(
            id=participant_id,
            name=name,
            role=role,
            assigned_role_name=assigned_role_name,
            role_type=role_type,
            role_description=role_description,
            private_info=private_info,
            ip_address=ip_address,
            joined_at=now,
            voter_type_id=voter_type_id,
            voter_type_name=voter_type_name,
        )

        # Write participant item
        item = participant_to_item(participant, session_code)
        self._session_table.put_item(Item=item)

        # Update META participantCount
        pk = session_pk(session_code)
        self._session_table.update_item(
            Key={"PK": pk, "SK": meta_sk()},
            UpdateExpression="SET participantCount = if_not_exists(participantCount, :zero) + :one",
            ExpressionAttributeValues={":zero": 0, ":one": 1},
        )

        # Bump version
        self._bump_version(session_code)

        return participant

    def get_session_participants(self, session_code: str) -> List[Participant]:
        """Return all participants for a session."""
        pk = session_pk(session_code)
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("PARTICIPANT#"),
        )
        return [item_to_participant(item) for item in items]

    def update_participant_role(
        self,
        participant_id: str,
        assigned_role_name: str,
        role_description: str,
        private_info: Optional[str],
        session_code: str,
    ) -> bool:
        """Update a participant's scenario role assignment."""
        pk = session_pk(session_code)
        sk = participant_sk(participant_id)

        try:
            self._session_table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="SET assignedRoleName = :rn, roleDescription = :rd, privateInfo = :pi",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={
                    ":rn": assigned_role_name,
                    ":rd": role_description,
                    ":pi": private_info,
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

        self._bump_version(session_code)
        return True

    def remove_participant(self, participant_id: str, session_code: str) -> bool:
        """Remove a participant from a session.

        DeleteItem on participant, decrement META participantCount, bump version.
        """
        pk = session_pk(session_code)
        sk = participant_sk(participant_id)

        # Check if item exists before deleting (to return correct bool)
        response = self._session_table.delete_item(
            Key={"PK": pk, "SK": sk},
            ReturnValues="ALL_OLD",
        )

        if "Attributes" not in response:
            return False

        # Decrement META participantCount
        self._session_table.update_item(
            Key={"PK": pk, "SK": meta_sk()},
            UpdateExpression="SET participantCount = if_not_exists(participantCount, :one) - :one",
            ExpressionAttributeValues={":one": 1},
        )

        self._bump_version(session_code)
        return True

    def is_facilitator(self, session_code: str, participant_id: str) -> bool:
        """Check if a participant is the facilitator for a session."""
        pk = session_pk(session_code)
        sk = participant_sk(participant_id)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": sk},
            ProjectionExpression="#r",
            ExpressionAttributeNames={"#r": "role"},
        )
        item = response.get("Item")
        if not item:
            return False
        return item.get("role") == "facilitator"

    def replace_player_for_role(
        self,
        session_code: str,
        role_id: str,
        new_participant_name: str,
        ip_address: Optional[str] = None,
    ) -> Optional[Participant]:
        """Replace the current claimant with a new participant (reconnection).

        Removes old participant, creates new one with same role assignment,
        and updates the role_pins claim fields.
        """
        from .dynamo_helpers import role_pin_sk as _role_pin_sk

        pk = session_pk(session_code)

        # Get the role PIN record
        rp_sk = _role_pin_sk(role_id)
        response = self._session_table.get_item(
            Key={"PK": pk, "SK": rp_sk},
        )
        role_pin_item = response.get("Item")
        if not role_pin_item:
            return None

        old_participant_id = role_pin_item.get("claimedByParticipantId")
        role_name = role_pin_item["roleName"]

        # Get scenario info to retrieve role details
        meta_response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="scenarioId, scenarioVersion",
        )
        meta = meta_response.get("Item")
        role_description: Optional[str] = None
        private_info: Optional[str] = None
        if meta and meta.get("scenarioId"):
            scenario_item = self._load_scenario_for_session(meta["scenarioId"])
            if scenario_item:
                roles = scenario_item.get("roles", [])
                for r in roles:
                    if r.get("id") == role_id:
                        role_description = r.get("description")
                        private_info = r.get("privateInfo")
                        break

        now = datetime.now(timezone.utc).isoformat()
        new_participant_id = str(uuid4())

        # Remove old participant if exists
        if old_participant_id:
            old_sk = participant_sk(old_participant_id)
            self._session_table.delete_item(Key={"PK": pk, "SK": old_sk})

        # Create new participant with role assignment
        new_participant = Participant(
            id=new_participant_id,
            name=new_participant_name,
            role="player",
            assigned_role_name=role_name,
            role_type="player",
            role_description=role_description,
            private_info=private_info,
            ip_address=ip_address,
            joined_at=now,
        )
        from .dynamo_helpers import participant_to_item as _p_to_item
        self._session_table.put_item(Item=_p_to_item(new_participant, session_code))

        # Update role_pins claim fields
        self._session_table.update_item(
            Key={"PK": pk, "SK": rp_sk},
            UpdateExpression="SET claimedByParticipantId = :pid, claimedByName = :pname, claimedAt = :at",
            ExpressionAttributeValues={
                ":pid": new_participant_id,
                ":pname": new_participant_name,
                ":at": now,
            },
        )

        self._bump_version(session_code)

        return new_participant

    # ==================== Responses ====================

    def add_response(
        self,
        participant_name: str,
        text: str,
        response_box: Optional[str] = None,
        participant_role_name: Optional[str] = None,
    ) -> ResponseItem:
        raise NotImplementedError

    def add_response_to_session(
        self,
        session_code: str,
        participant_name: str,
        text: str,
        response_box: Optional[str] = None,
        participant_role_name: Optional[str] = None,
    ) -> Optional[ResponseItem]:
        """Add a response to a specific session in the current sprint."""
        # Get session META to determine the current sprint
        pk = session_pk(session_code)
        meta_response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()}
        )
        meta_item = meta_response.get("Item")
        if not meta_item:
            return None

        # Determine current sprint ID from SPRINT items
        current_sprint_index = int(meta_item.get("currentSprintIndex", 0))
        # Query sprint items to find the sprint at current index
        sprint_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SPRINT#"),
        )
        sprint_items.sort(key=lambda s: s.get("sprintId", ""))
        if sprint_items and current_sprint_index < len(sprint_items):
            sprint_id = sprint_items[current_sprint_index]["sprintId"]
        else:
            sprint_id = "s1"

        now = datetime.now(timezone.utc).isoformat()
        response_id = str(uuid4())

        response_item = ResponseItem(
            id=response_id,
            participant_name=participant_name,
            text=text,
            sprint_id=sprint_id,
            created_at=now,
            response_box=response_box,
            participant_role_name=participant_role_name,
        )

        # Write the response item
        self._session_table.put_item(Item=response_to_item(response_item, session_code))

        # Bump version
        self._bump_version(session_code)

        return response_item

    def delete_response(self, session_code: str, response_id: str) -> bool:
        raise NotImplementedError

    # ==================== Sprint Control ====================

    def next_sprint(self) -> Session:
        raise NotImplementedError

    def next_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Advance to the next sprint for a specific session."""
        pk = session_pk(session_code)
        meta_response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()}
        )
        meta_item = meta_response.get("Item")
        if not meta_item:
            return None

        current_index = int(meta_item.get("currentSprintIndex", 0))
        total_sprints = int(meta_item.get("totalSprints", 1))
        next_index = min(current_index + 1, total_sprints - 1)

        # Query sprint items to get timer_seconds for the next sprint
        sprint_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SPRINT#"),
        )
        sprint_items.sort(key=lambda s: s.get("sprintId", ""))

        timer_seconds = 420
        if sprint_items and next_index < len(sprint_items):
            timer_seconds = sprint_items[next_index].get("timerSeconds") or 420

        # Update META with new sprint index, timer, and stop timer
        self._session_table.update_item(
            Key={"PK": pk, "SK": meta_sk()},
            UpdateExpression="SET currentSprintIndex = :idx, timerSeconds = :ts, timerRunning = :tr",
            ExpressionAttributeValues={
                ":idx": next_index,
                ":ts": timer_seconds,
                ":tr": False,
            },
        )

        self._bump_version(session_code)

        return self.get_session_by_code(session_code)

    def prev_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Go back to the previous sprint for a specific session."""
        pk = session_pk(session_code)
        meta_response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()}
        )
        meta_item = meta_response.get("Item")
        if not meta_item:
            return None

        current_index = int(meta_item.get("currentSprintIndex", 0))
        prev_index = max(current_index - 1, 0)

        # Query sprint items to get timer_seconds for the prev sprint
        sprint_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SPRINT#"),
        )
        sprint_items.sort(key=lambda s: s.get("sprintId", ""))

        timer_seconds = 420
        if sprint_items and prev_index < len(sprint_items):
            timer_seconds = sprint_items[prev_index].get("timerSeconds") or 420

        # Update META with new sprint index, timer, and stop timer
        self._session_table.update_item(
            Key={"PK": pk, "SK": meta_sk()},
            UpdateExpression="SET currentSprintIndex = :idx, timerSeconds = :ts, timerRunning = :tr",
            ExpressionAttributeValues={
                ":idx": prev_index,
                ":ts": timer_seconds,
                ":tr": False,
            },
        )

        self._bump_version(session_code)

        return self.get_session_by_code(session_code)

    def update_timer(
        self, session_code: str, action: str, seconds: Optional[int] = None
    ) -> Optional[Session]:
        raise NotImplementedError

    # ==================== Chat ====================

    def add_chat_message(
        self, session_code: str, sender_name: str, sender_role: str, text: str
    ) -> Optional[ChatMessage]:
        """Add a chat message to a session with ISO-8601 UTC timestamp and millisecond precision."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_dt.microsecond // 1000:03d}Z"
        message_id = str(uuid4())

        msg = ChatMessage(
            id=message_id,
            sender_name=sender_name,
            sender_role=sender_role,
            text=text,
            created_at=now,
        )

        # Write the chat message item
        self._session_table.put_item(Item=chat_message_to_item(msg, session_code))

        # Bump version
        self._bump_version(session_code)

        return msg

    # ==================== Session Log ====================

    def get_session_log(self) -> list:
        raise NotImplementedError

    # ==================== Participant Helpers ====================

    def get_participant_role_name(
        self, session_code: str, participant_id: str
    ) -> Optional[str]:
        """Get the assigned role name for a participant."""
        pk = session_pk(session_code)
        sk = participant_sk(participant_id)
        response = self._session_table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if not item:
            return None
        return item.get("assignedRoleName")

    # ==================== Voting (Response Upvotes) ====================

    def add_vote(
        self,
        session_code: str,
        response_id: str,
        participant_id: str,
        participant_name: str,
        participant_role: str,
        voter_type_id: Optional[str] = None,
        voter_type_name: Optional[str] = None,
    ) -> bool:
        """Add a vote to a response. Returns True if added, False if already exists.

        Uses conditional PutItem with attribute_not_exists(SK) to enforce uniqueness
        via the composite SK VOTE#<response_id>#<participant_id>.
        Catches ConditionalCheckFailedException and returns False for duplicates.
        """
        pk = session_pk(session_code)
        sk = vote_sk(response_id, participant_id)
        now = datetime.now(timezone.utc).isoformat()

        # Determine the current sprint_id from META
        meta_response = self._session_table.get_item(
            Key={"PK": pk, "SK": meta_sk()},
            ProjectionExpression="currentSprintIndex",
        )
        meta_item = meta_response.get("Item")
        if not meta_item:
            return False

        current_sprint_index = int(meta_item.get("currentSprintIndex", 0))
        sprint_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SPRINT#"),
        )
        sprint_items.sort(key=lambda s: s.get("sprintId", ""))
        if sprint_items and current_sprint_index < len(sprint_items):
            sprint_id = sprint_items[current_sprint_index]["sprintId"]
        else:
            sprint_id = "s1"

        item: Dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "responseId": response_id,
            "participantId": participant_id,
            "participantName": participant_name,
            "participantRole": participant_role,
            "sprintId": sprint_id,
            "createdAt": now,
            "voterTypeId": voter_type_id,
            "voterTypeName": voter_type_name,
        }

        try:
            self._session_table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(SK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

        self._bump_version(session_code)
        return True

    def remove_vote(
        self, session_code: str, response_id: str, participant_id: str
    ) -> bool:
        """Remove a participant's vote from a response. Returns True if deleted, False if not found."""
        pk = session_pk(session_code)
        sk = vote_sk(response_id, participant_id)

        response = self._session_table.delete_item(
            Key={"PK": pk, "SK": sk},
            ReturnValues="ALL_OLD",
        )

        if "Attributes" not in response:
            return False

        self._bump_version(session_code)
        return True

    def remove_vote_by_id(self, vote_id: str) -> Optional[Dict[str, Any]]:
        """Remove a specific vote by ID. Returns vote details or None if not found.

        Since DynamoDB doesn't index by vote_id, we need to scan for it.
        This is acceptable because remove_vote_by_id is a facilitator-only
        operation called infrequently.
        """
        # We don't have a vote_id-indexed lookup in DynamoDB.
        # This method is used by the facilitator manage-responses modal.
        # For now, raise NotImplementedError as it's not part of task 7.3 scope.
        raise NotImplementedError

    def get_votes_for_responses(
        self,
        response_ids: List[str],
        participant_id: Optional[str] = None,
        include_voters: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    def set_voting_enabled(self, session_code: str, enabled: bool) -> bool:
        raise NotImplementedError

    def toggle_voting_show_voters(self, session_code: str) -> bool:
        raise NotImplementedError

    def get_vote_summary_by_role(
        self, session_code: str, sprint_id: str
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_vote_summary_by_voter_type(
        self, session_code: str, sprint_id: str
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # ==================== Sprint Question / Scoring ====================

    def submit_engineer_vote(
        self,
        session_code: str,
        sprint_id: str,
        participant_id: str,
        participant_name: str,
        choice_index: int,
        choice_text: str,
        event_name: str = "engineer_vote_submitted",
    ) -> None:
        """Upsert an engineer (player) sprint question vote.

        Uses PutItem with the composite SK SQ_VOTE#<sprint_id>#<participant_id>.
        Matches SqliteRepository behavior: INSERT OR REPLACE semantics (upsert).
        One vote per (sprint_id, participant_id). Bumps version after write.
        """
        pk = session_pk(session_code)
        sk = sq_vote_sk(sprint_id, participant_id)
        now = datetime.now(timezone.utc).isoformat()

        item: Dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "sessionId": session_code,
            "sprintId": sprint_id,
            "participantId": participant_id,
            "participantName": participant_name,
            "choiceIndex": choice_index,
            "choiceText": choice_text,
            "createdAt": now,
        }

        # Plain PutItem (upsert) — overwrites any existing vote for this participant/sprint
        self._session_table.put_item(Item=item)
        self._bump_version(session_code)

    def get_engineer_vote(
        self, session_code: str, sprint_id: str, participant_id: str
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_all_engineer_votes(
        self, session_code: str, sprint_id: str
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def submit_head_to_head_vote(
        self,
        session_code: str,
        sprint_id: str,
        participant_id: str,
        participant_name: str,
        voter_type_id: Optional[str],
        voter_type_name: Optional[str],
        chosen_role_id: str,
    ) -> None:
        """Upsert a head-to-head vote.

        Uses PutItem with the composite SK H2H_VOTE#<sprint_id>#<participant_id>.
        Matches SqliteRepository behavior: INSERT OR REPLACE semantics (upsert).
        One vote per (sprint_id, participant_id). Bumps version after write.
        """
        pk = session_pk(session_code)
        sk = h2h_vote_sk(sprint_id, participant_id)
        now = datetime.now(timezone.utc).isoformat()

        item: Dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "sessionId": session_code,
            "sprintId": sprint_id,
            "participantId": participant_id,
            "participantName": participant_name,
            "voterTypeId": voter_type_id,
            "voterTypeName": voter_type_name,
            "chosenRoleId": chosen_role_id,
            "createdAt": now,
        }

        # Plain PutItem (upsert) — overwrites any existing vote for this participant/sprint
        self._session_table.put_item(Item=item)
        self._bump_version(session_code)

    def get_head_to_head_votes(
        self, session_code: str, sprint_id: str
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def calculate_and_store_scores(
        self, session_code: str, sprint_id: str
    ) -> None:
        """Compute and persist scores for a sprint using 2-of-3 group majority rule.

        Scoring categories:
        1. Engineer corporate-choice: +1 per engineer whose vote matches winning answer
        2. Group points: +1 per stakeholder group whose majority matches winning answer
        3. Individual voter: +1 per voter whose vote matches winning answer
        4. Head-to-head: +1 to engineer assigned the role that won stakeholder vote

        Concurrency safety:
        - Double-reveal guard: check for existing SCORE# items before computing
        - Score lock sentinel: conditional PutItem on SCORE_LOCK#<sprint_id> prevents
          concurrent computation
        """
        pk = session_pk(session_code)

        # --- Double-reveal guard: check if scores already exist for this sprint ---
        existing_scores = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SCORE#"),
        )
        # Filter to scores for this specific sprint
        for item in existing_scores:
            if item.get("sprintId") == sprint_id:
                return  # Already scored this sprint

        # --- Score lock sentinel: prevent concurrent computation ---
        lock_item = {
            "PK": pk,
            "SK": score_lock_sk(sprint_id),
            "lockedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._session_table.put_item(
                Item=lock_item,
                ConditionExpression="attribute_not_exists(SK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Another concurrent request already claimed computation
                return
            raise

        now = datetime.now(timezone.utc).isoformat()

        # --- Build role_name → role_id mapping from scenario ---
        role_name_to_id: Dict[str, str] = {}
        info = self.get_session_scenario_info(session_code)
        if info and info.get("scenario_id"):
            scenario_data = self._load_scenario_for_session(info["scenario_id"])
            if scenario_data:
                roles = scenario_data.get("roles", [])
                for r in roles:
                    role_name_to_id[r.get("name", "")] = r.get("id", "")

        # --- Load participants for lookup ---
        participant_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("PARTICIPANT#"),
        )
        # Build participant lookup by id
        participants_by_id: Dict[str, Dict[str, Any]] = {}
        for p_item in participant_items:
            participants_by_id[p_item["participantId"]] = p_item

        # --- Load sprint question votes ---
        sq_vote_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with(f"SQ_VOTE#{sprint_id}#"),
        )

        # Separate voter (stakeholder) votes and engineer (player) votes
        voter_votes: List[Dict[str, Any]] = []
        engineer_vote_rows: List[Dict[str, Any]] = []

        for v in sq_vote_items:
            pid = v["participantId"]
            p_data = participants_by_id.get(pid)
            if not p_data:
                continue
            role = p_data.get("role", "")
            if role == "voter":
                # Attach voter_type info from participant
                v["voterTypeId"] = p_data.get("voterTypeId")
                v["voterTypeName"] = p_data.get("voterTypeName")
                voter_votes.append(v)
            elif role == "player":
                engineer_vote_rows.append(v)

        # --- Compute engineer majority (needed for tiebreaker) ---
        engineer_tally: Dict[int, int] = {}
        for v in engineer_vote_rows:
            choice = int(v["choiceIndex"])
            engineer_tally[choice] = engineer_tally.get(choice, 0) + 1

        engineer_majority: Optional[int] = None
        if engineer_tally:
            max_eng = max(engineer_tally.values())
            tied_eng = [c for c, cnt in engineer_tally.items() if cnt == max_eng]
            if len(tied_eng) == 1:
                engineer_majority = tied_eng[0]

        # --- Determine winning answer using 2-of-3 group rule ---
        winning_answer: Optional[int] = None
        group_positions: Dict[str, int] = {}  # voter_type_id → winning choice_index

        if voter_votes:
            # Group votes by voter_type_id
            groups: Dict[str, Dict[int, int]] = {}  # voter_type_id → {choice_index: count}
            for v in voter_votes:
                vt_id = v.get("voterTypeId")
                if vt_id is None:
                    continue  # Skip voters without a group assignment
                if vt_id not in groups:
                    groups[vt_id] = {}
                choice = int(v["choiceIndex"])
                groups[vt_id][choice] = groups[vt_id].get(choice, 0) + 1

            # Determine each group's position (internal majority)
            for vt_id, tally in groups.items():
                if not tally:
                    continue
                max_count = max(tally.values())
                tied_choices = [c for c, cnt in tally.items() if cnt == max_count]
                if len(tied_choices) == 1:
                    group_positions[vt_id] = tied_choices[0]
                else:
                    # Internal tie — use engineer tiebreaker
                    if engineer_majority is not None:
                        group_positions[vt_id] = engineer_majority
                    # else: engineers also tied — exclude this group

            # Apply 2-of-3 rule: count how many groups chose each option
            choice_group_count: Dict[int, int] = {}  # choice_index → number of groups
            for vt_id, choice in group_positions.items():
                choice_group_count[choice] = choice_group_count.get(choice, 0) + 1

            # Find the winning answer (choice with 2+ groups)
            for choice, count in choice_group_count.items():
                if count >= 2:
                    winning_answer = choice
                    break

            # Fallback: if fewer than 2 groups have votes, use overall majority
            if winning_answer is None and len(group_positions) < 2:
                overall_tally: Dict[int, int] = {}
                for v in voter_votes:
                    choice = int(v["choiceIndex"])
                    overall_tally[choice] = overall_tally.get(choice, 0) + 1
                if overall_tally:
                    winning_answer = max(overall_tally, key=lambda k: overall_tally[k])

        # Collect all score items to write
        # Use a dict keyed by (participantId, sprintId) to accumulate points
        # when a participant earns multiple scores in the same sprint (e.g.,
        # corporate choice + head-to-head).
        score_accumulator: Dict[str, Dict[str, Any]] = {}  # SK → item

        def _add_score(pid: str, pname: str, role_id: str, role_name: str,
                       points: int, reason: str) -> None:
            """Accumulate score points for a participant in this sprint."""
            sk = score_sk(pid, sprint_id)
            if sk in score_accumulator:
                # Participant already has a score — add points and append reason
                existing = score_accumulator[sk]
                existing["points"] = int(existing["points"]) + points
                existing["reason"] = existing["reason"] + "; " + reason
            else:
                score_accumulator[sk] = {
                    "PK": pk,
                    "SK": sk,
                    "scoreId": str(uuid4()),
                    "participantId": pid,
                    "participantName": pname,
                    "assignedRoleId": role_id,
                    "assignedRoleName": role_name,
                    "sprintId": sprint_id,
                    "points": points,
                    "reason": reason,
                    "createdAt": now,
                }

        # --- Award engineer points based on winning_answer ---
        if winning_answer is not None:
            for pv in engineer_vote_rows:
                if int(pv["choiceIndex"]) == winning_answer:
                    pid = pv["participantId"]
                    pname = pv["participantName"]
                    p_data = participants_by_id.get(pid, {})
                    assigned_role_name = p_data.get("assignedRoleName", "")
                    assigned_role_id = role_name_to_id.get(assigned_role_name, "")
                    _add_score(
                        pid, pname, assigned_role_id, assigned_role_name or "",
                        1, "Matched stakeholder group majority on corporate choice",
                    )

        # --- Group scoring ---
        if winning_answer is not None:
            voter_types = self.get_voter_types_for_session(session_code)
            vt_name_map = {vt.id: vt.name for vt in voter_types}

            for vt_id, choice in group_positions.items():
                if choice == winning_answer:
                    vt_name = vt_name_map.get(vt_id, vt_id)
                    _add_score(
                        vt_id, vt_name, "group", vt_name,
                        1, "Group majority matched winning answer on corporate choice",
                    )

        # --- Individual voter scoring ---
        if winning_answer is not None:
            for vote in voter_votes:
                if vote.get("voterTypeId") is None:
                    continue  # Skip voters without a group
                if int(vote["choiceIndex"]) == winning_answer:
                    pid = vote["participantId"]
                    pname = vote["participantName"]
                    vt_id = vote.get("voterTypeId", "")
                    vt_name = vote.get("voterTypeName") or "Voter"
                    _add_score(
                        pid, pname, vt_id, vt_name,
                        1, "Individual vote matched winning answer on corporate choice",
                    )

        # --- Head-to-head scoring ---
        h2h_vote_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with(f"H2H_VOTE#{sprint_id}#"),
        )

        if h2h_vote_items:
            h2h_tally: Dict[str, int] = {}
            for v in h2h_vote_items:
                rid = v["chosenRoleId"]
                h2h_tally[rid] = h2h_tally.get(rid, 0) + 1

            # Winner: highest count; on tie, lexicographically lower role ID wins
            max_count = max(h2h_tally.values())
            tied_roles = [rid for rid, cnt in h2h_tally.items() if cnt == max_count]
            winning_role_id = min(tied_roles)

            # Find the player assigned the winning role
            role_id_to_name = {v: k for k, v in role_name_to_id.items()}
            winning_role_name = role_id_to_name.get(winning_role_id, "")

            # Find participant with that assigned_role_name and role='player'
            winner = None
            for p_item in participant_items:
                if p_item.get("role") == "player" and p_item.get("assignedRoleName") == winning_role_name:
                    winner = p_item
                    break

            if winner:
                _add_score(
                    winner["participantId"], winner["name"],
                    winning_role_id, winner.get("assignedRoleName", ""),
                    1, "Won head-to-head stakeholder vote",
                )

        # --- Write all score items ---
        score_items = list(score_accumulator.values())
        if score_items:
            batch_write_items(self._session_table, score_items)

        # Bump version after writing scores
        self._bump_version(session_code)

    def get_scores(self, session_code: str) -> List[Dict[str, Any]]:
        """Return scores grouped by participant with totalPoints and breakdown array.

        Queries all SCORE# items for the session and aggregates them by participant,
        matching the output format of SqliteRepository.get_scores().
        """
        pk = session_pk(session_code)
        score_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("SCORE#"),
        )

        if not score_items:
            return []

        # Group by participantId
        participant_scores: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        participant_info: Dict[str, Dict[str, Any]] = {}

        for item in score_items:
            pid = item["participantId"]
            participant_scores[pid].append(item)
            if pid not in participant_info:
                participant_info[pid] = {
                    "participantName": item["participantName"],
                    "assignedRoleId": item["assignedRoleId"],
                    "assignedRoleName": item["assignedRoleName"],
                }

        # Build result sorted by total points descending
        result: List[Dict[str, Any]] = []
        for pid, scores in participant_scores.items():
            total_points = sum(int(s["points"]) for s in scores)
            # Sort breakdown by createdAt
            scores_sorted = sorted(scores, key=lambda s: s.get("createdAt", ""))
            breakdown = [
                {
                    "sprintId": s["sprintId"],
                    "points": int(s["points"]),
                    "reason": s["reason"],
                }
                for s in scores_sorted
            ]
            info = participant_info[pid]
            result.append({
                "participantId": pid,
                "participantName": info["participantName"],
                "assignedRoleId": info["assignedRoleId"],
                "assignedRoleName": info["assignedRoleName"],
                "totalPoints": total_points,
                "breakdown": breakdown,
            })

        # Sort by total points descending
        result.sort(key=lambda x: x["totalPoints"], reverse=True)
        return result

    def set_sprint_question_phase(self, session_code: str, phase: str) -> None:
        raise NotImplementedError

    def get_sprint_question_phase(self, session_code: str) -> str:
        raise NotImplementedError

    def set_scoreboard_visible(self, session_code: str, visible: bool) -> None:
        raise NotImplementedError

    # ==================== Role PINs ====================

    def generate_role_pins(self, session_code: str) -> int:
        """Generate unique PINs for all player roles in the session's scenario.

        Returns the count of PINs generated. Idempotent — skips if PINs already exist.
        """
        pk = session_pk(session_code)

        # Check if ROLE_PIN items already exist (idempotent)
        existing = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )
        if existing:
            return 0

        # Get scenario info for this session
        info = self.get_session_scenario_info(session_code)
        if not info or not info.get("scenario_id"):
            return 0

        # Load scenario to get player roles
        scenario = self.get_scenario(info["scenario_id"], info.get("scenario_version"))
        if not scenario:
            return 0

        # Filter to player roles only
        player_roles = [r for r in scenario.roles if r.get("roleType") == "player"]
        if not player_roles:
            return 0

        # Generate unique PINs for each role
        existing_pins: set = set()
        items_to_write: List[Dict[str, Any]] = []

        for role in player_roles:
            pin = self._generate_unique_pin(existing_pins)
            existing_pins.add(pin)
            role_pin = RolePin(
                session_id=session_code,
                role_id=role.get("id", ""),
                role_name=role.get("name", ""),
                pin=pin,
            )
            items_to_write.append(role_pin_to_item(role_pin, session_code))

        # Write all PIN items in batch
        batch_write_items(self._session_table, items_to_write)

        return len(items_to_write)

    def validate_role_pin(self, session_code: str, pin: str) -> Optional[RolePin]:
        """Look up a PIN in the session. Returns RolePin if found, None otherwise."""
        pk = session_pk(session_code)

        # Query all ROLE_PIN items in the session partition
        role_pin_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )

        # Find the item whose pin value matches
        for item in role_pin_items:
            if item.get("pin") == pin:
                return item_to_role_pin(item)

        return None

    def claim_role_pin(
        self,
        session_code: str,
        role_id: str,
        participant_id: str,
        participant_name: str,
    ) -> bool:
        """Mark a role as claimed by a participant.

        Uses conditional UpdateItem with attribute_not_exists(claimedByParticipantId)
        to ensure the role is not already claimed. Returns False on condition failure.
        """
        pk = session_pk(session_code)
        sk = role_pin_sk(role_id)
        now = datetime.now(timezone.utc).isoformat()

        try:
            self._session_table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression=(
                    "SET claimedByParticipantId = :pid, "
                    "claimedByName = :pname, "
                    "claimedAt = :now"
                ),
                ConditionExpression=(
                    "attribute_not_exists(claimedByParticipantId) OR "
                    "claimedByParticipantId = :null"
                ),
                ExpressionAttributeValues={
                    ":pid": participant_id,
                    ":pname": participant_name,
                    ":now": now,
                    ":null": None,
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_role_pin(self, session_code: str, participant_id: str) -> bool:
        """Release a role claim when a player is removed. Returns True if a claim was released."""
        pk = session_pk(session_code)

        # Find the ROLE_PIN item claimed by this participant
        role_pin_items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )

        for item in role_pin_items:
            if item.get("claimedByParticipantId") == participant_id:
                # Clear the claim fields
                self._session_table.update_item(
                    Key={"PK": pk, "SK": item["SK"]},
                    UpdateExpression=(
                        "REMOVE claimedByParticipantId, claimedByName, claimedAt"
                    ),
                )
                return True

        return False

    def get_role_pins(self, session_code: str) -> List[RolePin]:
        """Get all role PINs for a session with claim status."""
        pk = session_pk(session_code)
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )
        pins = [item_to_role_pin(item) for item in items]
        # Sort by role_name to match SQLite ORDER BY role_name
        pins.sort(key=lambda rp: rp.role_name)
        return pins

    def get_role_pin_count(self, session_code: str) -> int:
        """Return total count of role PINs for a session. Used to check if PINs exist."""
        pk = session_pk(session_code)
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )
        return len(items)

    def get_unclaimed_role_count(self, session_code: str) -> int:
        """Return count of unclaimed roles. Used to determine if 'Player' option should show."""
        pk = session_pk(session_code)
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )
        return sum(
            1 for item in items
            if not item.get("claimedByParticipantId")
        )

    def get_unclaimed_roles(self, session_code: str) -> List[Dict[str, Any]]:
        """Return unclaimed roles with their details (id, name, description).

        Used to show available roles when a player needs to enter a role PIN.
        """
        pk = session_pk(session_code)
        items = query_all_pages(
            self._session_table,
            Key("PK").eq(pk) & Key("SK").begins_with("ROLE_PIN#"),
        )

        # Filter to unclaimed only
        unclaimed_items = [
            item for item in items
            if not item.get("claimedByParticipantId")
        ]

        if not unclaimed_items:
            return []

        # Get scenario info to retrieve role descriptions
        info = self.get_session_scenario_info(session_code)
        if not info or not info.get("scenario_id"):
            # Return basic info without descriptions
            result = [
                {"id": item["roleId"], "name": item["roleName"], "description": ""}
                for item in unclaimed_items
            ]
            result.sort(key=lambda r: r["name"])
            return result

        scenario = self.get_scenario(info["scenario_id"], info.get("scenario_version"))
        if not scenario:
            result = [
                {"id": item["roleId"], "name": item["roleName"], "description": ""}
                for item in unclaimed_items
            ]
            result.sort(key=lambda r: r["name"])
            return result

        # Build a map of role_id -> description from scenario
        role_descriptions = {r.get("id"): r.get("description", "") for r in scenario.roles}

        result = [
            {
                "id": item["roleId"],
                "name": item["roleName"],
                "description": role_descriptions.get(item["roleId"], ""),
            }
            for item in unclaimed_items
        ]
        result.sort(key=lambda r: r["name"])
        return result

    # ==================== Scenario CRUD ====================

    def list_scenarios(
        self, include_deleted: bool = False
    ) -> List[ScenarioVersion]:
        """List all scenarios (latest versions only by default)."""
        return self._scenario_repo.list_scenarios(include_deleted)

    def get_scenario(
        self, scenario_id: str, version: Optional[int] = None
    ) -> Optional[ScenarioVersion]:
        """Get a scenario by ID. If version is None, returns the latest version."""
        return self._scenario_repo.get_scenario(scenario_id, version)

    def get_scenario_history(self, scenario_id: str) -> List[ScenarioVersion]:
        """Get all versions of a scenario, newest first."""
        return self._scenario_repo.get_scenario_history(scenario_id)

    def create_scenario(
        self, data: Dict[str, Any], created_by: Optional[str] = None
    ) -> ScenarioVersion:
        """Create a new scenario from JSON data."""
        return self._scenario_repo.create_scenario(data, created_by)

    def update_scenario(
        self,
        scenario_id: str,
        data: Dict[str, Any],
        created_by: Optional[str] = None,
    ) -> Optional[ScenarioVersion]:
        """Update a scenario by creating a new version. Returns None if not found."""
        return self._scenario_repo.update_scenario(scenario_id, data, created_by)

    def delete_scenario(self, scenario_id: str) -> bool:
        """Soft-delete a scenario."""
        return self._scenario_repo.delete_scenario(scenario_id)

    def restore_scenario(self, scenario_id: str) -> bool:
        """Restore a soft-deleted scenario."""
        return self._scenario_repo.restore_scenario(scenario_id)

    def enable_scenario(self, scenario_id: str) -> bool:
        """Enable a scenario for use in session creation."""
        return self._scenario_repo.enable_scenario(scenario_id)

    def disable_scenario(self, scenario_id: str) -> bool:
        """Disable a scenario from being used in session creation."""
        return self._scenario_repo.disable_scenario(scenario_id)

    def diff_scenario(
        self, scenario_id: str, new_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compare new data against the current version and return differences."""
        return self._scenario_repo.diff_scenario(scenario_id, new_data)

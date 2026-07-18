"""DynamoDB key builder and validation utilities for the SSP single-table design."""

import json
import time
from typing import Any, Dict, List, Optional


def validate_id(value: str) -> str:
    """Validate that an ID segment does not contain the '#' delimiter.

    Raises ValueError if the value contains '#'.
    Returns the value unchanged if valid.
    """
    if "#" in value:
        raise ValueError(
            f"ID segment must not contain '#' delimiter, got: {value!r}"
        )
    return value


def session_pk(code: str) -> str:
    """Build partition key for a session: SESS#<code>."""
    return f"SESS#{validate_id(code)}"


def meta_sk() -> str:
    """Build sort key for session metadata: META."""
    return "META"


def participant_sk(participant_id: str) -> str:
    """Build sort key for a participant: PARTICIPANT#<id>."""
    return f"PARTICIPANT#{validate_id(participant_id)}"


def response_sk(sprint_id: str, response_id: str) -> str:
    """Build sort key for a response: RESPONSE#<sprint_id>#<response_id>."""
    return f"RESPONSE#{validate_id(sprint_id)}#{validate_id(response_id)}"


def vote_sk(response_id: str, participant_id: str) -> str:
    """Build sort key for a response vote: VOTE#<response_id>#<participant_id>."""
    return f"VOTE#{validate_id(response_id)}#{validate_id(participant_id)}"


def sq_vote_sk(sprint_id: str, participant_id: str) -> str:
    """Build sort key for a sprint question vote: SQ_VOTE#<sprint_id>#<participant_id>."""
    return f"SQ_VOTE#{validate_id(sprint_id)}#{validate_id(participant_id)}"


def h2h_vote_sk(sprint_id: str, participant_id: str) -> str:
    """Build sort key for a head-to-head vote: H2H_VOTE#<sprint_id>#<participant_id>."""
    return f"H2H_VOTE#{validate_id(sprint_id)}#{validate_id(participant_id)}"


def score_sk(participant_id: str, sprint_id: str) -> str:
    """Build sort key for a score: SCORE#<participant_id>#<sprint_id>."""
    return f"SCORE#{validate_id(participant_id)}#{validate_id(sprint_id)}"


def score_lock_sk(sprint_id: str) -> str:
    """Build sort key for a score lock sentinel: SCORE_LOCK#<sprint_id>."""
    return f"SCORE_LOCK#{validate_id(sprint_id)}"


def chat_sk(timestamp: str, message_id: str) -> str:
    """Build sort key for a chat message: CHAT#<timestamp>#<message_id>.

    Timestamp should be ISO-8601 UTC with millisecond precision
    (e.g., 2024-01-15T10:00:00.000Z) to ensure lexicographic order
    equals chronological order.
    """
    return f"CHAT#{validate_id(timestamp)}#{validate_id(message_id)}"


def role_pin_sk(role_id: str) -> str:
    """Build sort key for a role PIN: ROLE_PIN#<role_id>."""
    return f"ROLE_PIN#{validate_id(role_id)}"


def sprint_sk(sprint_id: str) -> str:
    """Build sort key for a sprint definition: SPRINT#<sprint_id>."""
    return f"SPRINT#{validate_id(sprint_id)}"


def scenario_pk(scenario_id: str) -> str:
    """Build partition key for a scenario: SCEN#<scenario_id>."""
    return f"SCEN#{validate_id(scenario_id)}"


def version_sk(version_num: int) -> str:
    """Build sort key for a scenario version: VER#<zero-padded-6-digit>.

    The version number is zero-padded to 6 digits (e.g., VER#000001).
    """
    return f"VER#{version_num:06d}"


# --- Batch write and pagination utilities ---


def batch_write_items(table: Any, items: List[dict], max_retries: int = 3) -> None:
    """Write items to a DynamoDB table in batches of 25 with retry on unprocessed items.

    Uses table.meta.client.batch_write_item() for the low-level call.
    Retries unprocessed items up to max_retries times with exponential backoff.

    Raises RuntimeError if items remain unprocessed after all retries.
    """
    table_name = table.table_name
    # Process in groups of 25 (DynamoDB BatchWriteItem limit)
    for i in range(0, len(items), 25):
        batch = items[i : i + 25]
        request_items = {
            table_name: [{"PutRequest": {"Item": item}} for item in batch]
        }

        retries = 0
        while request_items:
            response = table.meta.client.batch_write_item(
                RequestItems=request_items
            )
            unprocessed = response.get("UnprocessedItems", {})
            if not unprocessed:
                break
            retries += 1
            if retries > max_retries:
                raise RuntimeError(
                    f"Failed to write all items after {max_retries} retries. "
                    f"{len(unprocessed.get(table_name, []))} items remain unprocessed."
                )
            time.sleep(2**retries * 0.1)  # Exponential backoff
            request_items = unprocessed


def query_all_pages(table: Any, key_condition: Any, **kwargs: Any) -> List[dict]:
    """Query a DynamoDB table following pagination until all items are retrieved.

    Args:
        table: A boto3 DynamoDB Table resource.
        key_condition: A boto3 Key condition expression (e.g., Key('PK').eq('SESS#CODE')).
        **kwargs: Additional query parameters (FilterExpression, ProjectionExpression, etc.).

    Returns:
        A flat list of all items across all pages.
    """
    all_items: List[dict] = []
    query_kwargs = {"KeyConditionExpression": key_condition, **kwargs}

    while True:
        response = table.query(**query_kwargs)
        all_items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    return all_items


# ---------------------------------------------------------------------------
# Serialization helpers: DynamoDB item ↔ Python dataclass conversion
# ---------------------------------------------------------------------------

from .models import (
    Participant,
    ResponseItem,
    ChatMessage,
    Vote,
    VoterInfo,
    Sprint,
    SprintQuestion,
    HeadToHead,
    Score,
    RolePin,
)


# ---------------------------------------------------------------------------
# DynamoDB item → dataclass (deserialization)
# ---------------------------------------------------------------------------


def item_to_participant(item: Dict[str, Any]) -> Participant:
    """Convert a DynamoDB PARTICIPANT item to a Participant dataclass."""
    return Participant(
        id=item["participantId"],
        name=item["name"],
        role=item["role"],
        assigned_role_name=item.get("assignedRoleName"),
        assigned_role_id=item.get("assignedRoleId"),
        role_type=item.get("roleType"),
        role_description=item.get("roleDescription"),
        private_info=item.get("privateInfo"),
        ip_address=item.get("ipAddress"),
        joined_at=item.get("joinedAt"),
        voter_type_id=item.get("voterTypeId"),
        voter_type_name=item.get("voterTypeName"),
    )


def item_to_response(item: Dict[str, Any]) -> ResponseItem:
    """Convert a DynamoDB RESPONSE item to a ResponseItem dataclass."""
    return ResponseItem(
        id=item["responseId"],
        participant_name=item["participantName"],
        text=item["text"],
        sprint_id=item["sprintId"],
        created_at=item["createdAt"],
        response_box=item.get("responseBox"),
        participant_role_name=item.get("participantRoleName"),
    )


def item_to_chat_message(item: Dict[str, Any]) -> ChatMessage:
    """Convert a DynamoDB CHAT item to a ChatMessage dataclass."""
    return ChatMessage(
        id=item["messageId"],
        sender_name=item["senderName"],
        sender_role=item["senderRole"],
        text=item["text"],
        created_at=item["createdAt"],
    )


def item_to_vote(item: Dict[str, Any]) -> Vote:
    """Convert a DynamoDB VOTE item to a Vote dataclass."""
    return Vote(
        id=item.get("SK", ""),
        session_id=item.get("PK", "").replace("SESS#", ""),
        response_id=item["responseId"],
        participant_id=item["participantId"],
        participant_name=item["participantName"],
        participant_role=item["participantRole"],
        sprint_id=item["sprintId"],
        created_at=item["createdAt"],
        voter_type_id=item.get("voterTypeId"),
        voter_type_name=item.get("voterTypeName"),
    )


def item_to_voter_info(item: Dict[str, Any]) -> VoterInfo:
    """Convert a DynamoDB VOTE item to a VoterInfo dataclass."""
    return VoterInfo(
        participant_id=item["participantId"],
        participant_name=item["participantName"],
        participant_role=item["participantRole"],
        created_at=item["createdAt"],
        vote_id=item.get("SK", ""),
        voter_type_id=item.get("voterTypeId"),
        voter_type_name=item.get("voterTypeName"),
    )


def item_to_sprint(item: Dict[str, Any]) -> Sprint:
    """Convert a DynamoDB SPRINT item to a Sprint dataclass."""
    # responseBoxes stored as JSON string
    response_boxes_raw = item.get("responseBoxes")
    response_boxes: Optional[list] = None
    if response_boxes_raw is not None:
        response_boxes = json.loads(response_boxes_raw) if isinstance(response_boxes_raw, str) else response_boxes_raw

    # sprintQuestion stored as JSON string or None
    sprint_question_raw = item.get("sprintQuestion")
    sprint_question: Optional[SprintQuestion] = None
    if sprint_question_raw is not None:
        sq = json.loads(sprint_question_raw) if isinstance(sprint_question_raw, str) else sprint_question_raw
        sprint_question = SprintQuestion(prompt=sq["prompt"], choices=sq["choices"])

    # headToHead stored as JSON string or None
    head_to_head_raw = item.get("headToHead")
    head_to_head: Optional[HeadToHead] = None
    if head_to_head_raw is not None:
        h2h = json.loads(head_to_head_raw) if isinstance(head_to_head_raw, str) else head_to_head_raw
        head_to_head = HeadToHead(prompt=h2h["prompt"], role_a=h2h["roleA"], role_b=h2h["roleB"])

    return Sprint(
        id=item["sprintId"],
        name=item["name"],
        description=item["description"],
        timer_enabled=item.get("timerEnabled", False),
        timer_seconds=item.get("timerSeconds"),
        response_boxes=response_boxes,
        sprint_question=sprint_question,
        head_to_head=head_to_head,
    )


def item_to_score(item: Dict[str, Any]) -> Score:
    """Convert a DynamoDB SCORE item to a Score dataclass."""
    return Score(
        id=item.get("scoreId", item.get("SK", "")),
        session_id=item.get("PK", "").replace("SESS#", ""),
        participant_id=item["participantId"],
        participant_name=item["participantName"],
        assigned_role_id=item["assignedRoleId"],
        assigned_role_name=item["assignedRoleName"],
        sprint_id=item["sprintId"],
        points=int(item["points"]),
        reason=item["reason"],
        created_at=item["createdAt"],
    )


def item_to_role_pin(item: Dict[str, Any]) -> RolePin:
    """Convert a DynamoDB ROLE_PIN item to a RolePin dataclass."""
    return RolePin(
        session_id=item.get("PK", "").replace("SESS#", ""),
        role_id=item["roleId"],
        role_name=item["roleName"],
        pin=item["pin"],
        claimed_by_participant_id=item.get("claimedByParticipantId"),
        claimed_by_name=item.get("claimedByName"),
        claimed_at=item.get("claimedAt"),
    )


# ---------------------------------------------------------------------------
# Dataclass → DynamoDB item (serialization)
# ---------------------------------------------------------------------------


def participant_to_item(participant: Participant, session_code: str) -> Dict[str, Any]:
    """Convert a Participant dataclass to a DynamoDB item dict."""
    return {
        "PK": session_pk(session_code),
        "SK": participant_sk(participant.id),
        "participantId": participant.id,
        "name": participant.name,
        "role": participant.role,
        "assignedRoleName": participant.assigned_role_name,
        "assignedRoleId": participant.assigned_role_id,
        "roleType": participant.role_type,
        "roleDescription": participant.role_description,
        "privateInfo": participant.private_info,
        "ipAddress": participant.ip_address,
        "joinedAt": participant.joined_at,
        "voterTypeId": participant.voter_type_id,
        "voterTypeName": participant.voter_type_name,
    }


def response_to_item(response: ResponseItem, session_code: str) -> Dict[str, Any]:
    """Convert a ResponseItem dataclass to a DynamoDB item dict."""
    return {
        "PK": session_pk(session_code),
        "SK": response_sk(response.sprint_id, response.id),
        "responseId": response.id,
        "sprintId": response.sprint_id,
        "participantName": response.participant_name,
        "participantRoleName": response.participant_role_name,
        "text": response.text,
        "responseBox": response.response_box,
        "createdAt": response.created_at,
    }


def chat_message_to_item(msg: ChatMessage, session_code: str) -> Dict[str, Any]:
    """Convert a ChatMessage dataclass to a DynamoDB item dict."""
    return {
        "PK": session_pk(session_code),
        "SK": chat_sk(msg.created_at, msg.id),
        "messageId": msg.id,
        "senderName": msg.sender_name,
        "senderRole": msg.sender_role,
        "text": msg.text,
        "createdAt": msg.created_at,
    }


def vote_to_item(vote: Vote, session_code: str) -> Dict[str, Any]:
    """Convert a Vote dataclass to a DynamoDB item dict."""
    return {
        "PK": session_pk(session_code),
        "SK": vote_sk(vote.response_id, vote.participant_id),
        "responseId": vote.response_id,
        "participantId": vote.participant_id,
        "participantName": vote.participant_name,
        "participantRole": vote.participant_role,
        "sprintId": vote.sprint_id,
        "createdAt": vote.created_at,
        "voterTypeId": vote.voter_type_id,
        "voterTypeName": vote.voter_type_name,
    }


def score_to_item(score: Score, session_code: str) -> Dict[str, Any]:
    """Convert a Score dataclass to a DynamoDB item dict."""
    return {
        "PK": session_pk(session_code),
        "SK": score_sk(score.participant_id, score.sprint_id),
        "scoreId": score.id,
        "participantId": score.participant_id,
        "participantName": score.participant_name,
        "assignedRoleId": score.assigned_role_id,
        "assignedRoleName": score.assigned_role_name,
        "sprintId": score.sprint_id,
        "points": score.points,
        "reason": score.reason,
        "createdAt": score.created_at,
    }


def role_pin_to_item(role_pin: RolePin, session_code: str) -> Dict[str, Any]:
    """Convert a RolePin dataclass to a DynamoDB item dict."""
    item: Dict[str, Any] = {
        "PK": session_pk(session_code),
        "SK": role_pin_sk(role_pin.role_id),
        "roleId": role_pin.role_id,
        "roleName": role_pin.role_name,
        "pin": role_pin.pin,
    }
    if role_pin.claimed_by_participant_id is not None:
        item["claimedByParticipantId"] = role_pin.claimed_by_participant_id
    if role_pin.claimed_by_name is not None:
        item["claimedByName"] = role_pin.claimed_by_name
    if role_pin.claimed_at is not None:
        item["claimedAt"] = role_pin.claimed_at
    return item


def sprint_to_item(sprint: Sprint, session_code: str) -> Dict[str, Any]:
    """Convert a Sprint dataclass to a DynamoDB item dict."""
    item: Dict[str, Any] = {
        "PK": session_pk(session_code),
        "SK": sprint_sk(sprint.id),
        "sprintId": sprint.id,
        "name": sprint.name,
        "description": sprint.description,
        "timerEnabled": sprint.timer_enabled,
        "timerSeconds": sprint.timer_seconds,
    }

    # Store complex fields as JSON strings
    if sprint.response_boxes is not None:
        item["responseBoxes"] = json.dumps(sprint.response_boxes)
    else:
        item["responseBoxes"] = None

    if sprint.sprint_question is not None:
        item["sprintQuestion"] = json.dumps({
            "prompt": sprint.sprint_question.prompt,
            "choices": sprint.sprint_question.choices,
        })
    else:
        item["sprintQuestion"] = None

    if sprint.head_to_head is not None:
        item["headToHead"] = json.dumps({
            "prompt": sprint.head_to_head.prompt,
            "roleA": sprint.head_to_head.role_a,
            "roleB": sprint.head_to_head.role_b,
        })
    else:
        item["headToHead"] = None

    return item

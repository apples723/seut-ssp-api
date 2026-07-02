import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

TIMER_ENABLED = os.environ.get('TIMER_ENABLED', 'true').lower() == 'true'


@dataclass
class ChatMessage:
    id: str
    sender_name: str
    sender_role: str
    text: str
    created_at: str


@dataclass
class SprintQuestion:
    prompt: str
    choices: List[str]


@dataclass
class HeadToHead:
    prompt: str
    role_a: str
    role_b: str


@dataclass
class EngineerVote:
    id: str
    session_id: str
    sprint_id: str
    participant_id: str
    participant_name: str
    choice_index: int
    choice_text: str
    created_at: str


@dataclass
class HeadToHeadVote:
    id: str
    session_id: str
    sprint_id: str
    participant_id: str
    participant_name: str
    voter_type_id: Optional[str]
    voter_type_name: Optional[str]
    chosen_role_id: str
    created_at: str


@dataclass
class Score:
    id: str
    session_id: str
    participant_id: str
    participant_name: str
    assigned_role_id: str
    assigned_role_name: str
    sprint_id: str
    points: int
    reason: str
    created_at: str


@dataclass
class RolePin:
    """Represents a PIN assigned to a player role for claiming during a session."""
    session_id: str
    role_id: str
    role_name: str
    pin: str
    claimed_by_participant_id: Optional[str] = None
    claimed_by_name: Optional[str] = None
    claimed_at: Optional[str] = None


@dataclass
class Sprint:
    id: str
    name: str
    description: str
    timer_enabled: bool = False
    timer_seconds: Optional[int] = None
    response_boxes: Optional[List[str]] = None
    sprint_question: Optional[SprintQuestion] = None
    head_to_head: Optional[HeadToHead] = None


@dataclass
class VoterType:
    id: str
    name: str
    description: Optional[str] = None


@dataclass
class Participant:
    id: str
    name: str
    role: str
    assigned_role_name: Optional[str] = None
    role_type: Optional[str] = None
    role_description: Optional[str] = None
    private_info: Optional[str] = None
    ip_address: Optional[str] = None
    joined_at: Optional[str] = None
    voter_type_id: Optional[str] = None
    voter_type_name: Optional[str] = None


@dataclass
class VoterInfo:
    participant_id: str
    participant_name: str
    participant_role: str
    created_at: str
    vote_id: str
    voter_type_id: Optional[str] = None
    voter_type_name: Optional[str] = None


@dataclass
class Vote:
    id: str
    session_id: str
    response_id: str
    participant_id: str
    participant_name: str
    participant_role: str
    sprint_id: str
    created_at: str
    voter_type_id: Optional[str] = None
    voter_type_name: Optional[str] = None


@dataclass
class ResponseItem:
    id: str
    participant_name: str
    text: str
    sprint_id: str
    created_at: str
    response_box: Optional[str] = None
    participant_role_name: Optional[str] = None
    vote_count: int = 0
    voters: Optional[List['VoterInfo']] = None
    current_user_voted: bool = False


@dataclass
class Session:
    session_id: str
    scenario_name: str
    current_sprint_index: int
    total_sprints: int
    timer_visible: bool
    timer_seconds: Optional[int]
    participant: Participant
    current_sprint: Sprint
    responses: List[ResponseItem] = field(default_factory=list)
    participants: List[Participant] = field(default_factory=list)
    chat_messages: List[ChatMessage] = field(default_factory=list)
    sprints: List[Sprint] = field(default_factory=list)  # All sprints from scenario
    facilitator_pin: Optional[str] = None
    status: str = 'active'  # 'active' | 'ended'
    started: bool = False  # Whether facilitator has started the simulation
    scenario_description: Optional[str] = None  # Description shown in scenario card
    waiting_room_message: Optional[str] = None  # Custom message for waiting room
    present_mode_instructions: Optional[str] = None  # Instructions for present mode waiting room
    created_at: Optional[str] = None
    timer_running: bool = False
    version: int = 0  # Incremented on any change, used for SSE updates
    voting_enabled: bool = False
    voting_show_voters: bool = False
    voter_types: List[VoterType] = field(default_factory=list)
    sprint_question_phase: str = 'inactive'
    scoreboard_visible: bool = False
    engineer_vote_choice: Optional[int] = None
    scores: List[Dict[str, Any]] = field(default_factory=list)
    role_pins_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            'sessionId': self.session_id,
            'scenarioName': self.scenario_name,
            'scenarioDescription': self.scenario_description,
            'waitingRoomMessage': self.waiting_room_message,
            'presentModeInstructions': self.present_mode_instructions,
            'currentSprintIndex': self.current_sprint_index,
            'totalSprints': self.total_sprints,
            'timerVisible': self.timer_visible and TIMER_ENABLED,
            'timerSeconds': self.timer_seconds,
            'timerRunning': self.timer_running,
            'status': self.status,
            'started': self.started,
            'hasFacilitatorPin': self.facilitator_pin is not None,
            'createdAt': self.created_at,
            'version': self.version,
            'votingEnabled': self.voting_enabled,
            'votingShowVoters': self.voting_show_voters,
            'rolePinsEnabled': self.role_pins_enabled,
            'sprintPhase': self.sprint_question_phase,
            'scoreboardVisible': self.scoreboard_visible,
            'engineerVoteChoice': self.engineer_vote_choice,
            'scores': self.scores,
            'voterTypes': [
                {
                    'id': vt.id,
                    'name': vt.name,
                    'description': vt.description,
                }
                for vt in self.voter_types
            ],
            'participant': {
                'id': self.participant.id,
                'name': self.participant.name,
                'role': self.participant.role,
                'assignedRoleName': self.participant.assigned_role_name,
                'roleType': self.participant.role_type,
                'roleDescription': self.participant.role_description,
                'privateInfo': self.participant.private_info,
                'voterTypeId': self.participant.voter_type_id,
                'voterTypeName': self.participant.voter_type_name,
            },
            'currentSprint': {
                'id': self.current_sprint.id,
                'name': self.current_sprint.name,
                'description': self.current_sprint.description,
                'timerEnabled': self.current_sprint.timer_enabled,
                'timerSeconds': self.current_sprint.timer_seconds,
                'responseBoxes': self.current_sprint.response_boxes,
                'sprintQuestion': {
                    'prompt': self.current_sprint.sprint_question.prompt,
                    'choices': self.current_sprint.sprint_question.choices,
                } if self.current_sprint.sprint_question else None,
                'headToHead': {
                    'prompt': self.current_sprint.head_to_head.prompt,
                    'roleA': self.current_sprint.head_to_head.role_a,
                    'roleB': self.current_sprint.head_to_head.role_b,
                } if self.current_sprint.head_to_head else None,
            },
            'responses': [
                {
                    'id': response.id,
                    'participantName': response.participant_name,
                    'participantRoleName': response.participant_role_name,
                    'text': response.text,
                    'sprintId': response.sprint_id,
                    'createdAt': response.created_at,
                    'responseBox': response.response_box,
                    'voteCount': response.vote_count,
                    'currentUserVoted': response.current_user_voted,
                    'voters': [
                        {
                            'participantId': v.participant_id,
                            'participantName': v.participant_name,
                            'participantRole': v.participant_role,
                            'createdAt': v.created_at,
                            'voteId': v.vote_id,
                            'voterTypeId': v.voter_type_id,
                            'voterTypeName': v.voter_type_name,
                        }
                        for v in response.voters
                    ] if response.voters is not None else None,
                }
                for response in self.responses
            ],
            'chatMessages': [
                {
                    'id': msg.id,
                    'senderName': msg.sender_name,
                    'senderRole': msg.sender_role,
                    'text': msg.text,
                    'createdAt': msg.created_at,
                }
                for msg in self.chat_messages
            ],
            'participants': [
                {
                    'id': p.id,
                    'name': p.name,
                    'role': p.role,
                    'assignedRoleName': p.assigned_role_name,
                    'voterTypeId': p.voter_type_id,
                    'voterTypeName': p.voter_type_name,
                }
                for p in self.participants
            ],
        }

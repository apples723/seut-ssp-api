"""Repository protocol defining the abstract interface for all data access operations.

Both SqliteRepository and DynamoRepository implement this protocol.
Uses Python's typing.Protocol for structural subtyping without requiring inheritance.
"""
from typing import Dict, List, Optional, Any, Protocol

from .models import (
    Session,
    Participant,
    ResponseItem,
    ChatMessage,
    VoterInfo,
    VoterType,
    RolePin,
    Sprint,
)
from .scenario_repository import ScenarioVersion
from .tutorial_repository import TutorialActivation, TutorialVersion


class RepositoryProtocol(Protocol):
    """Protocol defining the complete repository interface.

    Covers: session CRUD, participant management, response management,
    sprint control, chat, voting, sprint question voting, scoring,
    role PINs, and scenario CRUD operations.
    """

    # ==================== Session CRUD ====================

    def get_session(self) -> Session:
        """Get the default demo session (backwards compatibility)."""
        ...

    def get_session_by_code(self, session_code: str, participant_id: Optional[str] = None) -> Optional[Session]:
        """Get a session by its code, optionally scoped to a participant."""
        ...

    def create_session(
        self,
        facilitator_name: str,
        scenario_id: str,
        facilitator_pin: Optional[str] = None,
        ip_address: Optional[str] = None,
        role_pins_enabled: bool = False,
    ) -> Session:
        """Create a new session with a generated code."""
        ...

    def list_sessions(self) -> list:
        """Return a summary list of all sessions for admin view."""
        ...

    def has_facilitator(self, session_code: str) -> bool:
        """Check if a session has a facilitator participant."""
        ...

    def check_facilitator_pin(self, session_code: str, pin: str) -> bool:
        """Check if the provided PIN matches the session's facilitator PIN."""
        ...

    def replace_facilitator(
        self, session_code: str, name: str, ip_address: Optional[str] = None
    ) -> Optional[Participant]:
        """Replace the current facilitator with a new one."""
        ...

    def end_session(self, session_code: str) -> bool:
        """End a session. Returns True if the session was ended."""
        ...

    def start_session(self, session_code: str) -> bool:
        """Start a session (set started=True). Returns True on success."""
        ...

    def get_session_scenario_info(self, session_code: str) -> Optional[dict]:
        """Return the scenario_id and scenario_version for a session."""
        ...

    def get_voter_types_for_session(self, session_code: str) -> List[VoterType]:
        """Return the voter types configured for a session."""
        ...

    def is_role_pins_enabled(self, session_code: str) -> bool:
        """Check whether role PIN-based assignment is enabled for a session."""
        ...

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
        """Add a participant to a session and return the participant object."""
        ...

    def get_session_participants(self, session_code: str) -> List[Participant]:
        """Return all participants for a session."""
        ...

    def update_participant_role(
        self,
        participant_id: str,
        assigned_role_name: str,
        role_description: str,
        private_info: Optional[str],
        session_code: str,
        assigned_role_id: Optional[str] = None,
    ) -> bool:
        """Update a participant's scenario role assignment."""
        ...

    def remove_participant(self, participant_id: str, session_code: str) -> bool:
        """Remove a participant from a session."""
        ...

    def is_facilitator(self, session_code: str, participant_id: str) -> bool:
        """Check if a participant is the facilitator for a session."""
        ...

    def replace_player_for_role(
        self,
        session_code: str,
        role_id: str,
        new_participant_name: str,
        ip_address: Optional[str] = None,
    ) -> Optional[Participant]:
        """Replace the current claimant with a new participant (reconnection)."""
        ...

    # ==================== Responses ====================

    def add_response(
        self,
        participant_name: str,
        text: str,
        response_box: Optional[str] = None,
        participant_role_name: Optional[str] = None,
    ) -> ResponseItem:
        """Add a response to the default demo session (backwards compatibility)."""
        ...

    def add_response_to_session(
        self,
        session_code: str,
        participant_name: str,
        text: str,
        response_box: Optional[str] = None,
        participant_role_name: Optional[str] = None,
    ) -> Optional[ResponseItem]:
        """Add a response to a specific session."""
        ...

    def delete_response(self, session_code: str, response_id: str) -> bool:
        """Delete a response and all associated votes. Returns True if deleted."""
        ...

    # ==================== Sprint Control ====================

    def next_sprint(self) -> Session:
        """Advance to the next sprint in the default demo session (backwards compatibility)."""
        ...

    def next_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Advance to the next sprint for a specific session."""
        ...

    def prev_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Go back to the previous sprint for a specific session."""
        ...

    def update_timer(
        self, session_code: str, action: str, seconds: Optional[int] = None
    ) -> Optional[Session]:
        """Update timer state for a session."""
        ...

    # ==================== Chat ====================

    def add_chat_message(
        self, session_code: str, sender_name: str, sender_role: str, text: str
    ) -> Optional[ChatMessage]:
        """Add a chat message to a session."""
        ...

    # ==================== Session Log ====================

    def get_session_log(self) -> list:
        """Return the session event log."""
        ...

    # ==================== Participant Helpers ====================

    def get_participant_role_name(self, session_code: str, participant_id: str) -> Optional[str]:
        """Get the assigned role name for a participant."""
        ...

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
        """Add a vote to a response. Returns True if added, False if already exists."""
        ...

    def remove_vote(self, session_code: str, response_id: str, participant_id: str) -> bool:
        """Remove a participant's vote from a response. Returns True if deleted."""
        ...

    def remove_vote_by_id(self, vote_id: str) -> Optional[Dict[str, Any]]:
        """Remove a specific vote by ID. Returns vote details or None if not found."""
        ...

    def get_votes_for_responses(
        self,
        response_ids: List[str],
        participant_id: Optional[str] = None,
        include_voters: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Get vote data for multiple responses."""
        ...

    def set_voting_enabled(self, session_code: str, enabled: bool) -> bool:
        """Enable or disable voting for a session. Returns True on success."""
        ...

    def toggle_voting_show_voters(self, session_code: str) -> bool:
        """Toggle voter visibility setting. Returns new boolean state."""
        ...

    def get_vote_summary_by_role(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Get vote summary grouped by participant_role for a session/sprint."""
        ...

    def get_vote_summary_by_voter_type(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Get vote summary grouped by voter_type for a session/sprint."""
        ...

    # ==================== Sprint Question / Scoring ====================

    def submit_engineer_vote(
        self,
        session_code: str,
        sprint_id: str,
        participant_id: str,
        participant_name: str,
        choice_index: int,
        choice_text: str,
        event_name: str = 'engineer_vote_submitted',
    ) -> None:
        """Upsert an engineer (player) sprint question vote."""
        ...

    def get_engineer_vote(
        self, session_code: str, sprint_id: str, participant_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return a single participant's sprint question vote, or None."""
        ...

    def get_all_engineer_votes(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Return all sprint question votes for a session/sprint."""
        ...

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
        """Upsert a head-to-head vote."""
        ...

    def get_head_to_head_votes(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Return all head-to-head votes for a session/sprint."""
        ...

    def calculate_and_store_scores(self, session_code: str, sprint_id: str) -> None:
        """Compute and persist scores for a sprint."""
        ...

    def get_scores(self, session_code: str) -> List[Dict[str, Any]]:
        """Return scores grouped by participant with totalPoints and breakdown."""
        ...

    def set_sprint_question_phase(self, session_code: str, phase: str) -> None:
        """Set the sprint question phase."""
        ...

    def get_sprint_question_phase(self, session_code: str) -> str:
        """Return the current sprint question phase, defaulting to 'inactive'."""
        ...

    def set_scoreboard_visible(self, session_code: str, visible: bool) -> None:
        """Update scoreboard visibility and increment version."""
        ...

    # ==================== Role PINs ====================

    def generate_role_pins(self, session_code: str) -> int:
        """Generate unique PINs for all player roles. Returns count generated."""
        ...

    def validate_role_pin(self, session_code: str, pin: str) -> Optional[RolePin]:
        """Look up a PIN in the session. Returns RolePin if found, None otherwise."""
        ...

    def claim_role_pin(
        self, session_code: str, role_id: str, participant_id: str, participant_name: str
    ) -> bool:
        """Mark a role as claimed by a participant. Returns True on success."""
        ...

    def release_role_pin(self, session_code: str, participant_id: str) -> bool:
        """Release a role claim when a player is removed. Returns True if released."""
        ...

    def get_role_pins(self, session_code: str) -> List[RolePin]:
        """Get all role PINs for a session with claim status."""
        ...

    def get_role_pin_count(self, session_code: str) -> int:
        """Return total count of role PINs for a session."""
        ...

    def get_unclaimed_role_count(self, session_code: str) -> int:
        """Return count of unclaimed roles."""
        ...

    def get_unclaimed_roles(self, session_code: str) -> List[Dict[str, Any]]:
        """Return unclaimed roles with their details (id, name, description)."""
        ...

    # ==================== Scenario CRUD ====================

    def list_scenarios(self, include_deleted: bool = False) -> List[ScenarioVersion]:
        """List all scenarios (latest versions only by default)."""
        ...

    def get_scenario(self, scenario_id: str, version: Optional[int] = None) -> Optional[ScenarioVersion]:
        """Get a scenario by ID. If version is None, returns the latest version."""
        ...

    def get_scenario_history(self, scenario_id: str) -> List[ScenarioVersion]:
        """Get all versions of a scenario, newest first."""
        ...

    def create_scenario(self, data: Dict[str, Any], created_by: Optional[str] = None) -> ScenarioVersion:
        """Create a new scenario from JSON data."""
        ...

    def update_scenario(
        self, scenario_id: str, data: Dict[str, Any], created_by: Optional[str] = None
    ) -> Optional[ScenarioVersion]:
        """Update a scenario by creating a new version. Returns None if not found."""
        ...

    def delete_scenario(self, scenario_id: str) -> bool:
        """Soft-delete a scenario."""
        ...

    def restore_scenario(self, scenario_id: str) -> bool:
        """Restore a soft-deleted scenario."""
        ...

    def enable_scenario(self, scenario_id: str) -> bool:
        """Enable a scenario for use in session creation."""
        ...

    def disable_scenario(self, scenario_id: str) -> bool:
        """Disable a scenario from being used in session creation."""
        ...

    def diff_scenario(self, scenario_id: str, new_data: Dict[str, Any]) -> Dict[str, Any]:
        """Compare new data against the current version and return differences."""
        ...

    # ==================== Tutorial Administration ====================

    def list_tutorials(self) -> List[TutorialVersion]: ...

    def get_tutorial(self, tutorial_id: str, version: Optional[int] = None) -> Optional[TutorialVersion]: ...

    def get_published_tutorial(self, tutorial_id: str) -> Optional[TutorialVersion]: ...

    def get_tutorial_activation(self) -> TutorialActivation: ...

    def activate_tutorial(self, tutorial_id: str,
                          expected_activation_revision: int) -> TutorialActivation: ...

    def get_tutorial_history(self, tutorial_id: str) -> List[TutorialVersion]: ...

    def create_tutorial(self, config: Dict[str, Any], created_by: Optional[str] = None) -> TutorialVersion: ...

    def update_tutorial(self, tutorial_id: str, config: Dict[str, Any], expected_revision: int,
                        created_by: Optional[str] = None) -> Optional[TutorialVersion]: ...

    def publish_tutorial(self, tutorial_id: str, version: int,
                         expected_revision: int) -> Optional[TutorialVersion]: ...

    def disable_tutorial(self, tutorial_id: str,
                         expected_revision: int) -> Optional[TutorialVersion]: ...

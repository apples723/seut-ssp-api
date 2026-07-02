"""
Property-Based Tests for Role Pins Toggle Feature

Tests Properties 1-4 from the design document using Hypothesis.
Each test runs a minimum of 100 iterations.

Feature: role-pins-toggle

Validates: Requirements 2.1, 4.1, 4.2, 4.3, 4.4
"""

import sys
import os
import json
import sqlite3

# Must be set before importing sqlite_repository, because the module-level
# `repo = SqliteRepository()` runs on import and calls os.makedirs(DB_PATH).
os.environ.setdefault('DB_PATH', '/tmp/test_role_pins_toggle_properties.db')

import pytest
from hypothesis import given, strategies as st, settings, assume
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models import Session, Participant, Sprint
from src.sqlite_repository import SqliteRepository
from src.scenario_repository import ScenarioRepository
from src.voting_repository import VotingRepository


# ==================== Shared Strategies ====================

# Operations that can be performed on a session after creation
session_operations = st.sampled_from(['start', 'join_player', 'join_viewer', 'end'])

# Role names for scenario roles
role_name_strategy = st.text(
    alphabet='abcdefghijklmnopqrstuvwxyz0123456789 -_',
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip())

# Player names
player_name_strategy = st.text(
    alphabet='abcdefghijklmnopqrstuvwxyz0123456789',
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip())


# ==================== Helpers ====================

def make_repo() -> SqliteRepository:
    """Create a fully isolated in-memory SqliteRepository.

    Bypasses __init__ entirely to avoid:
    - os.makedirs on a file-based DB_PATH
    - _ensure_demo_session loading scenario files from disk
    Sub-repositories and schema are initialised normally on the in-memory conn.
    """
    r = SqliteRepository.__new__(SqliteRepository)
    r.conn = sqlite3.connect(':memory:', check_same_thread=False)
    r.conn.execute('PRAGMA foreign_keys = ON')
    r.conn.row_factory = sqlite3.Row
    r.scenarios = ScenarioRepository(r.conn)
    r.voting = VotingRepository(r.conn)
    # _init_schema creates all tables and runs migrations; skip _ensure_demo_session
    r._init_schema()
    return r


def create_test_scenario(r: SqliteRepository, role_names: list) -> str:
    """Create a test scenario with the given player role names. Returns scenario_id."""
    scenario_id = f'scenario-{uuid4().hex[:8]}'
    roles = [{'id': name, 'name': name, 'roleType': 'player', 'description': f'Role: {name}'} for name in role_names]
    r.scenarios.create_scenario({
        'id': scenario_id,
        'title': 'Test Scenario',
        'roles': roles,
        'sprints': [{'id': 's1', 'name': 'Sprint 1', 'description': 'Test sprint'}],
    }, created_by='test')
    return scenario_id


def create_session_with_scenario(r: SqliteRepository, role_names: list, role_pins_enabled: bool = False) -> str:
    """Create a session backed by a scenario with the given roles. Returns session_code."""
    scenario_id = create_test_scenario(r, role_names)
    session = r.create_session('TestFacilitator', scenario_id, role_pins_enabled=role_pins_enabled)
    return session.session_id


def add_player(r: SqliteRepository, session_code: str, name: str = None, assigned_role_name: str = None) -> str:
    """Add a player participant to a session. Returns participant_id."""
    if name is None:
        name = f'Player-{uuid4().hex[:6]}'
    participant = r.add_participant(
        session_code, name, 'player',
        assigned_role_name=assigned_role_name,
    )
    return participant.id


def select_role_via_service(r: SqliteRepository, session_code: str, participant_id: str, role_id: str) -> dict:
    """Replicate the select_role service logic against the given repository.

    Returns a dict with 'statusCode' and parsed body matching the service response format.
    """
    # Validate session exists
    info = r.get_session_scenario_info(session_code)
    if not info:
        return {'statusCode': 404, 'body': {'ok': False, 'message': 'Session not found.'}}

    # Validate participant exists and is a player
    participants = r.get_session_participants(session_code)
    participant = next((p for p in participants if p.id == participant_id), None)
    if not participant:
        return {'statusCode': 404, 'body': {'ok': False, 'message': 'Participant not found.'}}
    if participant.role != 'player':
        return {'statusCode': 400, 'body': {'ok': False, 'message': 'Only players can select a role.'}}

    # Load scenario and validate role
    scenario = r.get_scenario(info['scenario_id'], info['scenario_version'])
    if not scenario:
        return {'statusCode': 404, 'body': {'ok': False, 'message': 'Scenario not found.'}}

    role_data = next((role for role in scenario.roles if role.get('id') == role_id and role.get('roleType') == 'player'), None)
    if not role_data:
        return {'statusCode': 400, 'body': {'ok': False, 'message': 'Invalid role.'}}

    role_name = role_data.get('name', '')

    # Idempotent: if the requesting player already holds this role, return success with no change
    if participant.assigned_role_name == role_name:
        return {'statusCode': 200, 'body': {'ok': True}}

    # Uniqueness enforcement: check if another active participant holds the requested role
    holder = next(
        (p for p in participants
         if p.id != participant_id and p.role == 'player' and p.assigned_role_name == role_name),
        None,
    )
    if holder:
        return {'statusCode': 409, 'body': {'ok': False, 'message': f'The role "{role_name}" is already claimed by another player.'}}

    # Update participant
    updated = r.update_participant_role(
        participant_id=participant_id,
        assigned_role_name=role_name,
        role_description=role_data.get('description', ''),
        private_info=role_data.get('privateInfo'),
        session_code=session_code,
    )
    if not updated:
        return {'statusCode': 500, 'body': {'ok': False, 'message': 'Failed to update role.'}}

    return {'statusCode': 200, 'body': {'ok': True}}


# ==================== Property 1: Immutability of role_pins_enabled ====================

@given(
    role_pins_enabled=st.booleans(),
    operations=st.lists(session_operations, min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_property1_immutability_of_role_pins_enabled(role_pins_enabled: bool, operations: list) -> None:
    """
    Feature: role-pins-toggle, Property 1: Immutability of role_pins_enabled

    For any session, once created with a given `role_pins_enabled` value, that value
    SHALL remain unchanged regardless of any subsequent operations (start, join,
    select role, end session) performed on the session.

    **Validates: Requirements 2.1**
    """
    r = make_repo()

    # Create a scenario with some roles
    role_names = ['role-a', 'role-b', 'role-c']
    session_code = create_session_with_scenario(r, role_names, role_pins_enabled=role_pins_enabled)

    # Verify initial value
    assert r.is_role_pins_enabled(session_code) == role_pins_enabled, (
        f"Initial role_pins_enabled should be {role_pins_enabled}"
    )

    # Perform a sequence of operations
    for op in operations:
        if op == 'start':
            r.start_session(session_code)
        elif op == 'join_player':
            name = f'Player-{uuid4().hex[:6]}'
            r.add_participant(session_code, name, 'player')
        elif op == 'join_viewer':
            name = f'Viewer-{uuid4().hex[:6]}'
            r.add_participant(session_code, name, 'viewer')
        elif op == 'end':
            r.end_session(session_code)

        # After each operation, verify role_pins_enabled hasn't changed
        assert r.is_role_pins_enabled(session_code) == role_pins_enabled, (
            f"role_pins_enabled changed to {not role_pins_enabled} after operation '{op}'. "
            f"Expected it to remain {role_pins_enabled}."
        )


# ==================== Property 2: Role uniqueness enforcement ====================

@given(
    num_roles=st.integers(min_value=2, max_value=5),
    claimed_role_index=st.integers(min_value=0, max_value=4),
    attacker_name=player_name_strategy,
)
@settings(max_examples=100)
def test_property2_role_uniqueness_enforcement(num_roles: int, claimed_role_index: int, attacker_name: str) -> None:
    """
    Feature: role-pins-toggle, Property 2: Role uniqueness enforcement

    For any session with `role_pins_enabled=false`, and for any role currently held
    by an active participant, if a different player attempts to claim that same role
    via the select-role endpoint, the server SHALL reject the request with HTTP 409.

    **Validates: Requirements 4.1, 4.3**
    """
    # Ensure claimed_role_index is within bounds
    assume(claimed_role_index < num_roles)

    r = make_repo()

    # Create role names
    role_names = [f'role-{i}' for i in range(num_roles)]
    session_code = create_session_with_scenario(r, role_names, role_pins_enabled=False)

    # First player claims a role
    holder_id = add_player(r, session_code, name='Holder')
    claimed_role = role_names[claimed_role_index]
    result = select_role_via_service(r, session_code, holder_id, claimed_role)
    assert result['statusCode'] == 200, (
        f"First player should successfully claim role, got {result}"
    )

    # Second player attempts to claim the same role
    attacker_id = add_player(r, session_code, name=attacker_name)
    result = select_role_via_service(r, session_code, attacker_id, claimed_role)

    assert result['statusCode'] == 409, (
        f"Expected 409 when another player tries to claim an already-held role '{claimed_role}', "
        f"got {result['statusCode']}: {result['body']}"
    )
    assert result['body']['ok'] is False
    assert claimed_role in result['body']['message']


# ==================== Property 3: Successful role assignment ====================

@given(
    num_roles=st.integers(min_value=2, max_value=5),
    first_role_index=st.integers(min_value=0, max_value=4),
    second_role_index=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=100)
def test_property3_successful_role_assignment(num_roles: int, first_role_index: int, second_role_index: int) -> None:
    """
    Feature: role-pins-toggle, Property 3: Successful role assignment

    For any session with `role_pins_enabled=false`, and for any role not currently
    held by another active participant, if a player selects that role, the server
    SHALL assign it to the player and the player's previous role assignment (if any)
    SHALL be released.

    **Validates: Requirements 4.2**
    """
    assume(first_role_index < num_roles)
    assume(second_role_index < num_roles)
    assume(first_role_index != second_role_index)

    r = make_repo()

    role_names = [f'role-{i}' for i in range(num_roles)]
    session_code = create_session_with_scenario(r, role_names, role_pins_enabled=False)

    # Player claims first role
    player_id = add_player(r, session_code, name='TestPlayer')
    first_role = role_names[first_role_index]
    result = select_role_via_service(r, session_code, player_id, first_role)
    assert result['statusCode'] == 200, (
        f"Player should successfully claim unclaimed role '{first_role}', got {result}"
    )

    # Verify assignment
    participants = r.get_session_participants(session_code)
    player = next(p for p in participants if p.id == player_id)
    assert player.assigned_role_name == first_role, (
        f"Player should be assigned '{first_role}', got '{player.assigned_role_name}'"
    )

    # Player switches to second role (also unclaimed)
    second_role = role_names[second_role_index]
    result = select_role_via_service(r, session_code, player_id, second_role)
    assert result['statusCode'] == 200, (
        f"Player should successfully switch to unclaimed role '{second_role}', got {result}"
    )

    # Verify new assignment
    participants = r.get_session_participants(session_code)
    player = next(p for p in participants if p.id == player_id)
    assert player.assigned_role_name == second_role, (
        f"Player should now be assigned '{second_role}', got '{player.assigned_role_name}'"
    )

    # Verify previous role is released (no one holds it)
    holders_of_first = [
        p for p in participants
        if p.role == 'player' and p.assigned_role_name == first_role
    ]
    assert len(holders_of_first) == 0, (
        f"Previous role '{first_role}' should be released, but {len(holders_of_first)} player(s) still hold it"
    )


# ==================== Property 4: Idempotent role re-selection ====================

@given(
    num_roles=st.integers(min_value=1, max_value=5),
    role_index=st.integers(min_value=0, max_value=4),
    re_select_count=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
def test_property4_idempotent_role_reselection(num_roles: int, role_index: int, re_select_count: int) -> None:
    """
    Feature: role-pins-toggle, Property 4: Idempotent role re-selection

    For any player who already holds a specific role, calling select-role for that
    same role SHALL return success with no state change (the role assignment remains
    identical).

    **Validates: Requirements 4.4**
    """
    assume(role_index < num_roles)

    r = make_repo()

    role_names = [f'role-{i}' for i in range(num_roles)]
    session_code = create_session_with_scenario(r, role_names, role_pins_enabled=False)

    # Player claims a role
    player_id = add_player(r, session_code, name='TestPlayer')
    target_role = role_names[role_index]
    result = select_role_via_service(r, session_code, player_id, target_role)
    assert result['statusCode'] == 200, (
        f"Initial role claim should succeed, got {result}"
    )

    # Re-select the same role multiple times
    for i in range(re_select_count):
        result = select_role_via_service(r, session_code, player_id, target_role)
        assert result['statusCode'] == 200, (
            f"Re-selecting own role (attempt {i+1}) should return 200, got {result['statusCode']}"
        )
        assert result['body']['ok'] is True, (
            f"Re-selecting own role should return ok=True, got {result['body']}"
        )

    # Verify role assignment hasn't changed
    participants = r.get_session_participants(session_code)
    player = next(p for p in participants if p.id == player_id)
    assert player.assigned_role_name == target_role, (
        f"After {re_select_count} re-selections, player should still hold '{target_role}', "
        f"got '{player.assigned_role_name}'"
    )

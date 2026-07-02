"""
Property-Based Tests for Agile Ethics Scoring Backend

Tests Properties 1-6 from the design document using Hypothesis.
Each test runs a minimum of 100 iterations.

Validates: Requirements 1.6, 2.8, 4.1, 4.6, 4.11, 14.1, 14.2, 14.5, 14.7, 14.9
"""

import sys
import os
import sqlite3

# Must be set before importing sqlite_repository, because the module-level
# `repo = SqliteRepository()` runs on import and calls os.makedirs(DB_PATH).
os.environ.setdefault('DB_PATH', '/tmp/test_scoring_properties.db')

import pytest
from hypothesis import given, strategies as st, settings, assume
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models import Sprint, SprintQuestion, HeadToHead, Session, Participant
from src.sqlite_repository import SqliteRepository
from src.scenario_repository import ScenarioRepository
from src.voting_repository import VotingRepository


# ==================== Shared Strategies ====================

non_empty_text = st.text(min_size=1, max_size=200).filter(lambda s: s.strip())

choices_strategy = st.lists(non_empty_text, min_size=2, max_size=6)

sprint_question_strategy = st.builds(
    SprintQuestion,
    prompt=non_empty_text,
    choices=choices_strategy,
)

head_to_head_strategy = st.builds(
    HeadToHead,
    prompt=non_empty_text,
    role_a=non_empty_text,
    role_b=non_empty_text,
)


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


def make_sprint(sprint_question=None, head_to_head=None) -> Sprint:
    return Sprint(
        id=f's-{uuid4().hex[:8]}',
        name='Test Sprint',
        description='A test sprint',
        timer_enabled=False,
        timer_seconds=None,
        response_boxes=None,
        sprint_question=sprint_question,
        head_to_head=head_to_head,
    )


def make_participant(role='player', assigned_role_name=None) -> Participant:
    return Participant(
        id=str(uuid4()),
        name=f'User-{uuid4().hex[:6]}',
        role=role,
        assigned_role_name=assigned_role_name,
    )


def make_session(sprint: Sprint, phase: str = 'inactive', scoreboard_visible: bool = False,
                 scores=None, engineer_vote_choice=None) -> Session:
    participant = make_participant()
    return Session(
        session_id=f'TEST-{uuid4().hex[:4].upper()}',
        scenario_name='Test Scenario',
        current_sprint_index=0,
        total_sprints=1,
        timer_visible=False,
        timer_seconds=None,
        participant=participant,
        current_sprint=sprint,
        sprint_question_phase=phase,
        scoreboard_visible=scoreboard_visible,
        engineer_vote_choice=engineer_vote_choice,
        scores=scores or [],
    )


# ==================== Property 1: SprintQuestion Serialization Round-Trip ====================

@given(sq=sprint_question_strategy)
@settings(max_examples=100)
def test_property1_sprint_question_round_trip(sq: SprintQuestion) -> None:
    """
    Property 1: SprintQuestion serialization round-trip.

    For any SprintQuestion with an arbitrary non-empty prompt and a list of
    two or more non-empty choice strings, serializing to dict and deserializing
    back SHALL produce an object equal to the original.

    Validates: Requirement 1.6
    """
    r = make_repo()
    sprint = make_sprint(sprint_question=sq)

    serialized = r._sprint_to_dict(sprint)
    deserialized = r._dict_to_sprint(serialized)

    assert deserialized.sprint_question is not None, (
        "Deserialized sprint should have a sprint_question"
    )
    assert deserialized.sprint_question.prompt == sq.prompt, (
        f"prompt mismatch: expected {sq.prompt!r}, got {deserialized.sprint_question.prompt!r}"
    )
    assert deserialized.sprint_question.choices == sq.choices, (
        f"choices mismatch: expected {sq.choices!r}, got {deserialized.sprint_question.choices!r}"
    )


@given(h2h=head_to_head_strategy)
@settings(max_examples=100)
def test_property1_head_to_head_round_trip(h2h: HeadToHead) -> None:
    """
    Property 1 (extended): HeadToHead serialization round-trip.

    Validates: Requirement 1.6
    """
    r = make_repo()
    sprint = make_sprint(head_to_head=h2h)

    serialized = r._sprint_to_dict(sprint)
    deserialized = r._dict_to_sprint(serialized)

    assert deserialized.head_to_head is not None
    assert deserialized.head_to_head.prompt == h2h.prompt
    assert deserialized.head_to_head.role_a == h2h.role_a
    assert deserialized.head_to_head.role_b == h2h.role_b


@given(sq=sprint_question_strategy, h2h=head_to_head_strategy)
@settings(max_examples=100)
def test_property1_sprint_with_both_fields_round_trip(sq: SprintQuestion, h2h: HeadToHead) -> None:
    """
    Property 1 (combined): Sprint with both sprintQuestion and headToHead round-trips correctly.

    Validates: Requirement 1.6
    """
    r = make_repo()
    sprint = make_sprint(sprint_question=sq, head_to_head=h2h)

    serialized = r._sprint_to_dict(sprint)
    deserialized = r._dict_to_sprint(serialized)

    assert deserialized.sprint_question == sq
    assert deserialized.head_to_head is not None
    assert deserialized.head_to_head.prompt == h2h.prompt
    assert deserialized.head_to_head.role_a == h2h.role_a
    assert deserialized.head_to_head.role_b == h2h.role_b


def test_property1_sprint_without_question_round_trips_as_none() -> None:
    """
    Property 1 (edge case): Sprint without sprintQuestion round-trips with sprint_question=None.
    """
    r = make_repo()
    sprint = make_sprint(sprint_question=None, head_to_head=None)

    serialized = r._sprint_to_dict(sprint)
    deserialized = r._dict_to_sprint(serialized)

    assert deserialized.sprint_question is None
    assert deserialized.head_to_head is None


# ==================== Property 2: Session.to_dict() Completeness ====================

@given(
    phase=st.sampled_from(['inactive', 'engineer_vote', 'discussion', 'stakeholder_vote', 'reveal']),
    scoreboard_visible=st.booleans(),
    engineer_vote_choice=st.one_of(st.none(), st.integers(min_value=0, max_value=5)),
    sq=st.one_of(st.none(), sprint_question_strategy),
    h2h=st.one_of(st.none(), head_to_head_strategy),
)
@settings(max_examples=100)
def test_property2_session_to_dict_contains_scoring_keys(
    phase: str,
    scoreboard_visible: bool,
    engineer_vote_choice,
    sq,
    h2h,
) -> None:
    """
    Property 2: Session.to_dict() includes all new scoring fields.

    For any Session with sprint_question_phase, scoreboard_visible, and
    engineer_vote_choice populated, calling to_dict() SHALL produce a dict
    containing the keys sprintPhase, scoreboardVisible, engineerVoteChoice,
    and scores. The currentSprint sub-dict SHALL contain sprintQuestion and
    headToHead when those fields are set on the sprint.

    Validates: Requirement 2.8
    """
    sprint = make_sprint(sprint_question=sq, head_to_head=h2h)
    session = make_session(
        sprint=sprint,
        phase=phase,
        scoreboard_visible=scoreboard_visible,
        engineer_vote_choice=engineer_vote_choice,
    )

    result = session.to_dict()

    # Top-level scoring keys must always be present
    assert 'sprintPhase' in result, "to_dict() must include 'sprintPhase'"
    assert 'scoreboardVisible' in result, "to_dict() must include 'scoreboardVisible'"
    assert 'engineerVoteChoice' in result, "to_dict() must include 'engineerVoteChoice'"
    assert 'scores' in result, "to_dict() must include 'scores'"

    # Values must match what was set
    assert result['sprintPhase'] == phase
    assert result['scoreboardVisible'] == scoreboard_visible
    assert result['engineerVoteChoice'] == engineer_vote_choice

    # currentSprint sub-dict must be present
    assert 'currentSprint' in result
    current = result['currentSprint']

    # sprintQuestion serialized correctly
    if sq is not None:
        assert current['sprintQuestion'] is not None, (
            "currentSprint.sprintQuestion should be set when sprint has a sprint_question"
        )
        assert current['sprintQuestion']['prompt'] == sq.prompt
        assert current['sprintQuestion']['choices'] == sq.choices
    else:
        assert current['sprintQuestion'] is None

    # headToHead serialized correctly
    if h2h is not None:
        assert current['headToHead'] is not None, (
            "currentSprint.headToHead should be set when sprint has a head_to_head"
        )
        assert current['headToHead']['prompt'] == h2h.prompt
        assert current['headToHead']['roleA'] == h2h.role_a
        assert current['headToHead']['roleB'] == h2h.role_b
    else:
        assert current['headToHead'] is None


@given(
    scores=st.lists(
        st.fixed_dictionaries({
            'participantId': st.uuids().map(str),
            'participantName': non_empty_text,
            'assignedRoleId': non_empty_text,
            'assignedRoleName': non_empty_text,
            'totalPoints': st.integers(min_value=0, max_value=20),
            'breakdown': st.just([]),
        }),
        min_size=0,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_property2_session_to_dict_scores_passthrough(scores) -> None:
    """
    Property 2 (scores): scores list is passed through to_dict() unchanged.

    Validates: Requirement 2.8
    """
    sprint = make_sprint()
    session = make_session(sprint=sprint, scores=scores)

    result = session.to_dict()

    assert result['scores'] == scores, (
        f"scores should pass through unchanged, expected {scores}, got {result['scores']}"
    )


# ==================== Property 3: Engineer Vote Upsert Idempotence ====================

@given(
    choice_sequence=st.lists(
        st.integers(min_value=0, max_value=3),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_property3_engineer_vote_upsert_idempotence(choice_sequence) -> None:
    """
    Property 3: Engineer vote upsert idempotence.

    For any (session_code, sprint_id, participant_id) triple, submitting
    multiple engineer votes with different choice_index values SHALL result
    in exactly one vote record, with the value of the most recent submission.

    Validates: Requirement 4.1
    """
    r = make_repo()

    # Create a minimal session row directly so we don't need a full scenario
    session_code = f'PROP-{uuid4().hex[:4].upper()}'
    sprint_id = f's-{uuid4().hex[:8]}'
    participant_id = str(uuid4())
    participant_name = 'TestPlayer'

    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_name, current_sprint_index, total_sprints,
            timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, 'Test', 0, 1, 1, 300,
          f'[{{"id": "{sprint_id}", "name": "S1", "description": "d", "timer_enabled": false}}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()

    # Submit votes in sequence — each overwrites the previous
    for choice_index in choice_sequence:
        r.submit_engineer_vote(
            session_code, sprint_id, participant_id, participant_name,
            choice_index, f'Choice {choice_index}'
        )

    # Assert exactly one row exists
    cursor.execute(
        'SELECT COUNT(*) as cnt FROM sprint_question_votes WHERE sprint_id = ? AND participant_id = ?',
        (sprint_id, participant_id)
    )
    count = cursor.fetchone()['cnt']
    assert count == 1, (
        f"Expected exactly 1 vote row, got {count} for choice_sequence={choice_sequence}"
    )

    # Assert the stored value is the last submitted choice
    cursor.execute(
        'SELECT choice_index FROM sprint_question_votes WHERE sprint_id = ? AND participant_id = ?',
        (sprint_id, participant_id)
    )
    stored = cursor.fetchone()['choice_index']
    expected = choice_sequence[-1]
    assert stored == expected, (
        f"Expected stored choice_index={expected} (last submitted), got {stored}"
    )


@given(
    num_participants=st.integers(min_value=2, max_value=5),
    choices=st.lists(st.integers(min_value=0, max_value=1), min_size=2, max_size=5),
)
@settings(max_examples=50)
def test_property3_multiple_participants_each_get_one_row(num_participants, choices) -> None:
    """
    Property 3 (multi-participant): Each participant gets exactly one vote row
    regardless of how many times they submit.

    Validates: Requirement 4.1
    """
    r = make_repo()
    session_code = f'PROP-{uuid4().hex[:4].upper()}'
    sprint_id = f's-{uuid4().hex[:8]}'

    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_name, current_sprint_index, total_sprints,
            timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, 'Test', 0, 1, 1, 300,
          f'[{{"id": "{sprint_id}", "name": "S1", "description": "d", "timer_enabled": false}}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()

    participant_ids = [str(uuid4()) for _ in range(num_participants)]

    # Each participant submits multiple votes
    for pid in participant_ids:
        for choice in choices:
            r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # Each participant should have exactly one row
    for pid in participant_ids:
        cursor.execute(
            'SELECT COUNT(*) as cnt FROM sprint_question_votes WHERE sprint_id = ? AND participant_id = ?',
            (sprint_id, pid)
        )
        count = cursor.fetchone()['cnt']
        assert count == 1, (
            f"Participant {pid[:8]} should have 1 vote row, got {count}"
        )


# ==================== Property 4: Score Computation Correctness ====================

def _setup_scoring_session(r: SqliteRepository, sprint_id: str) -> tuple:
    """Create a session with a facilitator and return session_code."""
    session_code = f'SCOR-{uuid4().hex[:4].upper()}'
    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_name, current_sprint_index, total_sprints,
            timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, 'Test', 0, 1, 1, 300,
          f'[{{"id": "{sprint_id}", "name": "S1", "description": "d", "timer_enabled": false}}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()
    return session_code


def _add_participant_direct(r: SqliteRepository, session_code: str, role: str,
                             assigned_role_name: str = None,
                             voter_type_id: str = None,
                             voter_type_name: str = None) -> str:
    """Insert a participant directly and return their ID."""
    pid = str(uuid4())
    name = f'{role.capitalize()}-{pid[:6]}'
    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO participants (id, session_id, name, role, assigned_role_name, voter_type_id, voter_type_name, joined_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (pid, session_code, name, role, assigned_role_name, voter_type_id, voter_type_name, '2024-01-01T00:00:00Z'))
    r.conn.commit()
    return pid


@given(
    # Generate vote counts for 3 groups (each group votes 0 or 1)
    group_a_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_b_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_c_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    player_choices=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_property4_score_computation_correctness(group_a_votes, group_b_votes, group_c_votes, player_choices) -> None:
    """
    Property 4: Score computation correctness with 2-of-3 group rule.

    Generate 3 groups of voters (each with a voter_type_id), compute expected
    group majorities, determine expected winning answer via 2-of-3 rule, assert
    engineer scores match engineers whose choice equals the winning answer.

    Validates: Requirements 4.6, 4.11, 14.1, 14.2, 14.5, 8.1
    """
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Set up voter types on the session so get_voter_types_for_session works
    import json
    voter_types_json = json.dumps([
        {'id': 'group-a', 'name': 'Group A'},
        {'id': 'group-b', 'name': 'Group B'},
        {'id': 'group-c', 'name': 'Group C'},
    ])
    cursor = r.conn.cursor()
    cursor.execute('UPDATE sessions SET voter_types_json = ? WHERE session_id = ?',
                   (voter_types_json, session_code))
    r.conn.commit()

    # Add voters for each group and record their votes
    groups = [
        ('group-a', 'Group A', group_a_votes),
        ('group-b', 'Group B', group_b_votes),
        ('group-c', 'Group C', group_c_votes),
    ]
    for vt_id, vt_name, votes in groups:
        for choice in votes:
            vid = _add_participant_direct(r, session_code, 'voter',
                                          voter_type_id=vt_id, voter_type_name=vt_name)
            r.submit_engineer_vote(session_code, sprint_id, vid, f'Voter-{vid[:4]}', choice, f'C{choice}')

    # Compute expected group majorities
    def group_majority(votes):
        tally = {}
        for c in votes:
            tally[c] = tally.get(c, 0) + 1
        max_count = max(tally.values())
        tied = [c for c, cnt in tally.items() if cnt == max_count]
        if len(tied) == 1:
            return tied[0]
        return None  # tied

    # Compute engineer majority (for tiebreaker)
    eng_tally = {}
    for c in player_choices:
        eng_tally[c] = eng_tally.get(c, 0) + 1
    eng_max = max(eng_tally.values()) if eng_tally else 0
    eng_tied = [c for c, cnt in eng_tally.items() if cnt == eng_max]
    engineer_majority = eng_tied[0] if len(eng_tied) == 1 else None

    group_positions = {}
    for vt_id, _, votes in groups:
        maj = group_majority(votes)
        if maj is not None:
            group_positions[vt_id] = maj
        elif engineer_majority is not None:
            group_positions[vt_id] = engineer_majority
        # else: group excluded (both tied)

    # Apply 2-of-3 rule
    choice_group_count = {}
    for vt_id, choice in group_positions.items():
        choice_group_count[choice] = choice_group_count.get(choice, 0) + 1

    winning_answer = None
    for choice, count in choice_group_count.items():
        if count >= 2:
            winning_answer = choice
            break

    # Fallback if fewer than 2 groups
    if winning_answer is None and len(group_positions) < 2:
        all_votes = group_a_votes + group_b_votes + group_c_votes
        overall_tally = {}
        for c in all_votes:
            overall_tally[c] = overall_tally.get(c, 0) + 1
        if overall_tally:
            winning_answer = max(overall_tally, key=lambda k: overall_tally[k])

    # Add players with distinct role names and record their votes
    player_ids = []
    for i, choice in enumerate(player_choices):
        role_name = f'Role-{i}'
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=role_name)
        player_ids.append((pid, choice, role_name))
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # Run score computation
    r.calculate_and_store_scores(session_code, sprint_id)

    # Fetch awarded engineer scores
    cursor = r.conn.cursor()
    cursor.execute(
        "SELECT participant_id FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%stakeholder group majority%'",
        (session_code, sprint_id)
    )
    awarded_pids = {row['participant_id'] for row in cursor.fetchall()}

    # Expected: players whose choice matched winning_answer
    if winning_answer is not None:
        expected_pids = {pid for pid, choice, _ in player_ids if choice == winning_answer}
    else:
        expected_pids = set()

    assert awarded_pids == expected_pids, (
        f"group_a={group_a_votes}, group_b={group_b_votes}, group_c={group_c_votes}, "
        f"player_choices={player_choices}, winning_answer={winning_answer}, "
        f"expected={expected_pids}, got={awarded_pids}"
    )


@given(
    player_choices=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
)
@settings(max_examples=50)
def test_property4_no_voter_votes_awards_no_sprint_question_points(player_choices) -> None:
    """
    Property 4 (edge case): When no voter votes exist, no sprint question points are awarded.
    This includes no engineer corporate choice points, no group scores, and no individual voter scores.

    Validates: Requirements 4.11, 14.5, 8.1
    """
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Add players only — no voters vote
    for i, choice in enumerate(player_choices):
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Role-{i}')
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # Run score computation — should not raise and should award no sprint question points
    r.calculate_and_store_scores(session_code, sprint_id)

    cursor = r.conn.cursor()
    # No engineer corporate choice scores
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%stakeholder group majority%'",
        (session_code, sprint_id)
    )
    count = cursor.fetchone()['cnt']
    assert count == 0, (
        f"No engineer corporate choice points should be awarded when no voter votes exist, got {count}"
    )

    # No group scores
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%Group majority%'",
        (session_code, sprint_id)
    )
    group_count = cursor.fetchone()['cnt']
    assert group_count == 0, (
        f"No group scores should be awarded when no voter votes exist, got {group_count}"
    )

    # No individual voter scores
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%Individual vote%'",
        (session_code, sprint_id)
    )
    voter_count = cursor.fetchone()['cnt']
    assert voter_count == 0, (
        f"No individual voter scores should be awarded when no voter votes exist, got {voter_count}"
    )


# ==================== Property 5: Head-to-Head Tiebreak Determinism ====================

def _setup_h2h_session(r: SqliteRepository, sprint_id: str, role_names: list) -> str:
    """Create a session backed by a minimal scenario that maps each role name to itself as an ID.

    calculate_and_store_scores builds role_name_to_id from the scenario's roles list.
    Without a scenario the map is empty, winning_role_name resolves to '' and no
    player is found.  Seeding a scenario with id==name for each role fixes this.
    """
    scenario_id = f'scenario-{uuid4().hex[:8]}'
    roles = [{'id': name, 'name': name} for name in role_names]
    r.scenarios.create_scenario({
        'id': scenario_id,
        'title': 'Test Scenario',
        'roles': roles,
        'sprints': [{'id': sprint_id, 'name': 'S1', 'description': 'd'}],
    }, created_by='test')

    session_code = f'H2H-{uuid4().hex[:4].upper()}'
    # Fetch the version that was just created
    scenario_version = r.scenarios.get_scenario(scenario_id)
    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_id, scenario_version, scenario_name,
            current_sprint_index, total_sprints, timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, scenario_id, scenario_version.version, 'Test', 0, 1, 1, 300,
          f'[{{"id": "{sprint_id}", "name": "S1", "description": "d", "timer_enabled": false}}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()
    return session_code


@given(
    role_a=st.text(alphabet='abcdefghijklmnopqrstuvwxyz0123456789-', min_size=1, max_size=20),
    role_b=st.text(alphabet='abcdefghijklmnopqrstuvwxyz0123456789-', min_size=1, max_size=20),
    votes_per_role=st.integers(min_value=1, max_value=6),
)
@settings(max_examples=100)
def test_property5_head_to_head_tiebreak_determinism(role_a, role_b, votes_per_role) -> None:
    """
    Property 5: Head-to-head tiebreak is deterministic.

    For any tied head-to-head vote distribution (equal counts for both roles),
    calculate_and_store_scores SHALL always award the point to the role with
    the lexicographically lower chosen_role_id, and SHALL produce the same
    result when called with the same inputs in any order.

    Validates: Requirement 14.7
    """
    assume(role_a != role_b)

    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    # Seed a scenario so calculate_and_store_scores can resolve role_id → role_name
    session_code = _setup_h2h_session(r, sprint_id, [role_a, role_b])

    expected_winner = min(role_a, role_b)

    # Add a player assigned to each role (role name == role id in our test scenario)
    _add_participant_direct(r, session_code, 'player', assigned_role_name=role_a)
    _add_participant_direct(r, session_code, 'player', assigned_role_name=role_b)

    # Add equal votes for both roles (tied)
    for _ in range(votes_per_role):
        vid_a = _add_participant_direct(r, session_code, 'voter')
        r.submit_head_to_head_vote(
            session_code, sprint_id, vid_a, f'Voter-{vid_a[:4]}',
            None, None, role_a
        )
        vid_b = _add_participant_direct(r, session_code, 'voter')
        r.submit_head_to_head_vote(
            session_code, sprint_id, vid_b, f'Voter-{vid_b[:4]}',
            None, None, role_b
        )

    r.calculate_and_store_scores(session_code, sprint_id)

    cursor = r.conn.cursor()
    cursor.execute(
        "SELECT assigned_role_name FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%head-to-head%'",
        (session_code, sprint_id)
    )
    rows = cursor.fetchall()

    assert len(rows) == 1, (
        f"Expected exactly 1 head-to-head score, got {len(rows)} "
        f"(role_a={role_a!r}, role_b={role_b!r}, votes_per_role={votes_per_role})"
    )
    winning_role = rows[0]['assigned_role_name']
    assert winning_role == expected_winner, (
        f"Tiebreak should award point to lexicographically lower role. "
        f"role_a={role_a!r}, role_b={role_b!r}, expected={expected_winner!r}, got={winning_role!r}"
    )


@given(
    roles=st.lists(
        st.text(alphabet='abcdefghijklmnopqrstuvwxyz0123456789-', min_size=1, max_size=20),
        min_size=2,
        max_size=4,
        unique=True,
    ),
    votes_per_role=st.integers(min_value=1, max_value=4),
)
@settings(max_examples=50)
def test_property5_tiebreak_consistent_across_calls(roles, votes_per_role) -> None:
    """
    Property 5 (consistency): Calling calculate_and_store_scores with the same
    tied inputs always produces the same winner.

    Validates: Requirement 14.7
    """
    winners = []
    for _ in range(2):
        r = make_repo()
        sprint_id = f's-{uuid4().hex[:8]}'
        session_code = _setup_h2h_session(r, sprint_id, roles)

        for role in roles:
            _add_participant_direct(r, session_code, 'player', assigned_role_name=role)

        # Equal votes for every role
        for role in roles:
            for _ in range(votes_per_role):
                vid = _add_participant_direct(r, session_code, 'voter')
                r.submit_head_to_head_vote(
                    session_code, sprint_id, vid, f'Voter-{vid[:4]}',
                    None, None, role
                )

        r.calculate_and_store_scores(session_code, sprint_id)

        cursor = r.conn.cursor()
        cursor.execute(
            "SELECT assigned_role_name FROM scores WHERE session_id = ? AND sprint_id = ? AND reason LIKE '%head-to-head%'",
            (session_code, sprint_id)
        )
        rows = cursor.fetchall()
        winners.append(rows[0]['assigned_role_name'] if rows else None)

    assert winners[0] == winners[1], (
        f"Tiebreak should be deterministic: got {winners[0]!r} then {winners[1]!r} "
        f"for roles={roles!r}"
    )


# ==================== Property 6: Score Totals Invariant ====================

@given(
    reveal_phases=st.lists(
        st.lists(
            st.fixed_dictionaries({
                'choice_index': st.integers(min_value=0, max_value=1),
                'is_voter': st.booleans(),
            }),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=3,
    )
)
@settings(max_examples=50)
def test_property6_score_totals_invariant(reveal_phases) -> None:
    """
    Property 6: Score totals invariant.

    For any session after one or more completed reveal phases, the sum of all
    Score.points records for a given participant_id SHALL equal the totalPoints
    value returned by get_scores() for that participant.

    Validates: Requirement 14.9
    """
    r = make_repo()

    # Create a session with multiple sprints (one per reveal phase)
    num_sprints = len(reveal_phases)
    sprint_ids = [f's-{uuid4().hex[:8]}' for _ in range(num_sprints)]
    session_code = f'INV-{uuid4().hex[:4].upper()}'

    sprints_json = [
        f'{{"id": "{sid}", "name": "Sprint {i+1}", "description": "d", "timer_enabled": false}}'
        for i, sid in enumerate(sprint_ids)
    ]
    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_name, current_sprint_index, total_sprints,
            timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, 'Test', 0, num_sprints, 1, 300,
          f'[{", ".join(sprints_json)}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()

    # Add a fixed set of players (reused across all sprints)
    num_players = 3
    player_ids = []
    for i in range(num_players):
        role_name = f'Role-{i}'
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=role_name)
        player_ids.append(pid)

    # Simulate each reveal phase
    for sprint_idx, phase_votes in enumerate(reveal_phases):
        sprint_id = sprint_ids[sprint_idx]

        for vote_spec in phase_votes:
            if vote_spec['is_voter']:
                # Voter votes on sprint question
                vid = _add_participant_direct(r, session_code, 'voter')
                r.submit_engineer_vote(
                    session_code, sprint_id, vid, f'Voter-{vid[:4]}',
                    vote_spec['choice_index'], f"C{vote_spec['choice_index']}"
                )
            else:
                # Player votes on sprint question (round-robin assignment)
                player_idx = len(phase_votes) % num_players
                pid = player_ids[player_idx]
                r.submit_engineer_vote(
                    session_code, sprint_id, pid, f'Player-{pid[:4]}',
                    vote_spec['choice_index'], f"C{vote_spec['choice_index']}"
                )

        # Reveal scores for this sprint
        r.calculate_and_store_scores(session_code, sprint_id)

    # Verify invariant: SUM(points) == totalPoints for each participant
    scores_list = r.get_scores(session_code)

    for score_entry in scores_list:
        pid = score_entry['participantId']
        reported_total = score_entry['totalPoints']
        breakdown = score_entry['breakdown']

        # Sum from breakdown
        breakdown_sum = sum(item['points'] for item in breakdown)

        # Sum directly from DB
        cursor.execute(
            'SELECT SUM(points) as total FROM scores WHERE session_id = ? AND participant_id = ?',
            (session_code, pid)
        )
        db_sum = cursor.fetchone()['total'] or 0

        assert reported_total == breakdown_sum, (
            f"totalPoints ({reported_total}) != sum of breakdown ({breakdown_sum}) "
            f"for participant {pid[:8]}"
        )
        assert reported_total == db_sum, (
            f"totalPoints ({reported_total}) != SUM(points) from DB ({db_sum}) "
            f"for participant {pid[:8]}"
        )


@given(
    point_batches=st.lists(
        st.integers(min_value=1, max_value=3),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_property6_direct_score_insertion_totals(point_batches) -> None:
    """
    Property 6 (direct): Directly inserted score rows aggregate correctly in get_scores().

    Validates: Requirement 14.9
    """
    r = make_repo()
    session_code = f'TOT-{uuid4().hex[:4].upper()}'
    sprint_id = f's-{uuid4().hex[:8]}'

    cursor = r.conn.cursor()
    cursor.execute('''
        INSERT INTO sessions (session_id, scenario_name, current_sprint_index, total_sprints,
            timer_visible, timer_seconds, sprints_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (session_code, 'Test', 0, 1, 1, 300,
          f'[{{"id": "{sprint_id}", "name": "S1", "description": "d", "timer_enabled": false}}]',
          '2024-01-01T00:00:00Z'))
    r.conn.commit()

    participant_id = str(uuid4())
    participant_name = 'TestPlayer'
    expected_total = sum(point_batches)

    # Insert score rows directly
    for i, points in enumerate(point_batches):
        cursor.execute('''
            INSERT INTO scores (id, session_id, participant_id, participant_name,
                assigned_role_id, assigned_role_name, sprint_id, points, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid4()), session_code, participant_id, participant_name,
              'role-1', 'Role One', f'{sprint_id}-{i}', points, 'Test reason',
              f'2024-01-01T00:00:0{i}Z'))
    r.conn.commit()

    scores_list = r.get_scores(session_code)

    assert len(scores_list) == 1, f"Expected 1 participant entry, got {len(scores_list)}"
    entry = scores_list[0]

    assert entry['totalPoints'] == expected_total, (
        f"totalPoints should be {expected_total}, got {entry['totalPoints']} "
        f"for point_batches={point_batches}"
    )
    assert len(entry['breakdown']) == len(point_batches), (
        f"breakdown should have {len(point_batches)} entries, got {len(entry['breakdown'])}"
    )
    assert sum(b['points'] for b in entry['breakdown']) == expected_total


# ==================== Double-Reveal Guard ====================

@given(
    group_a_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_b_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_c_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    player_choices=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_property_double_reveal_guard(group_a_votes, group_b_votes, group_c_votes, player_choices) -> None:
    """
    Double-reveal guard: calling calculate_and_store_scores twice for the same
    (session_id, sprint_id) pair SHALL produce the same number of score rows
    as calling it once. The second call is a no-op.
    This includes engineer scores, group scores, and individual voter scores.

    Validates: Requirements 1.1, 1.2, 1.3, 1.4, 8.4
    """
    import json
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Set up voter types on the session
    voter_types_json = json.dumps([
        {'id': 'group-a', 'name': 'Group A'},
        {'id': 'group-b', 'name': 'Group B'},
        {'id': 'group-c', 'name': 'Group C'},
    ])
    cursor = r.conn.cursor()
    cursor.execute('UPDATE sessions SET voter_types_json = ? WHERE session_id = ?',
                   (voter_types_json, session_code))
    r.conn.commit()

    # Add voters for each group and record their votes
    groups = [
        ('group-a', 'Group A', group_a_votes),
        ('group-b', 'Group B', group_b_votes),
        ('group-c', 'Group C', group_c_votes),
    ]
    for vt_id, vt_name, votes in groups:
        for choice in votes:
            vid = _add_participant_direct(r, session_code, 'voter',
                                          voter_type_id=vt_id, voter_type_name=vt_name)
            r.submit_engineer_vote(session_code, sprint_id, vid, f'Voter-{vid[:4]}', choice, f'C{choice}')

    # Add players and record their votes
    for i, choice in enumerate(player_choices):
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Role-{i}')
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # First call — computes and stores scores
    r.calculate_and_store_scores(session_code, sprint_id)

    cursor = r.conn.cursor()
    cursor.execute(
        'SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ?',
        (session_code, sprint_id))
    count_after_first = cursor.fetchone()['cnt']

    # Second call — should be a no-op due to the guard
    r.calculate_and_store_scores(session_code, sprint_id)

    cursor.execute(
        'SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ?',
        (session_code, sprint_id))
    count_after_second = cursor.fetchone()['cnt']

    assert count_after_first == count_after_second, (
        f"Double-reveal guard failed: first call produced {count_after_first} scores, "
        f"second call produced {count_after_second} scores."
    )


# ==================== Property: Group Scoring ====================

@given(
    group_a_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_b_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_c_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    player_choices=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_property_group_scoring(group_a_votes, group_b_votes, group_c_votes, player_choices) -> None:
    """
    Property: Group scoring correctness.

    For any vote distribution where 2+ groups agree, assert those groups receive
    score rows, and dissenting groups do not.

    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 8.2
    """
    import json
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Set up voter types on the session
    voter_types_json = json.dumps([
        {'id': 'group-a', 'name': 'Group A'},
        {'id': 'group-b', 'name': 'Group B'},
        {'id': 'group-c', 'name': 'Group C'},
    ])
    cursor = r.conn.cursor()
    cursor.execute('UPDATE sessions SET voter_types_json = ? WHERE session_id = ?',
                   (voter_types_json, session_code))
    r.conn.commit()

    # Add voters for each group
    groups = [
        ('group-a', 'Group A', group_a_votes),
        ('group-b', 'Group B', group_b_votes),
        ('group-c', 'Group C', group_c_votes),
    ]
    for vt_id, vt_name, votes in groups:
        for choice in votes:
            vid = _add_participant_direct(r, session_code, 'voter',
                                          voter_type_id=vt_id, voter_type_name=vt_name)
            r.submit_engineer_vote(session_code, sprint_id, vid, f'Voter-{vid[:4]}', choice, f'C{choice}')

    # Add players
    for i, choice in enumerate(player_choices):
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Role-{i}')
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # Compute expected group positions and winning answer
    def group_majority(votes):
        tally = {}
        for c in votes:
            tally[c] = tally.get(c, 0) + 1
        max_count = max(tally.values())
        tied = [c for c, cnt in tally.items() if cnt == max_count]
        if len(tied) == 1:
            return tied[0]
        return None

    eng_tally = {}
    for c in player_choices:
        eng_tally[c] = eng_tally.get(c, 0) + 1
    eng_max = max(eng_tally.values()) if eng_tally else 0
    eng_tied = [c for c, cnt in eng_tally.items() if cnt == eng_max]
    engineer_majority = eng_tied[0] if len(eng_tied) == 1 else None

    group_positions = {}
    for vt_id, _, votes in groups:
        maj = group_majority(votes)
        if maj is not None:
            group_positions[vt_id] = maj
        elif engineer_majority is not None:
            group_positions[vt_id] = engineer_majority

    choice_group_count = {}
    for vt_id, choice in group_positions.items():
        choice_group_count[choice] = choice_group_count.get(choice, 0) + 1

    winning_answer = None
    for choice, count in choice_group_count.items():
        if count >= 2:
            winning_answer = choice
            break

    if winning_answer is None and len(group_positions) < 2:
        all_votes = group_a_votes + group_b_votes + group_c_votes
        overall_tally = {}
        for c in all_votes:
            overall_tally[c] = overall_tally.get(c, 0) + 1
        if overall_tally:
            winning_answer = max(overall_tally, key=lambda k: overall_tally[k])

    # Run score computation
    r.calculate_and_store_scores(session_code, sprint_id)

    # Fetch group score rows
    cursor = r.conn.cursor()
    cursor.execute(
        "SELECT participant_id FROM scores WHERE session_id = ? AND sprint_id = ? AND reason = 'Group majority matched winning answer on corporate choice'",
        (session_code, sprint_id)
    )
    awarded_group_ids = {row['participant_id'] for row in cursor.fetchall()}

    # Expected: groups whose position matches winning_answer
    if winning_answer is not None:
        expected_group_ids = {vt_id for vt_id, choice in group_positions.items() if choice == winning_answer}
    else:
        expected_group_ids = set()

    assert awarded_group_ids == expected_group_ids, (
        f"group_a={group_a_votes}, group_b={group_b_votes}, group_c={group_c_votes}, "
        f"winning_answer={winning_answer}, group_positions={group_positions}, "
        f"expected_groups={expected_group_ids}, got={awarded_group_ids}"
    )


# ==================== Property: Individual Voter Scoring ====================

@given(
    group_a_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_b_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    group_c_votes=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
    player_choices=st.lists(st.integers(min_value=0, max_value=1), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_property_individual_voter_scoring(group_a_votes, group_b_votes, group_c_votes, player_choices) -> None:
    """
    Property: Individual voter scoring correctness.

    For any vote distribution, assert each voter whose choice_index matches the
    winning answer has exactly one score row with the correct reason.

    Validates: Requirements 6.1, 6.2, 6.3, 8.3
    """
    import json
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Set up voter types on the session
    voter_types_json = json.dumps([
        {'id': 'group-a', 'name': 'Group A'},
        {'id': 'group-b', 'name': 'Group B'},
        {'id': 'group-c', 'name': 'Group C'},
    ])
    cursor = r.conn.cursor()
    cursor.execute('UPDATE sessions SET voter_types_json = ? WHERE session_id = ?',
                   (voter_types_json, session_code))
    r.conn.commit()

    # Add voters for each group and track their IDs and choices
    all_voter_info = []  # (pid, choice, vt_id)
    groups = [
        ('group-a', 'Group A', group_a_votes),
        ('group-b', 'Group B', group_b_votes),
        ('group-c', 'Group C', group_c_votes),
    ]
    for vt_id, vt_name, votes in groups:
        for choice in votes:
            vid = _add_participant_direct(r, session_code, 'voter',
                                          voter_type_id=vt_id, voter_type_name=vt_name)
            r.submit_engineer_vote(session_code, sprint_id, vid, f'Voter-{vid[:4]}', choice, f'C{choice}')
            all_voter_info.append((vid, choice, vt_id))

    # Add players
    for i, choice in enumerate(player_choices):
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Role-{i}')
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', choice, f'C{choice}')

    # Compute expected winning answer
    def group_majority(votes):
        tally = {}
        for c in votes:
            tally[c] = tally.get(c, 0) + 1
        max_count = max(tally.values())
        tied = [c for c, cnt in tally.items() if cnt == max_count]
        if len(tied) == 1:
            return tied[0]
        return None

    eng_tally = {}
    for c in player_choices:
        eng_tally[c] = eng_tally.get(c, 0) + 1
    eng_max = max(eng_tally.values()) if eng_tally else 0
    eng_tied = [c for c, cnt in eng_tally.items() if cnt == eng_max]
    engineer_majority = eng_tied[0] if len(eng_tied) == 1 else None

    group_positions = {}
    for vt_id, _, votes in groups:
        maj = group_majority(votes)
        if maj is not None:
            group_positions[vt_id] = maj
        elif engineer_majority is not None:
            group_positions[vt_id] = engineer_majority

    choice_group_count = {}
    for vt_id, choice in group_positions.items():
        choice_group_count[choice] = choice_group_count.get(choice, 0) + 1

    winning_answer = None
    for choice, count in choice_group_count.items():
        if count >= 2:
            winning_answer = choice
            break

    if winning_answer is None and len(group_positions) < 2:
        all_votes = group_a_votes + group_b_votes + group_c_votes
        overall_tally = {}
        for c in all_votes:
            overall_tally[c] = overall_tally.get(c, 0) + 1
        if overall_tally:
            winning_answer = max(overall_tally, key=lambda k: overall_tally[k])

    # Run score computation
    r.calculate_and_store_scores(session_code, sprint_id)

    # Fetch individual voter score rows
    cursor = r.conn.cursor()
    cursor.execute(
        "SELECT participant_id FROM scores WHERE session_id = ? AND sprint_id = ? AND reason = 'Individual vote matched winning answer on corporate choice'",
        (session_code, sprint_id)
    )
    awarded_voter_ids = [row['participant_id'] for row in cursor.fetchall()]

    # Expected: voters whose choice matched winning_answer
    if winning_answer is not None:
        expected_voter_ids = [pid for pid, choice, _ in all_voter_info if choice == winning_answer]
    else:
        expected_voter_ids = []

    assert sorted(awarded_voter_ids) == sorted(expected_voter_ids), (
        f"group_a={group_a_votes}, group_b={group_b_votes}, group_c={group_c_votes}, "
        f"winning_answer={winning_answer}, "
        f"expected_voters={len(expected_voter_ids)}, got={len(awarded_voter_ids)}"
    )

    # Each awarded voter should have exactly one score row
    for vid in expected_voter_ids:
        count = awarded_voter_ids.count(vid)
        assert count == 1, (
            f"Voter {vid[:8]} should have exactly 1 individual score row, got {count}"
        )


# ==================== Property: Engineer Tiebreaker ====================

@given(
    tied_group_size=st.integers(min_value=1, max_value=4),
    engineer_choice=st.integers(min_value=0, max_value=1),
    num_engineers_majority=st.integers(min_value=2, max_value=5),
)
@settings(max_examples=100)
def test_property_engineer_tiebreaker(tied_group_size, engineer_choice, num_engineers_majority) -> None:
    """
    Property: Engineer tiebreaker correctness.

    When a group is internally tied (equal votes for both choices), the group's
    resolved position SHALL match the engineer majority.

    Validates: Requirements 3.1, 3.2, 8.5
    """
    import json
    r = make_repo()
    sprint_id = f's-{uuid4().hex[:8]}'
    session_code = _setup_scoring_session(r, sprint_id)

    # Set up voter types on the session
    voter_types_json = json.dumps([
        {'id': 'group-tied', 'name': 'Tied Group'},
        {'id': 'group-agree1', 'name': 'Agree Group 1'},
        {'id': 'group-agree2', 'name': 'Agree Group 2'},
    ])
    cursor = r.conn.cursor()
    cursor.execute('UPDATE sessions SET voter_types_json = ? WHERE session_id = ?',
                   (voter_types_json, session_code))
    r.conn.commit()

    # Create a tied group: equal votes for choice 0 and choice 1
    for _ in range(tied_group_size):
        vid0 = _add_participant_direct(r, session_code, 'voter',
                                        voter_type_id='group-tied', voter_type_name='Tied Group')
        r.submit_engineer_vote(session_code, sprint_id, vid0, f'Voter-{vid0[:4]}', 0, 'C0')
        vid1 = _add_participant_direct(r, session_code, 'voter',
                                        voter_type_id='group-tied', voter_type_name='Tied Group')
        r.submit_engineer_vote(session_code, sprint_id, vid1, f'Voter-{vid1[:4]}', 1, 'C1')

    # Create two agreeing groups that both choose engineer_choice (to ensure a winning answer)
    for vt_id, vt_name in [('group-agree1', 'Agree Group 1'), ('group-agree2', 'Agree Group 2')]:
        vid = _add_participant_direct(r, session_code, 'voter',
                                      voter_type_id=vt_id, voter_type_name=vt_name)
        r.submit_engineer_vote(session_code, sprint_id, vid, f'Voter-{vid[:4]}', engineer_choice, f'C{engineer_choice}')

    # Create engineers with a clear majority for engineer_choice
    other_choice = 1 - engineer_choice
    for i in range(num_engineers_majority):
        pid = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Eng-{i}')
        r.submit_engineer_vote(session_code, sprint_id, pid, f'Player-{pid[:4]}', engineer_choice, f'C{engineer_choice}')
    # Add 1 engineer voting the other way to ensure it's not unanimous but still a clear majority
    pid_other = _add_participant_direct(r, session_code, 'player', assigned_role_name=f'Eng-other')
    r.submit_engineer_vote(session_code, sprint_id, pid_other, f'Player-{pid_other[:4]}', other_choice, f'C{other_choice}')

    # Run score computation
    r.calculate_and_store_scores(session_code, sprint_id)

    # The tied group should be resolved by engineer majority (engineer_choice)
    # Since the two agreeing groups also chose engineer_choice, the winning answer is engineer_choice
    # The tied group's resolved position should also be engineer_choice (via tiebreaker)
    # So the tied group should receive a group score
    cursor = r.conn.cursor()
    cursor.execute(
        "SELECT participant_id FROM scores WHERE session_id = ? AND sprint_id = ? AND reason = 'Group majority matched winning answer on corporate choice'",
        (session_code, sprint_id)
    )
    awarded_group_ids = {row['participant_id'] for row in cursor.fetchall()}

    # All 3 groups should have scored: tied group resolved to engineer_choice, other 2 groups chose engineer_choice
    assert 'group-tied' in awarded_group_ids, (
        f"Tied group should be resolved by engineer majority ({engineer_choice}) and receive a score. "
        f"Awarded groups: {awarded_group_ids}"
    )
    assert 'group-agree1' in awarded_group_ids, "Agree Group 1 should receive a score"
    assert 'group-agree2' in awarded_group_ids, "Agree Group 2 should receive a score"

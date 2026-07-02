"""Sprint question voting and scoring service."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


VALID_PHASES = ('inactive', 'engineer_vote', 'discussion', 'stakeholder_vote', 'reveal')


def submit_engineer_sprint_vote(event: Dict[str, Any]) -> Dict[str, Any]:
    """Player submits their sprint question vote during the engineer_vote phase."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    choice_index = body.get('choiceIndex')
    choice_text = (body.get('choiceText') or '').strip()

    if not session_code or not participant_id or choice_index is None or not choice_text:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate phase
    phase = repo.get_sprint_question_phase(session_code)
    if phase != 'engineer_vote':
        return response(400, {'ok': False, 'message': 'Engineer voting is not currently open.'})

    # Validate participant is a player
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if caller.role != 'player':
        return response(403, {'ok': False, 'message': 'Only players can submit engineer votes.'})

    # Reject duplicate votes (players may not change their vote)
    sprint_id = session.current_sprint.id
    existing = repo.get_engineer_vote(session_code, sprint_id, participant_id)
    if existing:
        return response(409, {'ok': False, 'message': 'You have already voted on this sprint question.'})

    repo.submit_engineer_vote(session_code, sprint_id, participant_id, caller.name,
                              int(choice_index), choice_text)
    return response(200, {'ok': True})


def submit_stakeholder_sprint_vote(event: Dict[str, Any]) -> Dict[str, Any]:
    """Voter (stakeholder) submits their sprint question vote during the stakeholder_vote phase."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    participant_name = (body.get('participantName') or '').strip()
    choice_index = body.get('choiceIndex')
    choice_text = (body.get('choiceText') or '').strip()

    if not session_code or not participant_id or not participant_name or choice_index is None or not choice_text:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate phase
    phase = repo.get_sprint_question_phase(session_code)
    if phase != 'stakeholder_vote':
        return response(400, {'ok': False, 'message': 'Stakeholder voting is not currently open.'})

    # Validate participant is a voter
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if caller.role != 'voter':
        return response(403, {'ok': False, 'message': 'Only voters can submit stakeholder votes.'})

    sprint_id = session.current_sprint.id
    # Stakeholder votes use upsert (voters may change their vote before phase closes)
    repo.submit_engineer_vote(session_code, sprint_id, participant_id, participant_name,
                              int(choice_index), choice_text,
                              event_name='stakeholder_vote_submitted')
    return response(200, {'ok': True})


def submit_head_to_head_vote(event: Dict[str, Any]) -> Dict[str, Any]:
    """Voter submits a head-to-head vote during the stakeholder_vote phase."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    participant_name = (body.get('participantName') or '').strip()
    voter_type_id = (body.get('voterTypeId') or '').strip() or None
    voter_type_name = (body.get('voterTypeName') or '').strip() or None
    chosen_role_id = (body.get('chosenRoleId') or '').strip()

    if not session_code or not participant_id or not participant_name or not chosen_role_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate phase
    phase = repo.get_sprint_question_phase(session_code)
    if phase != 'stakeholder_vote':
        return response(400, {'ok': False, 'message': 'Stakeholder voting is not currently open.'})

    # Validate participant is a voter
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if caller.role != 'voter':
        return response(403, {'ok': False, 'message': 'Only voters can submit head-to-head votes.'})

    sprint_id = session.current_sprint.id
    repo.submit_head_to_head_vote(session_code, sprint_id, participant_id, participant_name,
                                  voter_type_id, voter_type_name, chosen_role_id)
    return response(200, {'ok': True})


def facilitator_set_phase(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator advances the sprint question phase."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    phase = (body.get('phase') or '').strip()

    if not session_code or not participant_id or not phase:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    if phase not in VALID_PHASES:
        return response(400, {'ok': False, 'message': f"Invalid phase. Must be one of: {', '.join(VALID_PHASES)}"})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate participant is a facilitator
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Facilitator role required.'})

    repo.set_sprint_question_phase(session_code, phase)
    return response(200, {'ok': True})


def get_sprint_question_status(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return current phase, vote counts, and vote details for the active sprint."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not participant_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Determine caller role for visibility rules
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    is_facilitator = caller is not None and caller.role == 'facilitator'

    sprint_id = session.current_sprint.id
    phase = repo.get_sprint_question_phase(session_code)

    engineer_votes = repo.get_all_engineer_votes(session_code, sprint_id)
    h2h_votes = repo.get_head_to_head_votes(session_code, sprint_id)

    # Hide individual choice details from non-facilitators until reveal
    if not is_facilitator and phase != 'reveal':
        engineer_votes_out = [
            {'participantId': v['participantId'], 'participantName': v['participantName']}
            for v in engineer_votes
        ]
        h2h_votes_out = [
            {'participantId': v['participantId'], 'participantName': v['participantName']}
            for v in h2h_votes
        ]
    else:
        engineer_votes_out = engineer_votes
        h2h_votes_out = h2h_votes

    return response(200, {
        'ok': True,
        'phase': phase,
        'sprintId': sprint_id,
        'engineerVoteCount': len(engineer_votes),
        'engineerVotes': engineer_votes_out,
        'headToHeadVoteCount': len(h2h_votes),
        'headToHeadVotes': h2h_votes_out,
    })


def get_session_scores(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return all score records for a session, grouped by participant."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    scores = repo.get_scores(session_code)
    return response(200, {'ok': True, 'scores': scores})


def facilitator_reveal_scores(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator triggers score computation, sets phase to reveal, and makes scoreboard visible."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not participant_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate participant is a facilitator
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Facilitator role required.'})

    sprint_id = session.current_sprint.id

    # Compute and persist scores for the current sprint
    repo.calculate_and_store_scores(session_code, sprint_id)

    # Only set phase if not already in reveal
    current_phase = repo.get_sprint_question_phase(session_code)
    if current_phase != 'reveal':
        repo.set_sprint_question_phase(session_code, 'reveal')
        repo.set_scoreboard_visible(session_code, True)

    scores = repo.get_scores(session_code)
    return response(200, {'ok': True, 'scores': scores})


def facilitator_toggle_scoreboard(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator shows or hides the scoreboard for all participants."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    visible = body.get('visible')

    if not session_code or not participant_id or visible is None:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate participant is a facilitator
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Facilitator role required.'})

    repo.set_scoreboard_visible(session_code, bool(visible))
    return response(200, {'ok': True})

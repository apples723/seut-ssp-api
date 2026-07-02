"""Response voting service: vote, retract, control, remove, summary."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


def get_session_voter_types(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return the voter types configured for a session."""
    params = event.get('queryStringParameters') or {}
    session_code = (params.get('code') or '').strip().upper()
    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})
    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})
    voter_types = repo.get_voter_types_for_session(session_code)
    return response(200, {
        'ok': True,
        'voterTypes': [{'id': vt.id, 'name': vt.name, 'description': vt.description} for vt in voter_types],
    })


def get_vote_summary_by_voter_type(event: Dict[str, Any]) -> Dict[str, Any]:
    """Get vote summary grouped by voter type for a session/sprint."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    sprint_id = (body.get('sprintId') or '').strip()

    if not session_code or not sprint_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    summary = repo.get_vote_summary_by_voter_type(session_code, sprint_id)
    return response(200, {'ok': True, 'summary': summary})


def vote_on_response(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle voting on a response. Voters only."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    response_id = (body.get('responseId') or '').strip()
    participant_id = (body.get('participantId') or '').strip()
    participant_name = (body.get('participantName') or '').strip()
    participant_role = (body.get('participantRole') or '').strip()

    if not session_code or not response_id or not participant_id or not participant_name or not participant_role:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    if participant_role != 'voter':
        return response(403, {'ok': False, 'message': 'Only voters can submit votes.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    if not session.voting_enabled:
        return response(403, {'ok': False, 'message': 'Voting is not currently enabled.'})

    # Validate response exists in this session
    response_exists = any(r.id == response_id for r in session.responses)
    if not response_exists:
        return response(404, {'ok': False, 'message': 'Response not found.'})

    # Look up participant to retrieve their voter type
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller:
        return response(404, {'ok': False, 'message': 'Participant not found.'})

    repo.add_vote(session_code, response_id, participant_id, participant_name, participant_role,
                  voter_type_id=caller.voter_type_id, voter_type_name=caller.voter_type_name)
    return response(200, {'ok': True})


def control_voting(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator control for voting state: open, close, or toggle-voter-visibility."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    action = (body.get('action') or '').strip()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not action:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    valid_actions = ('open', 'close', 'toggle-voter-visibility')
    if action not in valid_actions:
        return response(400, {'ok': False, 'message': 'Invalid action. Must be: open, close, toggle-voter-visibility'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Verify caller is facilitator
    if not participant_id:
        return response(403, {'ok': False, 'message': 'Only facilitators can control voting.'})

    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Only facilitators can control voting.'})

    if action == 'open':
        repo.set_voting_enabled(session_code, True)
    elif action == 'close':
        repo.set_voting_enabled(session_code, False)
    elif action == 'toggle-voter-visibility':
        repo.toggle_voting_show_voters(session_code)

    updated = repo.get_session_by_code(session_code)
    return response(200, {
        'ok': True,
        'votingEnabled': updated.voting_enabled,
        'votingShowVoters': updated.voting_show_voters,
    })


def facilitator_remove_vote(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator removes a specific vote by ID."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    vote_id = (body.get('voteId') or '').strip()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not vote_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Verify caller is facilitator
    if not participant_id:
        return response(403, {'ok': False, 'message': 'Only facilitators can remove votes.'})

    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Only facilitators can remove votes.'})

    result = repo.remove_vote_by_id(vote_id)
    if result is None:
        return response(404, {'ok': False, 'message': 'Vote not found.'})

    return response(200, {'ok': True})


def facilitator_remove_response(event: Dict[str, Any]) -> Dict[str, Any]:
    """Facilitator removes a response and all associated votes."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    response_id = (body.get('responseId') or '').strip()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not response_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Verify caller is facilitator
    if not participant_id:
        return response(403, {'ok': False, 'message': 'Only facilitators can remove responses.'})

    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller or caller.role != 'facilitator':
        return response(403, {'ok': False, 'message': 'Only facilitators can remove responses.'})

    success = repo.delete_response(session_code, response_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Response not found.'})

    return response(200, {'ok': True})


def retract_vote(event: Dict[str, Any]) -> Dict[str, Any]:
    """Voter retracts their own vote on a response."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    response_id = (body.get('responseId') or '').strip()
    participant_id = (body.get('participantId') or '').strip()

    if not session_code or not response_id or not participant_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Verify caller is a voter
    participants = repo.get_session_participants(session_code)
    caller = next((p for p in participants if p.id == participant_id), None)
    if not caller:
        return response(403, {'ok': False, 'message': 'Participant not found.'})

    if caller.role != 'voter':
        return response(403, {'ok': False, 'message': 'Only voters can retract votes.'})

    success = repo.remove_vote(session_code, response_id, participant_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Vote not found.'})

    return response(200, {'ok': True})

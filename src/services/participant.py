"""Participant management: join, roles, select, assign, remove."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response, get_client_ip


def _participant_dict(participant) -> Dict[str, Any]:
    """Build the participant payload included in join responses."""
    return {
        'id': participant.id,
        'name': participant.name,
        'role': participant.role,
        'assignedRoleName': participant.assigned_role_name,
        'roleType': participant.role_type,
        'roleDescription': participant.role_description,
        'privateInfo': participant.private_info,
    }


def join_session(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a request to join an existing session."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_name = (body.get('participantName') or '').strip()
    role = body.get('role')
    ip_address = get_client_ip(event)

    # Validate required fields
    if not session_code or not participant_name or not role:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    # Validate role
    if role not in ('facilitator', 'player', 'viewer', 'voter'):
        return response(400, {'ok': False, 'message': 'Invalid role.'})

    # Look up session
    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found. Please check your code.'})

    # Handle facilitator slot conflict
    if role == 'facilitator' and repo.has_facilitator(session_code):
        pin = (body.get('facilitatorPin') or '').strip()
        if not pin:
            return response(409, {
                'ok': False,
                'message': 'A facilitator has already joined this session.',
                'requiresPin': True,
            })
        if not repo.check_facilitator_pin(session_code, pin):
            return response(403, {'ok': False, 'message': 'Incorrect facilitator PIN.'})
        # PIN matched — replace the existing facilitator
        participant = repo.replace_facilitator(session_code, participant_name, ip_address)
        if not participant:
            return response(500, {'ok': False, 'message': 'Failed to rejoin as facilitator.'})
        session_data = session.to_dict()
        session_data['participant'] = _participant_dict(participant)
        return response(200, {'ok': True, 'session': session_data})

    # Handle voter type selection for voter role
    voter_type_id: Any = None
    voter_type_name: Any = None

    if role == 'voter':
        voter_types = repo.get_voter_types_for_session(session_code)
        if not voter_types:
            return response(400, {'ok': False, 'message': 'Voting is not available for this scenario.'})
        requested_voter_type_id = (body.get('voterTypeId') or '').strip()
        if not requested_voter_type_id:
            return response(400, {
                'ok': False,
                'requiresVoterTypeSelection': True,
                'voterTypes': [{'id': vt.id, 'name': vt.name, 'description': vt.description} for vt in voter_types],
            })
        matched = next((vt for vt in voter_types if vt.id == requested_voter_type_id), None)
        if not matched:
            return response(400, {'ok': False, 'message': 'Invalid voter type.'})
        voter_type_id = matched.id
        voter_type_name = matched.name

    # Handle player role PIN flow — require PIN only when role_pins_enabled is true
    if role == 'player':
        result = _handle_player_join(session_code, session, body, participant_name, role, ip_address)
        if result is not None:
            return result

    # Add participant and return session data
    participant = repo.add_participant(session_code, participant_name, role, ip_address,
                                       voter_type_id=voter_type_id, voter_type_name=voter_type_name)
    session_data = session.to_dict()
    session_data['participant'] = _participant_dict(participant)

    return response(200, {'ok': True, 'session': session_data})


def _handle_player_join(session_code: str, session, body: dict,
                        participant_name: str, role: str, ip_address: str):
    """Handle the player join flow with optional role PINs.
    
    Returns a response dict if the flow is handled (PIN required, PIN claimed, reconnection).
    Returns None if role PINs are disabled and normal join should proceed.
    """
    role_pins_enabled = repo.is_role_pins_enabled(session_code)
    if not role_pins_enabled:
        return None

    role_pin = (body.get('rolePin') or '').strip()

    if not role_pin:
        # No PIN provided — check if any roles are available
        unclaimed_count = repo.get_unclaimed_role_count(session_code)
        if unclaimed_count == 0:
            return response(200, {'ok': False, 'message': 'All player roles are filled.'})

        # Return available roles and request PIN
        available_roles = repo.get_unclaimed_roles(session_code)
        return response(200, {
            'ok': False,
            'requiresRolePin': True,
            'availableRoles': available_roles,
        })

    # PIN provided — validate it
    role_pin_record = repo.validate_role_pin(session_code, role_pin)
    if not role_pin_record:
        repo._log_event('role_pin_failed', session_code, {
            'participant_name': participant_name,
            'ip_address': ip_address,
        })
        return response(200, {'ok': False, 'message': 'Invalid role PIN.'})

    # Valid PIN — check if it's unclaimed or a reconnection
    if role_pin_record.claimed_by_participant_id is None:
        return _claim_new_role(session_code, session, role_pin_record, participant_name, role, ip_address)
    else:
        return _reconnect_role(session_code, session, role_pin_record, participant_name, ip_address)


def _claim_new_role(session_code, session, role_pin_record, participant_name, role, ip_address):
    """Claim an unclaimed role via PIN."""
    info = repo.get_session_scenario_info(session_code)
    role_description = None
    private_info = None
    if info and info['scenario_id']:
        scenario = repo.get_scenario(info['scenario_id'], info['scenario_version'])
        if scenario:
            for r in scenario.roles:
                if r.get('id') == role_pin_record.role_id:
                    role_description = r.get('description')
                    private_info = r.get('privateInfo')
                    break

    participant = repo.add_participant(
        session_code, participant_name, role, ip_address,
        assigned_role_name=role_pin_record.role_name,
        role_type='player',
        role_description=role_description,
        private_info=private_info,
    )

    repo.claim_role_pin(session_code, role_pin_record.role_id, participant.id, participant_name)

    repo._log_event('role_claimed', session_code, {
        'participant_name': participant_name,
        'assigned_role_name': role_pin_record.role_name,
        'ip_address': ip_address,
    })

    session_data = session.to_dict()
    session_data['participant'] = _participant_dict(participant)
    return response(200, {'ok': True, 'session': session_data})


def _reconnect_role(session_code, session, role_pin_record, participant_name, ip_address):
    """Reconnect to an already-claimed role via PIN."""
    previous_claimant = role_pin_record.claimed_by_name

    participant = repo.replace_player_for_role(
        session_code, role_pin_record.role_id, participant_name, ip_address
    )
    if not participant:
        return response(500, {'ok': False, 'message': 'Failed to rejoin as player.'})

    repo._log_event('role_reclaimed', session_code, {
        'participant_name': participant_name,
        'assigned_role_name': role_pin_record.role_name,
        'previous_claimant': previous_claimant,
    })

    session_data = session.to_dict()
    session_data['participant'] = _participant_dict(participant)
    return response(200, {'ok': True, 'session': session_data})


def get_session_roles(session_code: str) -> Dict[str, Any]:
    """Return the selectable player roles for a session's scenario, with claim status."""
    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    info = repo.get_session_scenario_info(session_code)
    if not info:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    scenario = repo.get_scenario(info['scenario_id'], info['scenario_version'])
    if not scenario:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    # After session start, exclude roles that have been claimed via role PINs
    if session.started:
        role_pins = repo.get_role_pins(session_code)
        claimed_role_ids = {rp.role_id for rp in role_pins if rp.claimed_by_participant_id is not None}

        roles = []
        for r in scenario.roles:
            if r.get('roleType') != 'player':
                continue
            if r.get('id') in claimed_role_ids:
                continue
            roles.append({
                'id': r.get('id'),
                'name': r.get('name', ''),
                'description': r.get('description', ''),
                'activeCount': 0,
            })

        return response(200, {'ok': True, 'roles': roles})

    # Before session start, return all roles with active counts
    participants = repo.get_session_participants(session_code)
    active_count_map: Dict[str, int] = {}
    for p in participants:
        if p.role == 'player' and p.assigned_role_name:
            active_count_map[p.assigned_role_name] = active_count_map.get(p.assigned_role_name, 0) + 1

    roles = []
    for r in scenario.roles:
        if r.get('roleType') != 'player':
            continue
        role_name = r.get('name', '')
        roles.append({
            'id': r.get('id'),
            'name': role_name,
            'description': r.get('description', ''),
            'activeCount': active_count_map.get(role_name, 0),
        })

    return response(200, {'ok': True, 'roles': roles})


def select_role(event: Dict[str, Any]) -> Dict[str, Any]:
    """Allow a player to claim a specific scenario role."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    role_id = (body.get('roleId') or '').strip()

    if not session_code or not participant_id or not role_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    info = repo.get_session_scenario_info(session_code)
    if not info:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    participants = repo.get_session_participants(session_code)
    participant = next((p for p in participants if p.id == participant_id), None)
    if not participant:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if participant.role != 'player':
        return response(400, {'ok': False, 'message': 'Only players can select a role.'})

    scenario = repo.get_scenario(info['scenario_id'], info['scenario_version'])
    if not scenario:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    role_data = next((r for r in scenario.roles if r.get('id') == role_id and r.get('roleType') == 'player'), None)
    if not role_data:
        return response(400, {'ok': False, 'message': 'Invalid role.'})

    role_name = role_data.get('name', '')

    # Idempotent: if the requesting player already holds this role, return success
    if participant.assigned_role_name == role_name:
        return response(200, {'ok': True})

    # Uniqueness: check if another active participant holds the requested role
    holder = next(
        (p for p in participants
         if p.id != participant_id and p.role == 'player' and p.assigned_role_name == role_name),
        None,
    )
    if holder:
        return response(409, {'ok': False, 'message': f'The role "{role_name}" is already claimed by another player.'})

    updated = repo.update_participant_role(
        participant_id=participant_id,
        assigned_role_name=role_name,
        role_description=role_data.get('description', ''),
        private_info=role_data.get('privateInfo'),
        session_code=session_code,
    )
    if not updated:
        return response(500, {'ok': False, 'message': 'Failed to update role.'})

    return response(200, {'ok': True})


def assign_player_role(event: Dict[str, Any]) -> Dict[str, Any]:
    """Allow a facilitator to reassign a player's scenario role."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    target_participant_id = (body.get('participantId') or '').strip()
    role_id = (body.get('roleId') or '').strip()

    if not session_code or not target_participant_id or not role_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    info = repo.get_session_scenario_info(session_code)
    if not info:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    participants = repo.get_session_participants(session_code)
    target = next((p for p in participants if p.id == target_participant_id), None)
    if not target:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if target.role != 'player':
        return response(400, {'ok': False, 'message': 'Can only assign roles to players.'})

    scenario = repo.get_scenario(info['scenario_id'], info['scenario_version'])
    if not scenario:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    role_data = next((r for r in scenario.roles if r.get('id') == role_id and r.get('roleType') == 'player'), None)
    if not role_data:
        return response(400, {'ok': False, 'message': 'Invalid role.'})

    role_name = role_data.get('name', '')

    updated = repo.update_participant_role(
        participant_id=target_participant_id,
        assigned_role_name=role_name,
        role_description=role_data.get('description', ''),
        private_info=role_data.get('privateInfo'),
        session_code=session_code,
    )
    if not updated:
        return response(500, {'ok': False, 'message': 'Failed to assign role.'})

    return response(200, {'ok': True})


def remove_player(event: Dict[str, Any]) -> Dict[str, Any]:
    """Allow a facilitator to remove a player from the session."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    target_participant_id = (body.get('participantId') or '').strip()

    if not session_code or not target_participant_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    participants = repo.get_session_participants(session_code)
    target = next((p for p in participants if p.id == target_participant_id), None)
    if not target:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if target.role != 'player':
        return response(400, {'ok': False, 'message': 'Can only remove players.'})

    # Release the role PIN claim before removing the participant
    role_released = repo.release_role_pin(session_code, target_participant_id)
    if role_released and target.assigned_role_name:
        repo._log_event('role_released', session_code, {
            'role_name': target.assigned_role_name,
            'removed_participant_name': target.name,
        })

    removed = repo.remove_participant(target_participant_id, session_code)
    if not removed:
        return response(500, {'ok': False, 'message': 'Failed to remove player.'})

    return response(200, {'ok': True})

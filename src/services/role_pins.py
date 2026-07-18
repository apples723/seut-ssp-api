"""Role PIN service: claim, validate, list PINs."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response, get_client_ip, check_admin_token


def claim_role_with_pin(event: Dict[str, Any]) -> Dict[str, Any]:
    """Allow a pre-start player to claim a role after PINs are generated."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    participant_id = (body.get('participantId') or '').strip()
    role_pin = (body.get('rolePin') or '').strip()
    ip_address = get_client_ip(event)

    if not session_code or not participant_id or not role_pin:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    # Validate session exists
    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Validate participant exists and is a player
    participants = repo.get_session_participants(session_code)
    participant = next((p for p in participants if p.id == participant_id), None)
    if not participant:
        return response(404, {'ok': False, 'message': 'Participant not found.'})
    if participant.role != 'player':
        return response(400, {'ok': False, 'message': 'Only players can claim roles.'})

    # Validate the role PIN
    role_pin_record = repo.validate_role_pin(session_code, role_pin)
    if not role_pin_record:
        repo._log_event('role_pin_failed', session_code, {
            'participant_name': participant.name,
            'ip_address': ip_address,
        })
        return response(200, {'ok': False, 'message': 'Invalid role PIN.'})

    # Get role details from scenario
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

    # Check if role is unclaimed or this is a reconnection
    if role_pin_record.claimed_by_participant_id is None:
        # Unclaimed role — claim it and update participant with role assignment
        repo.update_participant_role(
            participant_id=participant_id,
            assigned_role_name=role_pin_record.role_name,
            role_description=role_description,
            private_info=private_info,
            session_code=session_code,
            assigned_role_id=role_pin_record.role_id,
        )

        repo.claim_role_pin(session_code, role_pin_record.role_id, participant_id, participant.name)

        repo._log_event('role_claimed', session_code, {
            'participant_name': participant.name,
            'assigned_role_name': role_pin_record.role_name,
            'ip_address': ip_address,
        })

        return response(200, {
            'ok': True,
            'roleId': role_pin_record.role_id,
            'roleName': role_pin_record.role_name,
            'roleDescription': role_description,
            'privateInfo': private_info,
        })
    else:
        # Role already claimed — this is a reconnection
        previous_claimant = role_pin_record.claimed_by_name

        old_participant_id = role_pin_record.claimed_by_participant_id
        if old_participant_id != participant_id:
            repo.remove_participant(old_participant_id, session_code)

        repo.update_participant_role(
            participant_id=participant_id,
            assigned_role_name=role_pin_record.role_name,
            role_description=role_description,
            private_info=private_info,
            session_code=session_code,
            assigned_role_id=role_pin_record.role_id,
        )

        repo.claim_role_pin(session_code, role_pin_record.role_id, participant_id, participant.name)

        repo._log_event('role_reclaimed', session_code, {
            'participant_name': participant.name,
            'assigned_role_name': role_pin_record.role_name,
            'previous_claimant': previous_claimant,
        })

        return response(200, {
            'ok': True,
            'roleId': role_pin_record.role_id,
            'roleName': role_pin_record.role_name,
            'roleDescription': role_description,
            'privateInfo': private_info,
        })


def get_role_pins(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return all role PINs for a session (facilitator only)."""
    params = event.get('queryStringParameters') or {}
    session_code = (params.get('code') or '').strip().upper()
    participant_id = (params.get('pid') or '').strip()

    if not session_code or not participant_id:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Verify caller is the facilitator
    if not repo.is_facilitator(session_code, participant_id):
        return response(403, {'ok': False, 'message': 'Only the facilitator can view role PINs.'})

    role_pins = repo.get_role_pins(session_code)

    return response(200, {
        'ok': True,
        'rolePins': [
            {
                'roleId': rp.role_id,
                'roleName': rp.role_name,
                'pin': rp.pin,
                'claimedByName': rp.claimed_by_name,
                'claimedAt': rp.claimed_at,
            }
            for rp in role_pins
        ],
    })


def admin_get_session_pins(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return all role PINs for a session (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    params = event.get('queryStringParameters') or {}
    session_code = (params.get('code') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    role_pins = repo.get_role_pins(session_code)

    return response(200, {
        'ok': True,
        'rolePins': [
            {
                'roleId': rp.role_id,
                'roleName': rp.role_name,
                'pin': rp.pin,
                'claimedByName': rp.claimed_by_name,
                'claimedAt': rp.claimed_at,
            }
            for rp in role_pins
        ],
    })

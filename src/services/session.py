"""Session CRUD service functions: get, create, start, end."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response, get_client_ip


def get_session() -> Dict[str, Any]:
    return response(200, repo.get_session().to_dict())


def create_session(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a request to create a new session."""
    body = json.loads(event.get('body') or '{}')
    facilitator_name = (body.get('facilitatorName') or '').strip()
    scenario_id = body.get('scenarioId', 'mental-health-app')
    facilitator_pin = (body.get('facilitatorPin') or '').strip() or None
    role_pins_enabled = body.get('rolePinsEnabled', False)
    ip_address = get_client_ip(event)

    # Validate facilitator name
    if not facilitator_name:
        return response(400, {'ok': False, 'message': 'Facilitator name is required.'})

    # Validate PIN format if provided
    if facilitator_pin is not None and (not facilitator_pin.isdigit() or len(facilitator_pin) != 4):
        return response(400, {'ok': False, 'message': 'Facilitator PIN must be exactly 4 digits.'})

    # Validate rolePinsEnabled is a boolean if present
    if not isinstance(role_pins_enabled, bool):
        return response(400, {'ok': False, 'message': 'rolePinsEnabled must be a boolean'})

    # Create session
    session = repo.create_session(facilitator_name, scenario_id, facilitator_pin, ip_address,
                                  role_pins_enabled=role_pins_enabled)

    return response(200, {
        'ok': True,
        'session': session.to_dict(),
        'sessionCode': session.session_id,
    })


def start_session(event: Dict[str, Any]) -> Dict[str, Any]:
    """Start a session — facilitator only. Marks session as started so participants can interact."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    repo.start_session(session_code)
    return response(200, {'ok': True})


def end_session(event: Dict[str, Any]) -> Dict[str, Any]:
    """End a session — facilitator only. Marks session as ended so all clients redirect out."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    repo.end_session(session_code)
    return response(200, {'ok': True})

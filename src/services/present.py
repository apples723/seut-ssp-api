"""Present mode authentication."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


def present_auth(event: Dict[str, Any]) -> Dict[str, Any]:
    """Authenticate a facilitator for present mode (read-only view)."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    pin = (body.get('facilitatorPin') or '').strip()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    # Check if session has a facilitator PIN set
    if not session.facilitator_pin:
        return response(403, {'ok': False, 'message': 'This session does not have a facilitator PIN configured.'})

    # Validate the PIN
    if not pin:
        return response(400, {'ok': False, 'message': 'Facilitator PIN is required.'})

    if not repo.check_facilitator_pin(session_code, pin):
        return response(403, {'ok': False, 'message': 'Incorrect facilitator PIN.'})

    # Return session data for present mode
    return response(200, {'ok': True, 'session': session.to_dict()})

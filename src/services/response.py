"""Response submission service."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


def submit_response(event: Dict[str, Any]) -> Dict[str, Any]:
    body = json.loads(event.get('body') or '{}')
    text = (body.get('text') or '').strip()
    participant_name = (body.get('participantName') or 'Anonymous').strip() or 'Anonymous'
    participant_id = (body.get('participantId') or '').strip()
    session_code = (body.get('sessionCode') or '').strip().upper()
    response_box = body.get('responseBox')  # Optional: which box to put the response in

    if not text:
        return response(400, {'ok': False, 'message': 'Response text is required.'})

    # If session code provided, add to that session; otherwise use default
    if session_code:
        session = repo.get_session_by_code(session_code)
        if not session:
            return response(404, {'ok': False, 'message': 'Session not found.'})

        # Look up participant's assigned role name
        participant_role_name = None
        if participant_id:
            participant_role_name = repo.get_participant_role_name(session_code, participant_id)

        repo.add_response_to_session(session_code, participant_name, text, response_box, participant_role_name)
    else:
        repo.add_response(participant_name=participant_name, text=text, response_box=response_box)

    return response(200, {'ok': True})

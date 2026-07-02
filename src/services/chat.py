"""Chat service: send messages, get session log."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


def send_chat(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a chat message from a session participant."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    sender_name = (body.get('senderName') or '').strip()
    sender_role = (body.get('senderRole') or '').strip()
    text = (body.get('text') or '').strip()

    if not session_code or not sender_name or not sender_role or not text:
        return response(400, {'ok': False, 'message': 'Missing required fields.'})

    if sender_role not in ('facilitator', 'player', 'viewer', 'voter'):
        return response(400, {'ok': False, 'message': 'Invalid role.'})

    # Only facilitators, players, and voters may post; viewers are read-only
    if sender_role == 'viewer':
        return response(403, {'ok': False, 'message': 'Viewers cannot send chat messages.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    repo.add_chat_message(session_code, sender_name, sender_role, text)
    return response(200, {'ok': True})


def get_session_log() -> Dict[str, Any]:
    """Return the session activity log."""
    # TODO: add auth check before exposing in production
    return response(200, {'log': repo.get_session_log()})

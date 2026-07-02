"""Admin session management endpoints."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response, check_admin_token


def admin_list_sessions(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return all sessions for the admin dashboard."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})
    return response(200, {'ok': True, 'sessions': repo.list_sessions()})


def admin_end_session(event: Dict[str, Any]) -> Dict[str, Any]:
    """Admin endpoint to end a specific session."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    repo.end_session(session_code)
    return response(200, {'ok': True})

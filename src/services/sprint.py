"""Sprint control: next, prev, timer."""
import json
from typing import Any, Dict

from ..repository_factory import repo
from .helpers import response


def next_sprint(event: Dict[str, Any] = None) -> Dict[str, Any]:
    session_code = None
    if event:
        body = json.loads(event.get('body') or '{}')
        session_code = (body.get('sessionCode') or '').strip().upper()

    if session_code:
        session = repo.get_session_by_code(session_code)
        if not session:
            return response(404, {'ok': False, 'message': 'Session not found.'})
        repo.next_sprint_for_session(session_code)
    else:
        repo.next_sprint()

    return response(200, {'ok': True})


def prev_sprint(event: Dict[str, Any] = None) -> Dict[str, Any]:
    """Go back to the previous sprint."""
    session_code = None
    if event:
        body = json.loads(event.get('body') or '{}')
        session_code = (body.get('sessionCode') or '').strip().upper()

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    if session.current_sprint_index == 0:
        return response(400, {'ok': False, 'message': 'Already at first sprint.'})

    repo.prev_sprint_for_session(session_code)
    return response(200, {'ok': True})


def update_timer(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle timer control actions from facilitator."""
    body = json.loads(event.get('body') or '{}')
    session_code = (body.get('sessionCode') or '').strip().upper()
    action = (body.get('action') or '').strip().lower()
    seconds = body.get('seconds')

    if not session_code:
        return response(400, {'ok': False, 'message': 'Session code is required.'})

    valid_actions = ('start', 'stop', 'set', 'hide', 'show')
    if action not in valid_actions:
        return response(400, {'ok': False, 'message': f'Invalid action. Must be one of: {", ".join(valid_actions)}'})

    if action == 'set':
        if seconds is None or not isinstance(seconds, int) or seconds < 0:
            return response(400, {'ok': False, 'message': 'Seconds must be a non-negative integer for set action.'})

    session = repo.get_session_by_code(session_code)
    if not session:
        return response(404, {'ok': False, 'message': 'Session not found.'})

    updated = repo.update_timer(session_code, action, seconds)
    if not updated:
        return response(500, {'ok': False, 'message': 'Failed to update timer.'})

    return response(200, {
        'ok': True,
        'timerSeconds': updated.timer_seconds,
        'timerRunning': updated.timer_running,
        'timerVisible': updated.timer_visible,
    })

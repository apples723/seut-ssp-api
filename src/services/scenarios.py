"""Scenario admin CRUD and public listing."""
import json
from typing import Any, Dict
from uuid import uuid4

from ..repository_factory import repo
from .helpers import response, check_admin_token


def admin_list_scenarios(event: Dict[str, Any]) -> Dict[str, Any]:
    """List all scenarios (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}') if event.get('body') else {}
    include_deleted = body.get('includeDeleted', False)

    scenarios = repo.list_scenarios(include_deleted=include_deleted)
    return response(200, {
        'ok': True,
        'scenarios': [s.to_dict() for s in scenarios],
    })


def admin_get_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Get a single scenario by ID (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()
    version = body.get('version')

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    scenario = repo.get_scenario(scenario_id, version)
    if not scenario:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True, 'scenario': scenario.to_dict()})


def admin_get_scenario_history(event: Dict[str, Any]) -> Dict[str, Any]:
    """Get version history for a scenario (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    versions = repo.get_scenario_history(scenario_id)
    if not versions:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {
        'ok': True,
        'scenarioId': scenario_id,
        'versions': [v.to_dict() for v in versions],
    })


def admin_create_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new scenario (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_data = body.get('scenario')

    if not scenario_data:
        return response(400, {'ok': False, 'message': 'Scenario data is required.'})

    scenario_data = _apply_scenario_defaults(scenario_data)

    if scenario_data.get('id'):
        existing = repo.get_scenario(scenario_data['id'])
        if existing:
            return response(409, {'ok': False, 'message': f'Scenario with ID "{scenario_data["id"]}" already exists. Use update instead.'})

    created_by = body.get('createdBy', 'admin')
    scenario = repo.create_scenario(scenario_data, created_by)

    return response(200, {'ok': True, 'scenario': scenario.to_dict()})


def admin_update_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing scenario (creates new version) (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()
    scenario_data = body.get('scenario')

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    if not scenario_data:
        return response(400, {'ok': False, 'message': 'Scenario data is required.'})

    scenario_data = _apply_scenario_defaults(scenario_data)

    created_by = body.get('createdBy', 'admin')
    scenario = repo.update_scenario(scenario_id, scenario_data, created_by)

    if not scenario:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True, 'scenario': scenario.to_dict()})


def admin_delete_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Soft-delete a scenario (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    success = repo.delete_scenario(scenario_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True})


def admin_restore_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Restore a soft-deleted scenario (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    success = repo.restore_scenario(scenario_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True})


def admin_enable_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Enable a scenario for session creation (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    success = repo.enable_scenario(scenario_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True})


def admin_disable_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Disable a scenario from session creation (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    success = repo.disable_scenario(scenario_id)
    if not success:
        return response(404, {'ok': False, 'message': 'Scenario not found.'})

    return response(200, {'ok': True})


def admin_diff_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Compare uploaded JSON against existing scenario (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_id = (body.get('scenarioId') or '').strip()
    new_data = body.get('scenario')

    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario ID is required.'})

    if not new_data:
        return response(400, {'ok': False, 'message': 'Scenario data is required.'})

    new_data = _apply_scenario_defaults(new_data)

    diff = repo.diff_scenario(scenario_id, new_data)
    return response(200, {'ok': True, 'diff': diff})


def admin_import_scenario(event: Dict[str, Any]) -> Dict[str, Any]:
    """Import a scenario from JSON, auto-detecting create vs update (admin only)."""
    if not check_admin_token(event):
        return response(401, {'ok': False, 'message': 'Invalid admin token.'})

    body = json.loads(event.get('body') or '{}')
    scenario_data = body.get('scenario')

    if not scenario_data:
        return response(400, {'ok': False, 'message': 'Scenario data is required.'})

    scenario_data = _apply_scenario_defaults(scenario_data)

    scenario_id = scenario_data.get('id')
    if not scenario_id:
        return response(400, {'ok': False, 'message': 'Scenario must have an ID.'})

    existing = repo.get_scenario(scenario_id)
    created_by = body.get('createdBy', 'admin')

    if existing:
        diff = repo.diff_scenario(scenario_id, scenario_data)
        return response(200, {
            'ok': True,
            'action': 'update',
            'existingVersion': existing.version,
            'diff': diff,
            'scenario': scenario_data,
        })
    else:
        return response(200, {
            'ok': True,
            'action': 'create',
            'scenario': scenario_data,
        })


def list_scenarios_public(event: Dict[str, Any] = None) -> Dict[str, Any]:
    """List available scenarios for session creation (public, no auth required)."""
    scenarios = repo.list_scenarios(include_deleted=False)
    return response(200, {
        'ok': True,
        'scenarios': [
            {
                'id': s.id,
                'title': s.title,
                'description': s.description,
            }
            for s in scenarios if s.enabled
        ],
    })


def _apply_scenario_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply default values for lenient scenario import."""
    # Ensure ID
    if not data.get('id'):
        data['id'] = str(uuid4())[:8]

    # Ensure title
    if not data.get('title'):
        data['title'] = 'Untitled Scenario'

    # Ensure settings with defaults
    settings = data.get('settings', {})
    settings.setdefault('timerEnabled', True)
    settings.setdefault('timerSeconds', 480)
    data['settings'] = settings

    # Ensure roles array
    if not data.get('roles'):
        data['roles'] = [
            {'id': 'facilitator', 'name': 'Facilitator', 'roleType': 'facilitator', 'description': 'Controls the workshop.'},
            {'id': 'player', 'name': 'Player', 'roleType': 'player', 'description': 'Participates in the simulation.'},
            {'id': 'viewer', 'name': 'Viewer', 'roleType': 'viewer', 'description': 'Observes the session.'},
        ]
    else:
        for role in data['roles']:
            role.setdefault('id', str(uuid4())[:8])
            role.setdefault('name', 'Unnamed Role')
            role.setdefault('roleType', 'player')
            role.setdefault('description', '')

    # Ensure sprints array
    if not data.get('sprints'):
        data['sprints'] = [
            {'id': 's1', 'name': 'Sprint 1', 'description': 'First sprint.', 'timerEnabled': True, 'timerSeconds': 480},
        ]
    else:
        for i, sprint in enumerate(data['sprints']):
            sprint.setdefault('id', f's{i+1}')
            sprint.setdefault('name', f'Sprint {i+1}')
            sprint.setdefault('description', '')
            sprint.setdefault('timerEnabled', True)
            sprint.setdefault('timerSeconds', 480)

    return data

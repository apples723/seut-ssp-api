"""
WSGI entry point for Docker/Gunicorn deployment.

Current Architecture:
    This Flask app runs in a Docker container behind nginx, serving the REST API
    for the simulation platform. Gunicorn handles the WSGI interface.

Future Architecture (AWS Serverless):
    The target architecture is serverless on AWS:
    - API Gateway + Lambda for the backend (replacing this Flask app)
    - CloudFront + S3 for the static frontend SPA
    - The handlers/ directory contains Lambda-compatible wrappers ready for this migration

    When migrated to Lambda, this file becomes obsolete. The service layer (src/service.py)
    and repository (src/sqlite_repository.py) would be invoked directly by Lambda handlers,
    with API Gateway handling routing and CORS.

For local development without Docker, use local_server.py instead.
"""
from flask import Flask, request, jsonify, Response
from src.service import (
    get_session,
    submit_response,
    next_sprint,
    prev_sprint,
    join_session,
    create_session,
    send_chat,
    get_session_log,
    end_session,
    start_session,
    admin_list_sessions,
    admin_end_session,
    update_timer,
    present_auth,
    admin_list_scenarios,
    admin_get_scenario,
    admin_get_scenario_history,
    admin_create_scenario,
    admin_update_scenario,
    admin_delete_scenario,
    admin_restore_scenario,
    admin_enable_scenario,
    admin_disable_scenario,
    admin_diff_scenario,
    admin_import_scenario,
    list_scenarios_public,
    get_tutorial_config,
    admin_list_tutorials,
    admin_get_tutorial,
    admin_get_tutorial_history,
    admin_create_tutorial,
    admin_update_tutorial,
    admin_diff_tutorial,
    admin_import_tutorial,
    admin_publish_tutorial,
    admin_disable_tutorial,
    get_session_roles,
    select_role,
    assign_player_role,
    remove_player,
    vote_on_response,
    control_voting,
    facilitator_remove_vote,
    facilitator_remove_response,
    retract_vote,
    get_session_voter_types,
    get_vote_summary_by_voter_type,
    submit_engineer_sprint_vote,
    submit_stakeholder_sprint_vote,
    submit_head_to_head_vote,
    facilitator_set_phase,
    get_sprint_question_status,
    get_session_scores,
    facilitator_reveal_scores,
    facilitator_toggle_scoreboard,
    claim_role_with_pin,
    get_role_pins,
    admin_get_session_pins,
)
from src.sqlite_repository import repo
import json
import os
from pathlib import Path

app = Flask(__name__)


def _flask_response(result: dict) -> Response:
    """Convert Lambda-style response dict to Flask response."""
    body = result.get('body', '{}')
    status = result.get('statusCode', 200)
    resp = Response(body, status=status, mimetype='application/json')
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-Token'
    return resp


def _build_event(body: str = '{}') -> dict:
    """Build an event dict with body and headers for service functions."""
    headers = {k: v for k, v in request.headers}
    return {'body': body, 'headers': headers}


@app.route('/session', methods=['GET', 'OPTIONS'])
def handle_get_session() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    code = request.args.get('code')
    pid = request.args.get('pid')
    if code:
        session = repo.get_session_by_code(code.upper(), participant_id=pid)
        if session:
            # If a participant ID was provided but that participant no longer
            # exists in the session (e.g. removed by facilitator), reject.
            if pid and not any(p.id == pid for p in session.participants):
                return _flask_response({'statusCode': 403, 'body': json.dumps({'ok': False, 'message': 'You have been removed from this session.'})})
            return _flask_response({'statusCode': 200, 'body': json.dumps(session.to_dict())})
        return _flask_response({'statusCode': 404, 'body': json.dumps({'ok': False, 'message': 'Session not found.'})})
    return _flask_response(get_session())


@app.route('/join', methods=['POST', 'OPTIONS'])
def handle_join() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(join_session({'body': request.get_data(as_text=True)}))


@app.route('/create', methods=['POST', 'OPTIONS'])
def handle_create() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(create_session({'body': request.get_data(as_text=True)}))


@app.route('/responses', methods=['POST', 'OPTIONS'])
def handle_responses() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(submit_response({'body': request.get_data(as_text=True)}))


@app.route('/facilitator/next-sprint', methods=['POST', 'OPTIONS'])
def handle_next_sprint() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(next_sprint({'body': request.get_data(as_text=True)}))


@app.route('/facilitator/prev-sprint', methods=['POST', 'OPTIONS'])
def handle_prev_sprint() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(prev_sprint({'body': request.get_data(as_text=True)}))


@app.route('/chat', methods=['POST', 'OPTIONS'])
def handle_chat() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(send_chat({'body': request.get_data(as_text=True)}))


@app.route('/admin/log', methods=['GET'])
def handle_session_log() -> Response:
    return _flask_response(get_session_log())


@app.route('/facilitator/end-session', methods=['POST', 'OPTIONS'])
def handle_end_session() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(end_session(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/start-session', methods=['POST', 'OPTIONS'])
def handle_start_session() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(start_session(_build_event(request.get_data(as_text=True))))


@app.route('/admin/sessions', methods=['GET', 'OPTIONS'])
def handle_admin_list_sessions() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_list_sessions(_build_event()))


@app.route('/admin/end-session', methods=['POST', 'OPTIONS'])
def handle_admin_end_session() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_end_session(_build_event(request.get_data(as_text=True))))


@app.route('/session/roles', methods=['GET', 'OPTIONS'])
def handle_get_session_roles() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    code = request.args.get('code', '').strip().upper()
    if not code:
        return _flask_response({'statusCode': 400, 'body': json.dumps({'ok': False, 'message': 'Session code is required.'})})
    return _flask_response(get_session_roles(code))


@app.route('/session/select-role', methods=['POST', 'OPTIONS'])
def handle_select_role() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(select_role({'body': request.get_data(as_text=True)}))


@app.route('/facilitator/assign-role', methods=['POST', 'OPTIONS'])
def handle_assign_player_role() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(assign_player_role({'body': request.get_data(as_text=True)}))


@app.route('/facilitator/remove-player', methods=['POST', 'OPTIONS'])
def handle_remove_player() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(remove_player({'body': request.get_data(as_text=True)}))


# ==================== Role PIN Routes ====================

@app.route('/session/claim-role', methods=['POST', 'OPTIONS'])
def handle_claim_role() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(claim_role_with_pin(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/role-pins', methods=['GET', 'OPTIONS'])
def handle_get_role_pins() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    event = {
        'queryStringParameters': {
            'code': request.args.get('code', ''),
            'pid': request.args.get('pid', ''),
        },
        'headers': {k: v for k, v in request.headers},
    }
    return _flask_response(get_role_pins(event))


@app.route('/admin/session-pins', methods=['GET', 'OPTIONS'])
def handle_admin_session_pins() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    event = {
        'queryStringParameters': {
            'code': request.args.get('code', ''),
        },
        'headers': {k: v for k, v in request.headers},
    }
    return _flask_response(admin_get_session_pins(event))


@app.route('/vote', methods=['POST', 'OPTIONS'])
def handle_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(vote_on_response(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/voting', methods=['POST', 'OPTIONS'])
def handle_facilitator_voting() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(control_voting(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/remove-vote', methods=['POST', 'OPTIONS'])
def handle_facilitator_remove_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(facilitator_remove_vote(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/remove-response', methods=['POST', 'OPTIONS'])
def handle_facilitator_remove_response() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(facilitator_remove_response(_build_event(request.get_data(as_text=True))))


@app.route('/retract-vote', methods=['POST', 'OPTIONS'])
def handle_retract_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(retract_vote(_build_event(request.get_data(as_text=True))))


@app.route('/session/voter-types', methods=['GET', 'OPTIONS'])
def handle_get_session_voter_types() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    code = request.args.get('code', '').strip().upper()
    event = {'queryStringParameters': {'code': code}}
    return _flask_response(get_session_voter_types(event))


@app.route('/vote/summary-by-voter-type', methods=['POST', 'OPTIONS'])
def handle_get_vote_summary_by_voter_type() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(get_vote_summary_by_voter_type(_build_event(request.get_data(as_text=True))))


# ==================== Scenario Admin Routes ====================

@app.route('/admin/scenarios', methods=['GET', 'OPTIONS'])
def handle_admin_list_scenarios() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_list_scenarios(_build_event()))


@app.route('/admin/scenarios/get', methods=['POST', 'OPTIONS'])
def handle_admin_get_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_get_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/history', methods=['POST', 'OPTIONS'])
def handle_admin_get_scenario_history() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_get_scenario_history(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/create', methods=['POST', 'OPTIONS'])
def handle_admin_create_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_create_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/update', methods=['POST', 'OPTIONS'])
def handle_admin_update_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_update_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/delete', methods=['POST', 'OPTIONS'])
def handle_admin_delete_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_delete_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/restore', methods=['POST', 'OPTIONS'])
def handle_admin_restore_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_restore_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/enable', methods=['POST', 'OPTIONS'])
def handle_admin_enable_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_enable_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/disable', methods=['POST', 'OPTIONS'])
def handle_admin_disable_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_disable_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/diff', methods=['POST', 'OPTIONS'])
def handle_admin_diff_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_diff_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/admin/scenarios/import', methods=['POST', 'OPTIONS'])
def handle_admin_import_scenario() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_import_scenario(_build_event(request.get_data(as_text=True))))


@app.route('/scenarios', methods=['GET', 'OPTIONS'])
def handle_list_scenarios_public() -> Response:
    """Public endpoint to list available scenarios for session creation."""
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(list_scenarios_public())


# ==================== Tutorial Administration Routes ====================

@app.route('/tutorial/config', methods=['GET', 'OPTIONS'])
def handle_get_tutorial_config() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(get_tutorial_config({
        'queryStringParameters': {'id': request.args.get('id', 'default')}
    }))


@app.route('/admin/tutorials', methods=['GET', 'OPTIONS'])
def handle_admin_list_tutorials() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(admin_list_tutorials(_build_event()))


def _tutorial_admin_response(handler) -> Response:
    return _flask_response(handler(_build_event(request.get_data(as_text=True))))


@app.route('/admin/tutorials/get', methods=['POST', 'OPTIONS'])
def handle_admin_get_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_get_tutorial)


@app.route('/admin/tutorials/history', methods=['POST', 'OPTIONS'])
def handle_admin_get_tutorial_history() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_get_tutorial_history)


@app.route('/admin/tutorials/create', methods=['POST', 'OPTIONS'])
def handle_admin_create_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_create_tutorial)


@app.route('/admin/tutorials/update', methods=['POST', 'OPTIONS'])
def handle_admin_update_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_update_tutorial)


@app.route('/admin/tutorials/diff', methods=['POST', 'OPTIONS'])
def handle_admin_diff_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_diff_tutorial)


@app.route('/admin/tutorials/import', methods=['POST', 'OPTIONS'])
def handle_admin_import_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_import_tutorial)


@app.route('/admin/tutorials/publish', methods=['POST', 'OPTIONS'])
def handle_admin_publish_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_publish_tutorial)


@app.route('/admin/tutorials/disable', methods=['POST', 'OPTIONS'])
def handle_admin_disable_tutorial() -> Response:
    if request.method == 'OPTIONS': return _flask_response({'statusCode': 200})
    return _tutorial_admin_response(admin_disable_tutorial)


@app.route('/facilitator/timer', methods=['POST', 'OPTIONS'])
def handle_timer() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(update_timer(_build_event(request.get_data(as_text=True))))


@app.route('/present/auth', methods=['POST', 'OPTIONS'])
def handle_present_auth() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(present_auth(_build_event(request.get_data(as_text=True))))


# ==================== Scoring Routes ====================

@app.route('/sprint/engineer-vote', methods=['POST', 'OPTIONS'])
def handle_submit_engineer_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(submit_engineer_sprint_vote(_build_event(request.get_data(as_text=True))))


@app.route('/sprint/stakeholder-vote', methods=['POST', 'OPTIONS'])
def handle_submit_stakeholder_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(submit_stakeholder_sprint_vote(_build_event(request.get_data(as_text=True))))


@app.route('/sprint/head-to-head-vote', methods=['POST', 'OPTIONS'])
def handle_submit_head_to_head_vote() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(submit_head_to_head_vote(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/set-phase', methods=['POST', 'OPTIONS'])
def handle_facilitator_set_phase() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(facilitator_set_phase(_build_event(request.get_data(as_text=True))))


@app.route('/sprint/question-status', methods=['POST', 'OPTIONS'])
def handle_get_sprint_question_status() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(get_sprint_question_status(_build_event(request.get_data(as_text=True))))


@app.route('/session/scores', methods=['POST', 'OPTIONS'])
def handle_get_session_scores() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(get_session_scores(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/reveal-scores', methods=['POST', 'OPTIONS'])
def handle_facilitator_reveal_scores() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(facilitator_reveal_scores(_build_event(request.get_data(as_text=True))))


@app.route('/facilitator/toggle-scoreboard', methods=['POST', 'OPTIONS'])
def handle_facilitator_toggle_scoreboard() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    return _flask_response(facilitator_toggle_scoreboard(_build_event(request.get_data(as_text=True))))


def _parse_version(version_str: str) -> tuple:
    """Parse version string into tuple for comparison."""
    try:
        parts = version_str.strip().split('.')
        return tuple(int(p) for p in parts)
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _detect_environment() -> str:
    """Detect environment from BUILD_ENV."""
    build_env = os.environ.get('BUILD_ENV', '').strip().lower()
    if build_env in ('dev', 'development'):
        return 'dev'
    if build_env == 'tst':
        return 'tst'
    if build_env in ('prod', 'production'):
        return 'prod'
    
    # Default: prod
    return 'prod'


@app.route('/version', methods=['GET', 'OPTIONS'])
def handle_version() -> Response:
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    
    env_version = os.environ.get('RELEASE_VERSION', '').strip()
    file_version = None
    
    # Try to read from releaseversion.txt
    version_file = Path(__file__).parent / 'releaseversion.txt'
    if version_file.exists():
        try:
            file_version = version_file.read_text().strip()
        except Exception:
            file_version = None
    
    # Determine which version to use and if there's a mismatch
    mismatch = False
    version = None
    
    if env_version and file_version:
        # Both exist - check for mismatch and use highest
        mismatch = env_version != file_version
        if _parse_version(env_version) >= _parse_version(file_version):
            version = env_version
        else:
            version = file_version
    elif env_version:
        version = env_version
    elif file_version:
        version = file_version
    else:
        version = 'unknown'
    
    environment = _detect_environment()
    
    result = {
        'version': version,
        'mismatch': mismatch,
        'environment': environment,
    }
    
    # Only include source details when there's a mismatch
    if mismatch:
        result['sources'] = {
            'envVar': {'name': 'RELEASE_VERSION', 'version': env_version},
            'file': {'name': 'releaseversion.txt', 'version': file_version},
        }
    
    return _flask_response({
        'statusCode': 200,
        'body': json.dumps(result)
    })


@app.route('/admin/buildinfo', methods=['GET', 'OPTIONS'])
def handle_admin_buildinfo() -> Response:
    """Admin-protected endpoint to get detailed build information."""
    if request.method == 'OPTIONS':
        return _flask_response({'statusCode': 200})
    
    # Verify admin token
    admin_token = os.environ.get('ADMIN_TOKEN', 'admin')
    provided_token = request.headers.get('X-Admin-Token', '')
    
    if provided_token != admin_token:
        return _flask_response({
            'statusCode': 401,
            'body': json.dumps({'ok': False, 'message': 'Invalid admin token.'})
        })
    
    # Try to read buildinfo.json
    buildinfo_file = Path(__file__).parent / 'buildinfo.json'
    if buildinfo_file.exists():
        try:
            buildinfo = json.loads(buildinfo_file.read_text())
            return _flask_response({
                'statusCode': 200,
                'body': json.dumps({'ok': True, 'buildInfo': buildinfo})
            })
        except Exception as e:
            return _flask_response({
                'statusCode': 500,
                'body': json.dumps({'ok': False, 'message': f'Error reading buildinfo: {str(e)}'})
            })
    
    # No buildinfo.json - return minimal info
    return _flask_response({
        'statusCode': 200,
        'body': json.dumps({
            'ok': True,
            'buildInfo': {
                'version': 'dev',
                'buildTime': None,
                'commitHash': None,
                'commitHashShort': None,
                'commitMessage': None,
                'commitTime': None,
                'author': None,
            }
        })
    })

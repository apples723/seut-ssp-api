#!/usr/bin/env python3
"""
Simple local development server for the API.
Run with: python local_server.py
Server runs on http://localhost:8000
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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


class APIHandler(BaseHTTPRequestHandler):
    def _send_response(self, result: dict) -> None:
        status = result.get('statusCode', 200)
        self.send_response(status)
        for key, value in result.get('headers', {}).items():
            self.send_header(key, value)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Admin-Token')
        self.end_headers()
        self.wfile.write(result.get('body', '{}').encode())

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Admin-Token')
        self.end_headers()

    def _build_event(self, body: str = '{}') -> dict:
        """Build an event dict with body and headers for service functions."""
        headers = {k: v for k, v in self.headers.items()}
        return {'body': body, 'headers': headers}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/session':
            code = query.get('code', [None])[0]
            pid = query.get('pid', [None])[0]
            if code:
                session = repo.get_session_by_code(code.upper(), participant_id=pid)
                if session:
                    # If a participant ID was provided but that participant no longer
                    # exists in the session (e.g. removed by facilitator), reject.
                    if pid and not any(p.id == pid for p in session.participants):
                        result = {
                            'statusCode': 403,
                            'headers': {
                                'Content-Type': 'application/json',
                                'Access-Control-Allow-Origin': '*',
                            },
                            'body': json.dumps({'ok': False, 'message': 'You have been removed from this session.'}),
                        }
                    else:
                        result = {
                            'statusCode': 200,
                            'headers': {
                                'Content-Type': 'application/json',
                                'Access-Control-Allow-Origin': '*',
                            },
                            'body': json.dumps(session.to_dict()),
                        }
                else:
                    result = {
                        'statusCode': 404,
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*',
                        },
                        'body': json.dumps({'ok': False, 'message': 'Session not found.'}),
                    }
            else:
                result = get_session()
            self._send_response(result)
        elif path == '/admin/log':
            self._send_response(get_session_log())
        elif path == '/admin/sessions':
            self._send_response(admin_list_sessions(self._build_event()))
        elif path == '/admin/scenarios':
            self._send_response(admin_list_scenarios(self._build_event()))
        elif path == '/admin/tutorials':
            self._send_response(admin_list_tutorials(self._build_event()))
        elif path == '/tutorial/config':
            self._send_response(get_tutorial_config({
                'queryStringParameters': {'id': query.get('id', ['default'])[0]}
            }))
        elif path == '/scenarios':
            self._send_response(list_scenarios_public())
        elif path == '/session/roles':
            code = query.get('code', [None])[0]
            if not code:
                self._send_response({
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({'ok': False, 'message': 'Session code is required.'}),
                })
            else:
                self._send_response(get_session_roles(code.strip().upper()))
        elif path == '/session/voter-types':
            code = query.get('code', [None])[0]
            event = {'queryStringParameters': {'code': code or ''}}
            self._send_response(get_session_voter_types(event))
        elif path == '/facilitator/role-pins':
            code = query.get('code', [None])[0]
            pid = query.get('pid', [None])[0]
            event = {
                'queryStringParameters': {'code': code or '', 'pid': pid or ''},
                'headers': {k: v for k, v in self.headers.items()},
            }
            self._send_response(get_role_pins(event))
        elif path == '/admin/session-pins':
            code = query.get('code', [None])[0]
            event = {
                'queryStringParameters': {'code': code or ''},
                'headers': {k: v for k, v in self.headers.items()},
            }
            self._send_response(admin_get_session_pins(event))
        elif path == '/version':
            self._send_response(self._get_version())
        elif path == '/admin/buildinfo':
            self._send_response(self._get_admin_buildinfo())
        else:
            self.send_error(404)

    def _get_version(self) -> dict:
        """Return version info from env var or releaseversion.txt."""
        env_version = os.environ.get('RELEASE_VERSION', '').strip()
        file_version = None
        
        version_file = Path(__file__).parent / 'releaseversion.txt'
        if version_file.exists():
            try:
                file_version = version_file.read_text().strip()
            except Exception:
                pass
        
        # Determine which version to use and if there's a mismatch
        mismatch = False
        version = None
        
        if env_version and file_version:
            mismatch = env_version != file_version
            # Use higher version
            if self._parse_version(env_version) >= self._parse_version(file_version):
                version = env_version
            else:
                version = file_version
        elif env_version:
            version = env_version
        elif file_version:
            version = file_version
        else:
            version = 'unknown'
        
        environment = self._detect_environment()
        
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
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
            },
            'body': json.dumps(result),
        }

    def _parse_version(self, version_str: str) -> tuple:
        """Parse version string into tuple for comparison."""
        try:
            parts = version_str.strip().split('.')
            return tuple(int(p) for p in parts)
        except (ValueError, AttributeError):
            return (0, 0, 0)

    def _detect_environment(self) -> str:
        """Detect environment from BUILD_ENV."""
        build_env = os.environ.get('BUILD_ENV', '').strip().lower()
        if build_env in ('dev', 'development'):
            return 'dev'
        if build_env == 'tst':
            return 'tst'
        if build_env in ('prod', 'production'):
            return 'prod'
        # Default for local dev
        return 'dev'

    def _get_admin_buildinfo(self) -> dict:
        """Return build info (admin-protected)."""
        admin_token = os.environ.get('ADMIN_TOKEN', 'admin')
        provided_token = self.headers.get('X-Admin-Token', '')
        
        if provided_token != admin_token:
            return {
                'statusCode': 401,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                },
                'body': json.dumps({'ok': False, 'message': 'Invalid admin token.'}),
            }
        
        buildinfo_file = Path(__file__).parent / 'buildinfo.json'
        if buildinfo_file.exists():
            try:
                buildinfo = json.loads(buildinfo_file.read_text())
                return {
                    'statusCode': 200,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                    },
                    'body': json.dumps({'ok': True, 'buildInfo': buildinfo}),
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                    },
                    'body': json.dumps({'ok': False, 'message': f'Error reading buildinfo: {str(e)}'}),
                }
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
            },
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
            }),
        }

    def do_POST(self) -> None:
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length else '{}'
        event = self._build_event(body)

        path = urlparse(self.path).path

        if path == '/join':
            result = join_session(event)
        elif path == '/create':
            result = create_session(event)
        elif path == '/responses':
            result = submit_response(event)
        elif path == '/facilitator/next-sprint':
            result = next_sprint(event)
        elif path == '/facilitator/prev-sprint':
            result = prev_sprint(event)
        elif path == '/facilitator/start-session':
            result = start_session(event)
        elif path == '/facilitator/end-session':
            result = end_session(event)
        elif path == '/facilitator/timer':
            result = update_timer(event)
        elif path == '/chat':
            result = send_chat(event)
        elif path == '/admin/end-session':
            result = admin_end_session(event)
        elif path == '/present/auth':
            result = present_auth(event)
        elif path == '/admin/scenarios/get':
            result = admin_get_scenario(event)
        elif path == '/admin/scenarios/history':
            result = admin_get_scenario_history(event)
        elif path == '/admin/scenarios/create':
            result = admin_create_scenario(event)
        elif path == '/admin/scenarios/update':
            result = admin_update_scenario(event)
        elif path == '/admin/scenarios/delete':
            result = admin_delete_scenario(event)
        elif path == '/admin/scenarios/restore':
            result = admin_restore_scenario(event)
        elif path == '/admin/scenarios/enable':
            result = admin_enable_scenario(event)
        elif path == '/admin/scenarios/disable':
            result = admin_disable_scenario(event)
        elif path == '/admin/scenarios/diff':
            result = admin_diff_scenario(event)
        elif path == '/admin/scenarios/import':
            result = admin_import_scenario(event)
        elif path == '/admin/tutorials/get':
            result = admin_get_tutorial(event)
        elif path == '/admin/tutorials/history':
            result = admin_get_tutorial_history(event)
        elif path == '/admin/tutorials/create':
            result = admin_create_tutorial(event)
        elif path == '/admin/tutorials/update':
            result = admin_update_tutorial(event)
        elif path == '/admin/tutorials/diff':
            result = admin_diff_tutorial(event)
        elif path == '/admin/tutorials/import':
            result = admin_import_tutorial(event)
        elif path == '/admin/tutorials/publish':
            result = admin_publish_tutorial(event)
        elif path == '/admin/tutorials/disable':
            result = admin_disable_tutorial(event)
        elif path == '/session/select-role':
            result = select_role(event)
        elif path == '/facilitator/assign-role':
            result = assign_player_role(event)
        elif path == '/facilitator/remove-player':
            result = remove_player(event)
        elif path == '/vote':
            result = vote_on_response(event)
        elif path == '/facilitator/voting':
            result = control_voting(event)
        elif path == '/facilitator/remove-vote':
            result = facilitator_remove_vote(event)
        elif path == '/facilitator/remove-response':
            result = facilitator_remove_response(event)
        elif path == '/retract-vote':
            result = retract_vote(event)
        elif path == '/sprint/engineer-vote':
            result = submit_engineer_sprint_vote(event)
        elif path == '/sprint/stakeholder-vote':
            result = submit_stakeholder_sprint_vote(event)
        elif path == '/sprint/head-to-head-vote':
            result = submit_head_to_head_vote(event)
        elif path == '/facilitator/set-phase':
            result = facilitator_set_phase(event)
        elif path == '/sprint/question-status':
            result = get_sprint_question_status(event)
        elif path == '/session/scores':
            result = get_session_scores(event)
        elif path == '/facilitator/reveal-scores':
            result = facilitator_reveal_scores(event)
        elif path == '/facilitator/toggle-scoreboard':
            result = facilitator_toggle_scoreboard(event)
        elif path == '/session/claim-role':
            result = claim_role_with_pin(event)
        elif path == '/vote/summary-by-voter-type':
            result = get_vote_summary_by_voter_type(event)
        else:
            self.send_error(404)
            return

        self._send_response(result)

    def log_message(self, format: str, *args) -> None:
        print(f"[API] {args[0]}")


def main() -> None:
    port = 8000
    server = HTTPServer(('0.0.0.0', port), APIHandler)
    print(f" API server running at http://localhost:{port}")
    print(f"   Demo session code: DEMO-4829")
    print(f"   Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n Server stopped")


if __name__ == '__main__':
    main()

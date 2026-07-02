import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from handlers.get_session import handler as get_session_handler
from handlers.submit_response import handler as submit_response_handler
from handlers.next_sprint import handler as next_sprint_handler
from handlers.join_session import handler as join_session_handler
from handlers.create_session import handler as create_session_handler


def test_get_session_returns_session():
    response = get_session_handler({}, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['scenarioName'] == 'Mental Health App Role-Play'


def test_submit_response():
    response = submit_response_handler({'body': json.dumps({'text': 'Hello world', 'participantName': 'Grant'})}, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['ok'] is True


def test_next_sprint():
    response = next_sprint_handler({}, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['ok'] is True


# Join Session Tests

def test_join_session_returns_400_for_missing_fields():
    """join_session returns 400 for missing required fields."""
    response = join_session_handler({'body': json.dumps({})}, None)
    assert response['statusCode'] == 400
    payload = json.loads(response['body'])
    assert payload['ok'] is False
    assert payload['message'] == 'Missing required fields.'


def test_join_session_returns_400_for_invalid_role():
    """join_session returns 400 for invalid role."""
    response = join_session_handler({
        'body': json.dumps({
            'sessionCode': 'DEMO-4829',
            'participantName': 'Test User',
            'role': 'invalid_role'
        })
    }, None)
    assert response['statusCode'] == 400
    payload = json.loads(response['body'])
    assert payload['ok'] is False
    assert payload['message'] == 'Invalid role.'


def test_join_session_returns_404_for_unknown_session():
    """join_session returns 404 for unknown session code."""
    response = join_session_handler({
        'body': json.dumps({
            'sessionCode': 'XXXX-9999',
            'participantName': 'Test User',
            'role': 'player'
        })
    }, None)
    assert response['statusCode'] == 404
    payload = json.loads(response['body'])
    assert payload['ok'] is False
    assert payload['message'] == 'Session not found. Please check your code.'


def test_join_session_returns_200_for_valid_session():
    """join_session returns 200 with session data for valid session code."""
    response = join_session_handler({
        'body': json.dumps({
            'sessionCode': 'DEMO-4829',
            'participantName': 'Test User',
            'role': 'player'
        })
    }, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['ok'] is True
    assert 'session' in payload
    assert payload['session']['sessionId'] == 'DEMO-4829'
    assert payload['session']['participant']['role'] == 'player'
    assert payload['session']['participant']['name'] == 'Test User'


def test_join_session_normalizes_session_code_to_uppercase():
    """join_session normalizes session code to uppercase."""
    response = join_session_handler({
        'body': json.dumps({
            'sessionCode': 'demo-4829',
            'participantName': 'Test User',
            'role': 'viewer'
        })
    }, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['ok'] is True
    assert payload['session']['sessionId'] == 'DEMO-4829'


def test_join_session_includes_cors_headers():
    """join_session includes CORS headers in response."""
    response = join_session_handler({
        'body': json.dumps({
            'sessionCode': 'DEMO-4829',
            'participantName': 'Test User',
            'role': 'facilitator'
        })
    }, None)
    assert 'headers' in response
    assert response['headers'].get('Access-Control-Allow-Origin') == '*'


# Create Session Tests

def test_create_session_returns_400_for_missing_facilitator_name():
    """create_session returns 400 for missing facilitator name."""
    response = create_session_handler({'body': json.dumps({})}, None)
    assert response['statusCode'] == 400
    payload = json.loads(response['body'])
    assert payload['ok'] is False
    assert payload['message'] == 'Facilitator name is required.'


def test_create_session_returns_200_with_new_session():
    """create_session returns 200 with new session data."""
    response = create_session_handler({
        'body': json.dumps({
            'facilitatorName': 'Professor Smith'
        })
    }, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['ok'] is True
    assert 'session' in payload
    assert 'sessionCode' in payload
    # Verify session code format: XXXX-NNNN
    code = payload['sessionCode']
    assert len(code) == 9
    assert code[4] == '-'
    assert code[:4].isalpha() and code[:4].isupper()
    assert code[5:].isdigit()


def test_create_session_includes_cors_headers():
    """create_session includes CORS headers in response."""
    response = create_session_handler({
        'body': json.dumps({
            'facilitatorName': 'Test Facilitator'
        })
    }, None)
    assert 'headers' in response
    assert response['headers'].get('Access-Control-Allow-Origin') == '*'


def test_create_session_sets_facilitator_role():
    """create_session sets the participant role to facilitator."""
    response = create_session_handler({
        'body': json.dumps({
            'facilitatorName': 'Dr. Jones'
        })
    }, None)
    assert response['statusCode'] == 200
    payload = json.loads(response['body'])
    assert payload['session']['participant']['role'] == 'facilitator'
    assert payload['session']['participant']['name'] == 'Dr. Jones'

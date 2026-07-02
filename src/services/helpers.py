"""Shared service utilities: response formatting, IP extraction, admin auth."""
import json
import os
from typing import Any, Dict

ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'admin')  # default for local dev


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body),
    }


def get_client_ip(event: Dict[str, Any]) -> str:
    """Extract client IP from request, handling nginx proxy."""
    headers = event.get('headers') or {}
    # X-Forwarded-For may contain multiple IPs; first is the client
    forwarded = headers.get('X-Forwarded-For') or headers.get('x-forwarded-for') or ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    # Fall back to X-Real-IP or direct connection
    return headers.get('X-Real-IP') or headers.get('x-real-ip') or event.get('client_ip', 'unknown')


def check_admin_token(event: Dict[str, Any]) -> bool:
    """Validate the admin token from the X-Admin-Token header."""
    headers = event.get('headers') or {}
    token = headers.get('X-Admin-Token') or headers.get('x-admin-token') or ''
    return token == ADMIN_TOKEN

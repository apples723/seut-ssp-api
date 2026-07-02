"""Handler for present mode authentication."""
from src.service import present_auth


def handler(event, context):
    return present_auth(event)

from src.service import get_session_log


def handler(event, context):
    return get_session_log()

from src.service import end_session


def handler(event, context):
    return end_session(event)

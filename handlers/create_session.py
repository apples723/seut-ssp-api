from src.service import create_session


def handler(event, context):
    return create_session(event)

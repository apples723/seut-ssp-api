from src.service import join_session


def handler(event, context):
    return join_session(event)

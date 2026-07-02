from src.service import get_session


def handler(event, context):
    return get_session()

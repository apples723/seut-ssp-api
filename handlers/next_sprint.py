from src.service import next_sprint


def handler(event, context):
    return next_sprint()

from src.service import send_chat


def handler(event, context):
    return send_chat(event)

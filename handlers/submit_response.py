from src.service import submit_response


def handler(event, context):
    return submit_response(event)

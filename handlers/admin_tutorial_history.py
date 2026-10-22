from src.service import admin_get_tutorial_history


def handler(event, context):
    return admin_get_tutorial_history(event)

from src.service import admin_get_tutorial


def handler(event, context):
    return admin_get_tutorial(event)

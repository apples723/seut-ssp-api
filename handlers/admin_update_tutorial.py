from src.service import admin_update_tutorial


def handler(event, context):
    return admin_update_tutorial(event)

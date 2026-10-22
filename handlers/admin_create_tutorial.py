from src.service import admin_create_tutorial


def handler(event, context):
    return admin_create_tutorial(event)

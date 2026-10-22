from src.service import admin_disable_tutorial


def handler(event, context):
    return admin_disable_tutorial(event)

from src.service import admin_publish_tutorial


def handler(event, context):
    return admin_publish_tutorial(event)

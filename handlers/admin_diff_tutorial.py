from src.service import admin_diff_tutorial


def handler(event, context):
    return admin_diff_tutorial(event)

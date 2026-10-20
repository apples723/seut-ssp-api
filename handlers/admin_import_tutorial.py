from src.service import admin_import_tutorial


def handler(event, context):
    return admin_import_tutorial(event)

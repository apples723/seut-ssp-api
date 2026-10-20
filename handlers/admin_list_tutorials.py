from src.service import admin_list_tutorials


def handler(event, context):
    return admin_list_tutorials(event)

from src.service import get_tutorial_config


def handler(event, context):
    return get_tutorial_config(event)

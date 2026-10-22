"""Lambda handler for activating a published tutorial."""
from src.service import admin_activate_tutorial


def handler(event, context):
    return admin_activate_tutorial(event)

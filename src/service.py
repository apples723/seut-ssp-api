"""Service layer — thin re-export barrel for backwards compatibility.

All business logic has been split into domain modules under src/services/.
This file re-exports everything so existing imports (wsgi.py, handlers/, tests)
continue to work without modification.
"""
from .services import (
    # Session
    get_session,
    create_session,
    start_session,
    end_session,
    # Participants
    join_session,
    get_session_roles,
    select_role,
    assign_player_role,
    remove_player,
    # Responses
    submit_response,
    # Sprint
    next_sprint,
    prev_sprint,
    update_timer,
    # Chat
    send_chat,
    get_session_log,
    # Voting
    vote_on_response,
    control_voting,
    facilitator_remove_vote,
    facilitator_remove_response,
    retract_vote,
    get_session_voter_types,
    get_vote_summary_by_voter_type,
    # Scoring
    submit_engineer_sprint_vote,
    submit_stakeholder_sprint_vote,
    submit_head_to_head_vote,
    facilitator_set_phase,
    get_sprint_question_status,
    get_session_scores,
    facilitator_reveal_scores,
    facilitator_toggle_scoreboard,
    # Role PINs
    claim_role_with_pin,
    get_role_pins,
    admin_get_session_pins,
    # Scenarios
    admin_list_scenarios,
    admin_get_scenario,
    admin_get_scenario_history,
    admin_create_scenario,
    admin_update_scenario,
    admin_delete_scenario,
    admin_restore_scenario,
    admin_enable_scenario,
    admin_disable_scenario,
    admin_diff_scenario,
    admin_import_scenario,
    list_scenarios_public,
    # Tutorials
    get_tutorial_config,
    admin_list_tutorials,
    admin_get_tutorial,
    admin_get_tutorial_history,
    admin_create_tutorial,
    admin_update_tutorial,
    admin_diff_tutorial,
    admin_import_tutorial,
    admin_publish_tutorial,
    admin_disable_tutorial,
    admin_activate_tutorial,
    # Present
    present_auth,
    # Admin
    admin_list_sessions,
    admin_end_session,
)

# Legacy re-export of response helper for any code that imported it directly
from .services.helpers import response

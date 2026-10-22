"""Public and admin services for versioned tutorial configuration."""
import json
from typing import Any, Dict, Optional, Tuple

from ..repository_factory import repo
from ..tutorial_repository import (
    ActiveTutorialDisableError,
    TUTORIAL_MAX_ID_BYTES,
    TutorialActivationConflictError,
    TutorialAlreadyExistsError,
    TutorialConflictError,
    TutorialNotActivatableError,
    TutorialValidationError,
    recursive_diff,
    validate_tutorial_config,
)
from .helpers import check_admin_token, response


def _parse_body(event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None, response(400, {
            "ok": False,
            "message": "Malformed JSON body.",
            "errors": [{"path": "body", "message": "Must contain valid JSON.", "code": "malformed_json"}],
        })
    if not isinstance(body, dict):
        return None, response(400, {
            "ok": False,
            "message": "Invalid request body.",
            "errors": [{"path": "body", "message": "Must be a JSON object.", "code": "object_required"}],
        })
    return body, None


def _admin(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not check_admin_token(event):
        return response(401, {"ok": False, "message": "Invalid admin token."})
    return None


def _field_error(path: str, message: str, code: str = "required") -> Dict[str, Any]:
    return response(400, {
        "ok": False,
        "message": "Invalid request.",
        "errors": [{"path": path, "message": message, "code": code}],
    })


def _tutorial_id(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    value = body.get("tutorialId")
    if not isinstance(value, str) or not value.strip():
        return None, _field_error("tutorialId", "Must be a non-empty string.", "nonempty_string")
    value = value.strip()
    try:
        value_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None, _field_error("tutorialId", "Must contain valid Unicode.", "invalid_unicode")
    if value_size > TUTORIAL_MAX_ID_BYTES:
        return None, _field_error("tutorialId", f"Must be at most {TUTORIAL_MAX_ID_BYTES} UTF-8 bytes.", "max_bytes")
    if "#" in value:
        return None, _field_error("tutorialId", "Must not contain '#'.", "invalid_id")
    return value, None


def _created_by(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    value = body.get("createdBy", "admin")
    if not isinstance(value, str) or not value.strip():
        return None, _field_error("createdBy", "Must be a non-empty string.", "nonempty_string")
    value = value.strip()
    try:
        value_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None, _field_error("createdBy", "Must contain valid Unicode.", "invalid_unicode")
    if value_size > 256:
        return None, _field_error("createdBy", "Must be at most 256 UTF-8 bytes.", "max_bytes")
    return value, None


def _integer(body: Dict[str, Any], field: str, required: bool = True) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    value = body.get(field)
    if value is None and not required:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None, _field_error(field, "Must be a positive integer.", "positive_integer")
    return value, None


def _nonnegative_integer(
    body: Dict[str, Any], field: str
) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, _field_error(
            field, "Must be a nonnegative integer.", "nonnegative_integer"
        )
    return value, None


def _config(body: Dict[str, Any], tutorial_id: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    config = body.get("config")
    try:
        validate_tutorial_config(config)
    except TutorialValidationError as error:
        return None, response(400, {
            "ok": False,
            "message": "Tutorial configuration is invalid.",
            "errors": [issue.to_dict() for issue in error.issues],
        })
    if tutorial_id is not None and config["id"] != tutorial_id:
        return None, _field_error("config.id", "Must match tutorialId.", "id_mismatch")
    return config, None


def _conflict(error: TutorialConflictError) -> Dict[str, Any]:
    return response(409, {
        "ok": False,
        "message": "expectedRevision is stale.",
        "currentRevision": error.current_revision,
    })


def get_tutorial_config(event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return an explicitly requested published tutorial or the active tutorial."""
    event = event or {}
    params = event.get("queryStringParameters") or {}
    explicit_id = "id" in params
    if explicit_id:
        tutorial_id = params["id"]
        if not isinstance(tutorial_id, str) or not tutorial_id.strip():
            return _field_error("id", "Must be a non-empty string.", "nonempty_string")
        tutorial_id = tutorial_id.strip()
        try:
            tutorial_id_size = len(tutorial_id.encode("utf-8"))
        except UnicodeEncodeError:
            return _field_error("id", "Must contain valid Unicode.", "invalid_unicode")
        if tutorial_id_size > TUTORIAL_MAX_ID_BYTES:
            return _field_error("id", f"Must be at most {TUTORIAL_MAX_ID_BYTES} UTF-8 bytes.", "max_bytes")
        if "#" in tutorial_id:
            return _field_error("id", "Must not contain '#'.", "invalid_id")
    else:
        tutorial_id = repo.get_tutorial_activation().active_tutorial_id
        if tutorial_id is None:
            return response(404, {"ok": False, "message": "Published tutorial not found."})
    tutorial = repo.get_published_tutorial(tutorial_id)
    if not tutorial and not explicit_id:
        # Activation and publication are separate records. If a switch completed
        # between those reads, retry once with the newly selected tutorial.
        current_id = repo.get_tutorial_activation().active_tutorial_id
        if current_id is not None and current_id != tutorial_id:
            tutorial = repo.get_published_tutorial(current_id)
    if not tutorial:
        return response(404, {"ok": False, "message": "Published tutorial not found."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})


def admin_list_tutorials(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    activation = repo.get_tutorial_activation()
    tutorials = []
    for item in repo.list_tutorials():
        published = repo.get_published_tutorial(item.id)
        active = item.id == activation.active_tutorial_id
        summary = item.to_dict()
        summary.update({
            "active": active,
            "publishedVersion": published.version if published else None,
            "canActivate": published is not None and not active,
        })
        tutorials.append(summary)
    return response(200, {
        "ok": True,
        "tutorials": tutorials,
        **activation.to_dict(),
    })


def admin_activate_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    expected, error = _nonnegative_integer(body, "expectedActivationRevision")
    if error:
        return error
    if not repo.get_tutorial(tutorial_id):
        return response(404, {"ok": False, "message": "Tutorial not found."})
    try:
        activation = repo.activate_tutorial(tutorial_id, expected)
    except TutorialActivationConflictError as conflict:
        return response(409, {
            "ok": False,
            "message": "expectedActivationRevision is stale.",
            "currentActivationRevision": conflict.current_activation_revision,
        })
    except TutorialNotActivatableError:
        return response(409, {
            "ok": False,
            "message": "Tutorial must be published and enabled before activation.",
        })
    return response(200, {"ok": True, **activation.to_dict()})


def admin_get_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    version, error = _integer(body, "version", required=False)
    if error:
        return error
    tutorial = repo.get_tutorial(tutorial_id, version)
    if not tutorial:
        return response(404, {"ok": False, "message": "Tutorial not found."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})


def admin_get_tutorial_history(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    versions = repo.get_tutorial_history(tutorial_id)
    if not versions:
        return response(404, {"ok": False, "message": "Tutorial not found."})
    return response(200, {
        "ok": True, "tutorialId": tutorial_id,
        "versions": [version.to_dict() for version in versions],
    })


def admin_create_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    config, error = _config(body)
    if error:
        return error
    tutorial_id = config["id"]
    created_by, error = _created_by(body)
    if error:
        return error
    try:
        tutorial = repo.create_tutorial(config, created_by)
    except TutorialAlreadyExistsError:
        return response(409, {"ok": False, "message": "Tutorial already exists."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})


def admin_update_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    expected, error = _integer(body, "expectedRevision")
    if error:
        return error
    config, error = _config(body, tutorial_id)
    if error:
        return error
    created_by, error = _created_by(body)
    if error:
        return error
    try:
        tutorial = repo.update_tutorial(
            tutorial_id, config, expected, created_by
        )
    except TutorialConflictError as conflict:
        return _conflict(conflict)
    if not tutorial:
        return response(404, {"ok": False, "message": "Tutorial not found."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})


def admin_diff_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    config, error = _config(body, tutorial_id)
    if error:
        return error
    version, error = _integer(body, "version", required=False)
    if error:
        return error
    current = repo.get_tutorial(tutorial_id, version)
    if not current:
        return response(404, {"ok": False, "message": "Tutorial not found."})
    changes = recursive_diff(current.config, config)
    return response(200, {"ok": True, "diff": {
        "tutorialId": tutorial_id, "fromVersion": current.version,
        "hasChanges": bool(changes), "changes": changes,
    }})


def admin_import_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and preview create/update without writing."""
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    config, error = _config(body)
    if error:
        return error
    tutorial_id = config["id"]
    current = repo.get_tutorial(tutorial_id)
    if not current:
        return response(200, {
            "ok": True, "action": "create", "tutorialId": tutorial_id,
            "config": config, "expectedRevision": 0,
            "diff": {"tutorialId": tutorial_id, "hasChanges": True,
                     "changes": [{"path": "config", "type": "added", "new": config}]},
        })
    changes = recursive_diff(current.config, config)
    return response(200, {
        "ok": True, "action": "update", "tutorialId": tutorial_id,
        "config": config, "expectedRevision": current.revision,
        "existingVersion": current.version, "existingRevision": current.revision,
        "diff": {"tutorialId": tutorial_id, "fromVersion": current.version,
                 "hasChanges": bool(changes), "changes": changes},
    })


def admin_publish_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    expected, error = _integer(body, "expectedRevision")
    if error:
        return error
    version, error = _integer(body, "version", required=False)
    if error:
        return error
    selected = repo.get_tutorial(tutorial_id, version)
    if not selected:
        return response(404, {"ok": False, "message": "Tutorial version not found."})
    try:
        validate_tutorial_config(selected.config)
        tutorial = repo.publish_tutorial(tutorial_id, selected.version, expected)
    except TutorialValidationError as validation:
        return response(400, {
            "ok": False, "message": "Tutorial configuration is invalid.",
            "errors": [issue.to_dict() for issue in validation.issues],
        })
    except TutorialConflictError as conflict:
        return _conflict(conflict)
    if not tutorial:
        return response(404, {"ok": False, "message": "Tutorial version not found."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})


def admin_disable_tutorial(event: Dict[str, Any]) -> Dict[str, Any]:
    denied = _admin(event)
    if denied:
        return denied
    body, error = _parse_body(event)
    if error:
        return error
    tutorial_id, error = _tutorial_id(body)
    if error:
        return error
    expected, error = _integer(body, "expectedRevision")
    if error:
        return error
    try:
        tutorial = repo.disable_tutorial(tutorial_id, expected)
    except ActiveTutorialDisableError:
        return response(409, {
            "ok": False,
            "message": "Activate another tutorial before disabling the active tutorial.",
        })
    except TutorialConflictError as conflict:
        return _conflict(conflict)
    if not tutorial:
        return response(404, {"ok": False, "message": "Published tutorial not found."})
    return response(200, {"ok": True, "tutorial": tutorial.to_dict()})

import copy
import sqlite3

import pytest

from src.tutorial_repository import (
    TUTORIAL_STEP_IDS,
    TutorialConflictError,
    TutorialRepository,
    TutorialValidationError,
    validate_tutorial_config,
)


def tutorial_config(tutorial_id: str = "default") -> dict:
    required_slots = {
        "welcome": "teaching-arc",
        "cascade": "teaching-arc",
        "wrapup": "actions",
    }
    steps = []
    for index, step_id in enumerate(TUTORIAL_STEP_IDS, start=1):
        body = [{"markdown": f"Copy for {step_id}."}]
        if step_id in required_slots:
            body.append({"slot": required_slots[step_id]})
        steps.append({
            "id": step_id,
            "navigationLabel": f"Step {index}",
            "eyebrow": f"{index} · Tutorial",
            "title": step_id.title(),
            "body": body,
            "differences": [{
                "omitted": "The tutorial is simplified.",
                "actualBehavior": "The live platform has complete behavior.",
            }],
        })
    return {
        "schemaVersion": 1,
        "id": tutorial_id,
        "title": "Platform tutorial",
        "steps": steps,
    }


def repository() -> TutorialRepository:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return TutorialRepository(connection)


def test_validation_rejects_noncanonical_ids_and_mixed_body_blocks() -> None:
    whitespace_id = tutorial_config(" default")
    with pytest.raises(TutorialValidationError) as whitespace_error:
        validate_tutorial_config(whitespace_id)
    assert any(issue.path == "config.id" for issue in whitespace_error.value.issues)

    mixed_block = tutorial_config()
    mixed_block["steps"][1]["body"][0]["slot"] = "not-a-slot"
    with pytest.raises(TutorialValidationError) as block_error:
        validate_tutorial_config(mixed_block)
    assert any(issue.path.startswith("config.steps[1].body[0]") for issue in block_error.value.issues)


def test_disabled_resource_stays_disabled_until_a_draft_is_published() -> None:
    repo = repository()
    first = tutorial_config()
    repo.create_tutorial(first, "admin")
    repo.publish_tutorial("default", version=1, expected_revision=1)

    second = copy.deepcopy(first)
    second["title"] = "Second draft"
    repo.update_tutorial("default", second, expected_revision=2, created_by="admin")
    repo.disable_tutorial("default", expected_revision=3)

    third = copy.deepcopy(second)
    third["title"] = "Third draft"
    saved = repo.update_tutorial("default", third, expected_revision=4, created_by="admin")

    assert saved is not None and saved.version == 3
    assert repo.get_published_tutorial("default") is None
    listed = repo.list_tutorials()
    assert len(listed) == 1
    assert listed[0].version == 3
    assert listed[0].status == "disabled"

    published = repo.publish_tutorial("default", version=3, expected_revision=5)
    assert published is not None and published.status == "published"
    assert repo.get_published_tutorial("default").config["title"] == "Third draft"
    assert published.revision == 6


def test_lifecycle_revision_rejects_stale_writes() -> None:
    repo = repository()
    config = tutorial_config()
    created = repo.create_tutorial(config, "admin")
    assert created.revision == 1

    published = repo.publish_tutorial("default", version=1, expected_revision=1)
    assert published is not None and published.revision == 2

    changed = copy.deepcopy(config)
    changed["title"] = "Stale edit"
    with pytest.raises(TutorialConflictError) as stale:
        repo.update_tutorial("default", changed, expected_revision=1, created_by="admin")
    assert stale.value.current_revision == 2


def test_validation_rejects_storage_unsafe_payload_size() -> None:
    config = tutorial_config()
    config["steps"][0]["body"][0]["markdown"] = "界" * 120_000

    with pytest.raises(TutorialValidationError) as oversized:
        validate_tutorial_config(config)

    assert any(
        issue.path == "config" and issue.code == "max_bytes"
        for issue in oversized.value.issues
    )


def test_validation_rejects_lone_unicode_surrogates() -> None:
    config = tutorial_config()
    config["steps"][0]["body"][0]["markdown"] = "broken \ud800 text"

    with pytest.raises(TutorialValidationError) as invalid_unicode:
        validate_tutorial_config(config)

    assert any(
        issue.path == "config.steps[0].body[0].markdown"
        and issue.code == "invalid_unicode"
        for issue in invalid_unicode.value.issues
    )


def test_existing_sqlite_heads_gain_revision_from_latest_version() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE tutorial_heads (
            id TEXT PRIMARY KEY,
            latest_version INTEGER NOT NULL,
            current_draft_version INTEGER,
            published_version INTEGER,
            disabled INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO tutorial_heads VALUES ('default', 4, 4, NULL, 0);
    """)

    TutorialRepository(connection)

    head = connection.execute(
        "SELECT latest_version, revision FROM tutorial_heads WHERE id = 'default'"
    ).fetchone()
    assert dict(head) == {"latest_version": 4, "revision": 4}


def test_service_contract_uses_revision_for_every_lifecycle_write(monkeypatch, tmp_path) -> None:
    import importlib
    import json

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "service-contract.db"))
    tutorial_services = importlib.import_module("src.services.tutorials")
    repo = repository()
    monkeypatch.setattr(tutorial_services, "repo", repo)
    monkeypatch.setattr(tutorial_services, "check_admin_token", lambda event: True)

    config = tutorial_config()
    create = tutorial_services.admin_create_tutorial({
        "headers": {"X-Admin-Token": "test"},
        "body": json.dumps({"config": config}),
    })
    created = json.loads(create["body"])["tutorial"]
    assert create["statusCode"] == 200
    assert created["version"] == 1
    assert created["revision"] == 1

    publish = tutorial_services.admin_publish_tutorial({
        "headers": {"X-Admin-Token": "test"},
        "body": json.dumps({
            "tutorialId": "default", "version": 1, "expectedRevision": 1,
        }),
    })
    published = json.loads(publish["body"])["tutorial"]
    assert published["revision"] == 2

    stale_disable = tutorial_services.admin_disable_tutorial({
        "headers": {"X-Admin-Token": "test"},
        "body": json.dumps({"tutorialId": "default", "expectedRevision": 1}),
    })
    conflict = json.loads(stale_disable["body"])
    assert stale_disable["statusCode"] == 409
    assert conflict["currentRevision"] == 2
    assert "currentVersion" not in conflict


def test_history_publish_keeps_newer_draft_available() -> None:
    repo = repository()
    first = tutorial_config()
    repo.create_tutorial(first, "admin")
    repo.publish_tutorial("default", version=1, expected_revision=1)

    second = copy.deepcopy(first)
    second["title"] = "Unpublished work"
    repo.update_tutorial("default", second, expected_revision=2, created_by="admin")
    repo.publish_tutorial("default", version=1, expected_revision=3)

    assert repo.get_published_tutorial("default").version == 1
    current = repo.get_tutorial("default")
    assert current is not None
    assert current.version == 2
    assert current.status == "draft"
    assert current.config["title"] == "Unpublished work"


def test_service_contract_exposes_and_checks_aggregate_revision(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "service.db"))
    from src.services import tutorials as service

    service_repo = repository()
    monkeypatch.setattr(service, "repo", service_repo)

    def event(payload: dict, token: str = "admin") -> dict:
        import json
        return {
            "body": json.dumps(payload),
            "headers": {"X-Admin-Token": token},
        }

    import json
    unauthorized = service.admin_create_tutorial(event({"config": tutorial_config()}, "wrong"))
    assert unauthorized["statusCode"] == 401

    created_response = service.admin_create_tutorial(event({"config": tutorial_config()}))
    created = json.loads(created_response["body"])["tutorial"]
    assert created_response["statusCode"] == 200
    assert created["version"] == 1
    assert created["revision"] == 1

    published_response = service.admin_publish_tutorial(event({
        "tutorialId": "default",
        "version": 1,
        "expectedRevision": 1,
    }))
    published = json.loads(published_response["body"])["tutorial"]
    assert published["revision"] == 2

    stale_response = service.admin_disable_tutorial(event({
        "tutorialId": "default",
        "expectedRevision": 1,
    }))
    stale = json.loads(stale_response["body"])
    assert stale_response["statusCode"] == 409
    assert stale["currentRevision"] == 2

    public_response = service.get_tutorial_config({
        "queryStringParameters": {"id": "default"},
    })
    assert public_response["statusCode"] == 200

    disabled_response = service.admin_disable_tutorial(event({
        "tutorialId": "default",
        "expectedRevision": 2,
    }))
    disabled = json.loads(disabled_response["body"])["tutorial"]
    assert disabled["revision"] == 3
    assert service.get_tutorial_config({
        "queryStringParameters": {"id": "default"},
    })["statusCode"] == 404

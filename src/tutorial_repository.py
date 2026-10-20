"""Versioned tutorial model, validation, diffs, and SQLite persistence."""
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TUTORIAL_SCHEMA_VERSION = 1
TUTORIAL_MAX_CONFIG_BYTES = 350 * 1024
TUTORIAL_MAX_ID_BYTES = 128
TUTORIAL_STEP_IDS = (
    "welcome", "roles", "waiting", "sprint", "responses", "engineer",
    "discussion", "stakeholder", "reveal", "controls", "cascade", "wrapup",
)
TUTORIAL_TOKENS = {"scenarioName", "headToHeadRoleA", "headToHeadRoleB"}
REQUIRED_SLOTS = {"welcome": "teaching-arc", "cascade": "teaching-arc", "wrapup": "actions"}
TOKEN_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")


@dataclass(frozen=True)
class TutorialValidationIssue:
    path: str
    message: str
    code: str = "invalid"

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code}


class TutorialValidationError(ValueError):
    def __init__(self, issues: List[TutorialValidationIssue]):
        super().__init__("Tutorial configuration is invalid.")
        self.issues = issues


class TutorialConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("The tutorial was changed by another request.")
        self.current_revision = current_revision


class TutorialAlreadyExistsError(RuntimeError):
    pass


@dataclass
class TutorialVersion:
    id: str
    version: int
    status: str
    config: Dict[str, Any]
    created_at: str
    created_by: Optional[str] = None
    published_at: Optional[str] = None
    revision: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "status": self.status,
            "config": self.config,
            "createdAt": self.created_at,
            "createdBy": self.created_by,
        }
        if self.published_at is not None:
            result["publishedAt"] = self.published_at
        if self.revision is not None:
            result["revision"] = self.revision
        return result


def serialize_tutorial_config(config: Dict[str, Any]) -> str:
    """Serialize exactly as persisted so validation enforces DynamoDB's item budget."""
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def validate_tutorial_config(config: Any, path: str = "config") -> Dict[str, Any]:
    """Validate the complete, closed TutorialConfig v1 schema."""
    issues: List[TutorialValidationIssue] = []

    def issue(field_path: str, message: str, code: str = "invalid") -> None:
        issues.append(TutorialValidationIssue(field_path, message, code))

    def nonempty(value: Any, field_path: str) -> bool:
        if not isinstance(value, str) or not value.strip():
            issue(field_path, "Must be a non-empty string.", "nonempty_string")
            return False
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            issue(field_path, "Must contain valid Unicode.", "invalid_unicode")
            return False
        return True

    def only_fields(value: Dict[str, Any], allowed: set, field_path: str) -> None:
        for key in value:
            if key not in allowed:
                issue(f"{field_path}.{key}", "Field is not supported.", "unknown_field")

    def markdown(value: Any, field_path: str) -> None:
        if not nonempty(value, field_path):
            return
        for token in TOKEN_PATTERN.findall(value):
            token_name = token.strip()
            if token_name not in TUTORIAL_TOKENS:
                issue(field_path, f'Unknown template token "{token_name}".', "unknown_token")
        if re.search(r"<\s*/?\s*(script|style|iframe|object|embed|svg|math|img)\b", value, re.IGNORECASE):
            issue(field_path, "Unsafe or unsupported HTML is not allowed.", "unsafe_html")

    if not _is_object(config):
        raise TutorialValidationError([
            TutorialValidationIssue(path, "Must be an object.", "object_required")
        ])

    only_fields(config, {"schemaVersion", "id", "title", "description", "steps"}, path)
    if config.get("schemaVersion") != TUTORIAL_SCHEMA_VERSION or isinstance(config.get("schemaVersion"), bool):
        issue(f"{path}.schemaVersion", "Must equal 1.", "unsupported_schema_version")
    tutorial_id = config.get("id")
    if nonempty(tutorial_id, f"{path}.id"):
        if tutorial_id != tutorial_id.strip():
            issue(f"{path}.id", "Must not have leading or trailing whitespace.", "invalid_id")
        if "#" in tutorial_id:
            issue(f"{path}.id", "Must not contain '#'.", "invalid_id")
        if len(tutorial_id.encode("utf-8")) > TUTORIAL_MAX_ID_BYTES:
            issue(
                f"{path}.id",
                f"Must be at most {TUTORIAL_MAX_ID_BYTES} UTF-8 bytes.",
                "max_bytes",
            )
    nonempty(config.get("title"), f"{path}.title")
    if "description" in config:
        nonempty(config["description"], f"{path}.description")

    steps = config.get("steps")
    if not isinstance(steps, list):
        issue(f"{path}.steps", "Must be an array of exactly 12 steps.", "array_required")
        steps = []
    elif len(steps) != len(TUTORIAL_STEP_IDS):
        issue(f"{path}.steps", "Must contain exactly 12 steps.", "fixed_length")

    seen_ids = set()
    for index, step in enumerate(steps):
        step_path = f"{path}.steps[{index}]"
        if not _is_object(step):
            issue(step_path, "Must be an object.", "object_required")
            continue
        only_fields(step, {"id", "navigationLabel", "eyebrow", "title", "body", "tip", "differences"}, step_path)
        step_id = step.get("id")
        if nonempty(step_id, f"{step_path}.id"):
            if step_id in seen_ids:
                issue(f"{step_path}.id", "Step IDs must be unique.", "duplicate")
            seen_ids.add(step_id)
            if index < len(TUTORIAL_STEP_IDS) and step_id != TUTORIAL_STEP_IDS[index]:
                issue(f"{step_path}.id", f'Must be "{TUTORIAL_STEP_IDS[index]}" at this position.', "fixed_order")
        nonempty(step.get("navigationLabel"), f"{step_path}.navigationLabel")
        nonempty(step.get("eyebrow"), f"{step_path}.eyebrow")
        nonempty(step.get("title"), f"{step_path}.title")

        body = step.get("body")
        if not isinstance(body, list) or not body:
            issue(f"{step_path}.body", "Must be a non-empty array.", "nonempty_array")
            body = []
        found_slots: List[str] = []
        for block_index, block in enumerate(body):
            block_path = f"{step_path}.body[{block_index}]"
            if not _is_object(block):
                issue(block_path, "Must be an object.", "object_required")
                continue
            if "markdown" in block:
                only_fields(block, {"markdown", "persona", "variant"}, block_path)
                markdown(block.get("markdown"), f"{block_path}.markdown")
                if "persona" in block and block["persona"] not in ("participant", "facilitator"):
                    issue(f"{block_path}.persona", "Must be participant or facilitator.", "enum")
                if "variant" in block and block["variant"] not in ("persona-note", "inline-tip"):
                    issue(f"{block_path}.variant", "Must be persona-note or inline-tip.", "enum")
            elif "slot" in block:
                only_fields(block, {"slot"}, block_path)
                slot = block.get("slot")
                if nonempty(slot, f"{block_path}.slot"):
                    found_slots.append(slot)
                    required = REQUIRED_SLOTS.get(step_id)
                    if required is None or slot != required:
                        issue(f"{block_path}.slot", "This slot is not allowed for the step.", "unsupported_slot")
            else:
                issue(block_path, "Must contain either markdown or slot.", "block_type")

        required_slot = REQUIRED_SLOTS.get(step_id)
        if required_slot is not None and found_slots != [required_slot]:
            issue(f"{step_path}.body", f'Must contain exactly one "{required_slot}" slot.', "required_slot")
        elif required_slot is None and found_slots:
            issue(f"{step_path}.body", "Slots are not allowed for this step.", "unsupported_slot")

        if "tip" in step:
            tip = step["tip"]
            if isinstance(tip, str):
                markdown(tip, f"{step_path}.tip")
            elif _is_object(tip):
                only_fields(tip, {"participant", "facilitator"}, f"{step_path}.tip")
                if set(tip) != {"participant", "facilitator"}:
                    issue(f"{step_path}.tip", "Persona tips require both participant and facilitator.", "complete_personas")
                markdown(tip.get("participant"), f"{step_path}.tip.participant")
                markdown(tip.get("facilitator"), f"{step_path}.tip.facilitator")
            else:
                issue(f"{step_path}.tip", "Must be a string or a complete persona object.", "tip_shape")

        differences = step.get("differences")
        if not isinstance(differences, list) or not differences:
            issue(f"{step_path}.differences", "Must be a non-empty array.", "nonempty_array")
        else:
            for difference_index, difference in enumerate(differences):
                difference_path = f"{step_path}.differences[{difference_index}]"
                if not _is_object(difference):
                    issue(difference_path, "Must be an object.", "object_required")
                    continue
                only_fields(difference, {"omitted", "actualBehavior"}, difference_path)
                nonempty(difference.get("omitted"), f"{difference_path}.omitted")
                nonempty(difference.get("actualBehavior"), f"{difference_path}.actualBehavior")

    if not issues:
        serialized_size = len(serialize_tutorial_config(config).encode("utf-8"))
        if serialized_size > TUTORIAL_MAX_CONFIG_BYTES:
            issue(
                path,
                f"Serialized configuration must be at most {TUTORIAL_MAX_CONFIG_BYTES} UTF-8 bytes.",
                "max_bytes",
            )

    if issues:
        raise TutorialValidationError(issues)
    return config


def recursive_diff(old: Any, new: Any, path: str = "config") -> List[Dict[str, Any]]:
    """Return deterministic path-based changes between JSON-compatible values."""
    changes: List[Dict[str, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child_path = f"{path}.{key}"
            if key not in old:
                changes.append({"path": child_path, "type": "added", "new": new[key]})
            elif key not in new:
                changes.append({"path": child_path, "type": "removed", "old": old[key]})
            else:
                changes.extend(recursive_diff(old[key], new[key], child_path))
    elif isinstance(old, list) and isinstance(new, list):
        for index in range(max(len(old), len(new))):
            child_path = f"{path}[{index}]"
            if index >= len(old):
                changes.append({"path": child_path, "type": "added", "new": new[index]})
            elif index >= len(new):
                changes.append({"path": child_path, "type": "removed", "old": old[index]})
            else:
                changes.extend(recursive_diff(old[index], new[index], child_path))
    elif old != new:
        changes.append({"path": path, "type": "modified", "old": old, "new": new})
    return changes


class TutorialRepository:
    """SQLite implementation with immutable config versions and pointer metadata."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tutorial_versions (
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft', 'published', 'disabled')),
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                published_at TEXT,
                PRIMARY KEY (id, version)
            );
            CREATE TABLE IF NOT EXISTS tutorial_heads (
                id TEXT PRIMARY KEY,
                latest_version INTEGER NOT NULL,
                current_draft_version INTEGER,
                published_version INTEGER,
                disabled INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_tutorial_versions_status
                ON tutorial_versions(id, status, version DESC);
        """)
        head_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(tutorial_heads)").fetchall()
        }
        if "revision" not in head_columns:
            self.conn.execute(
                "ALTER TABLE tutorial_heads ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
            self.conn.execute("UPDATE tutorial_heads SET revision = latest_version")
        self.conn.commit()

    def list_tutorials(self) -> List[TutorialVersion]:
        rows = self.conn.execute("""
            SELECT v.*, h.disabled AS resource_disabled, h.revision AS revision
            FROM tutorial_heads h
            JOIN tutorial_versions v ON v.id = h.id AND v.version = h.latest_version
            ORDER BY json_extract(v.config_json, '$.title') COLLATE NOCASE
        """).fetchall()
        tutorials = [self._row_to_version(row) for row in rows]
        for tutorial, row in zip(tutorials, rows):
            if row["resource_disabled"]:
                tutorial.status = "disabled"
        return tutorials

    def get_tutorial(self, tutorial_id: str, version: Optional[int] = None) -> Optional[TutorialVersion]:
        if version is None:
            row = self.conn.execute("""
                SELECT v.*, h.revision AS revision FROM tutorial_heads h
                JOIN tutorial_versions v ON v.id = h.id
                 AND v.version = COALESCE(h.current_draft_version, h.latest_version)
                WHERE h.id = ?
            """, (tutorial_id,)).fetchone()
        else:
            row = self.conn.execute("""
                SELECT v.*, h.revision AS revision FROM tutorial_versions v
                JOIN tutorial_heads h ON h.id = v.id
                WHERE v.id = ? AND v.version = ?
            """, (tutorial_id, version)).fetchone()
        return self._row_to_version(row) if row else None

    def get_published_tutorial(self, tutorial_id: str) -> Optional[TutorialVersion]:
        row = self.conn.execute("""
            SELECT v.*, h.revision AS revision FROM tutorial_heads h
            JOIN tutorial_versions v ON v.id = h.id AND v.version = h.published_version
            WHERE h.id = ? AND h.disabled = 0 AND v.status = 'published'
        """, (tutorial_id,)).fetchone()
        return self._row_to_version(row) if row else None

    def get_tutorial_history(self, tutorial_id: str) -> List[TutorialVersion]:
        rows = self.conn.execute("""
            SELECT v.*, h.revision AS revision FROM tutorial_versions v
            JOIN tutorial_heads h ON h.id = v.id
            WHERE v.id = ? ORDER BY v.version DESC
        """, (tutorial_id,)).fetchall()
        return [self._row_to_version(row) for row in rows]

    def create_tutorial(self, config: Dict[str, Any], created_by: Optional[str]) -> TutorialVersion:
        tutorial_id = config["id"]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                if self.conn.execute("SELECT 1 FROM tutorial_heads WHERE id = ?", (tutorial_id,)).fetchone():
                    raise TutorialAlreadyExistsError(tutorial_id)
                config_json = serialize_tutorial_config(config)
                self.conn.execute("""
                    INSERT INTO tutorial_versions
                        (id, version, status, config_json, created_at, created_by)
                    VALUES (?, 1, 'draft', ?, ?, ?)
                """, (tutorial_id, config_json, now, created_by))
                self.conn.execute("""
                    INSERT INTO tutorial_heads
                        (id, latest_version, current_draft_version, published_version, disabled, revision)
                    VALUES (?, 1, 1, NULL, 0, 1)
                """, (tutorial_id,))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return TutorialVersion(
            tutorial_id, 1, "draft", config, now, created_by, revision=1
        )

    def update_tutorial(
        self, tutorial_id: str, config: Dict[str, Any], expected_revision: int,
        created_by: Optional[str],
    ) -> Optional[TutorialVersion]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                head = self.conn.execute(
                    "SELECT * FROM tutorial_heads WHERE id = ?", (tutorial_id,)
                ).fetchone()
                if not head:
                    self.conn.rollback()
                    return None
                if head["revision"] != expected_revision:
                    raise TutorialConflictError(head["revision"])
                new_version = int(head["latest_version"]) + 1
                new_revision = int(head["revision"]) + 1
                old_draft = head["current_draft_version"]
                if old_draft is not None:
                    self.conn.execute(
                        "UPDATE tutorial_versions SET status = 'disabled' WHERE id = ? AND version = ? AND status = 'draft'",
                        (tutorial_id, old_draft),
                    )
                config_json = serialize_tutorial_config(config)
                self.conn.execute("""
                    INSERT INTO tutorial_versions
                        (id, version, status, config_json, created_at, created_by)
                    VALUES (?, ?, 'draft', ?, ?, ?)
                """, (tutorial_id, new_version, config_json, now, created_by))
                self.conn.execute("""
                    UPDATE tutorial_heads
                    SET latest_version = ?, current_draft_version = ?, revision = ?
                    WHERE id = ?
                """, (new_version, new_version, new_revision, tutorial_id))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return TutorialVersion(
            tutorial_id, new_version, "draft", config, now, created_by,
            revision=new_revision,
        )

    def publish_tutorial(
        self, tutorial_id: str, version: int, expected_revision: int
    ) -> Optional[TutorialVersion]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                head = self.conn.execute("SELECT * FROM tutorial_heads WHERE id = ?", (tutorial_id,)).fetchone()
                if not head:
                    self.conn.rollback()
                    return None
                if head["revision"] != expected_revision:
                    raise TutorialConflictError(head["revision"])
                new_revision = int(head["revision"]) + 1
                row = self.conn.execute(
                    "SELECT * FROM tutorial_versions WHERE id = ? AND version = ?", (tutorial_id, version)
                ).fetchone()
                if not row:
                    self.conn.rollback()
                    return None
                old_published = head["published_version"]
                if old_published is not None and old_published != version:
                    self.conn.execute(
                        "UPDATE tutorial_versions SET status = 'disabled' WHERE id = ? AND version = ?",
                        (tutorial_id, old_published),
                    )
                self.conn.execute(
                    "UPDATE tutorial_versions SET status = 'published', published_at = ? WHERE id = ? AND version = ?",
                    (now, tutorial_id, version),
                )
                current_draft = None if head["current_draft_version"] == version else head["current_draft_version"]
                self.conn.execute("""
                    UPDATE tutorial_heads
                    SET published_version = ?, current_draft_version = ?, disabled = 0,
                        revision = ?
                    WHERE id = ?
                """, (version, current_draft, new_revision, tutorial_id))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return TutorialVersion(
            tutorial_id, version, "published", json.loads(row["config_json"]),
            row["created_at"], row["created_by"], now, revision=new_revision,
        )

    def disable_tutorial(self, tutorial_id: str, expected_revision: int) -> Optional[TutorialVersion]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                head = self.conn.execute("SELECT * FROM tutorial_heads WHERE id = ?", (tutorial_id,)).fetchone()
                if not head:
                    self.conn.rollback()
                    return None
                if head["revision"] != expected_revision:
                    raise TutorialConflictError(head["revision"])
                new_revision = int(head["revision"]) + 1
                published_version = head["published_version"]
                if published_version is None:
                    self.conn.rollback()
                    return None
                row = self.conn.execute(
                    "SELECT * FROM tutorial_versions WHERE id = ? AND version = ?",
                    (tutorial_id, published_version),
                ).fetchone()
                self.conn.execute(
                    "UPDATE tutorial_versions SET status = 'disabled' WHERE id = ? AND version = ?",
                    (tutorial_id, published_version),
                )
                self.conn.execute("""
                    UPDATE tutorial_heads
                    SET published_version = NULL, disabled = 1, revision = ?
                    WHERE id = ?
                """, (new_revision, tutorial_id))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return TutorialVersion(
            tutorial_id, published_version, "disabled", json.loads(row["config_json"]),
            row["created_at"], row["created_by"], row["published_at"],
            revision=new_revision,
        )

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> TutorialVersion:
        return TutorialVersion(
            id=row["id"], version=int(row["version"]), status=row["status"],
            config=json.loads(row["config_json"]), created_at=row["created_at"],
            created_by=row["created_by"], published_at=row["published_at"],
            revision=int(row["revision"]) if "revision" in row.keys() else None,
        )

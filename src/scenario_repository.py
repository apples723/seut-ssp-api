"""Scenario repository for managing scenario CRUD and versioning."""
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

from .models import Sprint, SprintQuestion, HeadToHead


# Path to scenarios directory (used for initial seeding)
SCENARIOS_DIR = os.environ.get('SCENARIOS_DIR', '/app/scenarios')


@dataclass
class ScenarioVersion:
    """A versioned scenario stored in the database."""
    id: str
    version: int
    title: str
    description: Optional[str] = None
    waiting_room_message: Optional[str] = None
    present_mode_instructions: Optional[str] = None
    settings: Dict[str, Any] = field(default_factory=dict)
    roles: List[Dict[str, Any]] = field(default_factory=list)
    sprints: List[Dict[str, Any]] = field(default_factory=list)
    voter_types: List[Dict[str, Any]] = field(default_factory=list)
    is_latest: bool = True
    is_deleted: bool = False
    enabled: bool = True
    created_at: Optional[str] = None
    created_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'version': self.version,
            'title': self.title,
            'description': self.description,
            'waitingRoomMessage': self.waiting_room_message,
            'presentModeInstructions': self.present_mode_instructions,
            'settings': self.settings,
            'roles': self.roles,
            'sprints': self.sprints,
            'voterTypes': self.voter_types,
            'isLatest': self.is_latest,
            'isDeleted': self.is_deleted,
            'enabled': self.enabled,
            'createdAt': self.created_at,
            'createdBy': self.created_by,
        }

    def to_scenario_json(self) -> Dict[str, Any]:
        """Convert to the original scenario.json format."""
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'waitingRoomMessage': self.waiting_room_message,
            'presentModeInstructions': self.present_mode_instructions,
            'settings': self.settings,
            'roles': self.roles,
            'sprints': self.sprints,
            'voterTypes': self.voter_types,
            'enabled': self.enabled,
        }


def load_scenario_from_file(scenario_id: str) -> Optional[dict]:
    """Load a scenario from its JSON file (used for initial seeding)."""
    scenario_path = os.path.join(SCENARIOS_DIR, scenario_id, 'scenario.json')
    if not os.path.exists(scenario_path):
        fallback_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'scenarios', scenario_id, 'scenario.json')
        if os.path.exists(fallback_path):
            scenario_path = fallback_path
        else:
            return None
    with open(scenario_path, 'r') as f:
        return json.load(f)


def parse_sprints_from_scenario(scenario: dict) -> List[Sprint]:
    """Parse sprint data from scenario JSON into Sprint objects."""
    sprints = []
    for sprint_data in scenario.get('sprints', []):
        # Deserialize sprintQuestion (camelCase from JSON)
        sq_data = sprint_data.get('sprintQuestion')
        sprint_question = None
        if sq_data:
            sprint_question = SprintQuestion(
                prompt=sq_data['prompt'],
                choices=sq_data['choices'],
            )

        # Deserialize headToHead (camelCase from JSON)
        h2h_data = sprint_data.get('headToHead')
        head_to_head = None
        if h2h_data:
            head_to_head = HeadToHead(
                prompt=h2h_data['prompt'],
                role_a=h2h_data.get('roleA', ''),
                role_b=h2h_data.get('roleB', ''),
            )

        sprints.append(Sprint(
            id=sprint_data['id'],
            name=sprint_data['name'],
            description=sprint_data['description'],
            timer_enabled=sprint_data.get('timerEnabled', False),
            timer_seconds=sprint_data.get('timerSeconds'),
            response_boxes=sprint_data.get('responseBoxes'),
            sprint_question=sprint_question,
            head_to_head=head_to_head,
        ))
    return sprints


class ScenarioRepository:
    """Repository for scenario CRUD operations and versioning."""
    
    def __init__(self, conn: sqlite3.Connection):
        """Initialize with a database connection."""
        self.conn = conn
    
    def seed_scenarios_from_files(self) -> None:
        """Seed scenarios from JSON files if the scenarios table is empty."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM scenarios')
        if cursor.fetchone()[0] > 0:
            return  # Already seeded
        
        # Find all scenario directories
        scenarios_dirs = [SCENARIOS_DIR]
        fallback_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'scenarios')
        if os.path.exists(fallback_dir):
            scenarios_dirs.append(fallback_dir)
        
        seeded = set()
        for scenarios_dir in scenarios_dirs:
            if not os.path.exists(scenarios_dir):
                continue
            for scenario_id in os.listdir(scenarios_dir):
                if scenario_id in seeded:
                    continue
                scenario_path = os.path.join(scenarios_dir, scenario_id, 'scenario.json')
                if os.path.exists(scenario_path):
                    try:
                        with open(scenario_path, 'r') as f:
                            data = json.load(f)
                        self._insert_scenario_from_dict(data, created_by='system-seed')
                        seeded.add(scenario_id)
                    except Exception as e:
                        print(f"Warning: Failed to seed scenario {scenario_id}: {e}")
    
    def _insert_scenario_from_dict(self, data: Dict[str, Any], created_by: Optional[str] = None) -> ScenarioVersion:
        """Insert a scenario from a dict (JSON format) as a new version."""
        cursor = self.conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        scenario_id = data.get('id', str(uuid4()))
        
        try:
            cursor.execute('BEGIN TRANSACTION')
            
            # Check if scenario already exists
            cursor.execute('SELECT MAX(version) FROM scenarios WHERE id = ?', (scenario_id,))
            row = cursor.fetchone()
            version = (row[0] or 0) + 1 if row[0] else 1
            
            # Mark previous versions as not latest
            if version > 1:
                cursor.execute('UPDATE scenarios SET is_latest = 0 WHERE id = ?', (scenario_id,))
            
            # Get enabled status - preserve from existing scenario if updating, default to True for new
            enabled = data.get('enabled', True)
            if version > 1:
                cursor.execute('SELECT enabled FROM scenarios WHERE id = ? AND version = ?', (scenario_id, version - 1))
                prev_row = cursor.fetchone()
                if prev_row and prev_row[0] is not None:
                    enabled = bool(prev_row[0])
            
            cursor.execute('''
                INSERT INTO scenarios (id, version, title, description, waiting_room_message, 
                    present_mode_instructions, settings_json, roles_json, sprints_json, voter_types_json,
                    is_latest, is_deleted, enabled, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
            ''', (
                scenario_id,
                version,
                data.get('title', 'Untitled Scenario'),
                data.get('description'),
                data.get('waitingRoomMessage'),
                data.get('presentModeInstructions'),
                json.dumps(data.get('settings', {})),
                json.dumps(data.get('roles', [])),
                json.dumps(data.get('sprints', [])),
                json.dumps(data.get('voterTypes', [])) if data.get('voterTypes') else None,
                1 if enabled else 0,
                now,
                created_by,
            ))
            
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        return ScenarioVersion(
            id=scenario_id,
            version=version,
            title=data.get('title', 'Untitled Scenario'),
            description=data.get('description'),
            waiting_room_message=data.get('waitingRoomMessage'),
            present_mode_instructions=data.get('presentModeInstructions'),
            settings=data.get('settings', {}),
            roles=data.get('roles', []),
            sprints=data.get('sprints', []),
            voter_types=data.get('voterTypes', []),
            is_latest=True,
            is_deleted=False,
            enabled=enabled,
            created_at=now,
            created_by=created_by,
        )
    
    def list_scenarios(self, include_deleted: bool = False) -> List[ScenarioVersion]:
        """List all scenarios (latest versions only by default)."""
        cursor = self.conn.cursor()
        if include_deleted:
            cursor.execute('SELECT * FROM scenarios WHERE is_latest = 1 ORDER BY title')
        else:
            cursor.execute('SELECT * FROM scenarios WHERE is_latest = 1 AND is_deleted = 0 ORDER BY title')
        return [self._row_to_scenario(row) for row in cursor.fetchall()]
    
    def get_scenario(self, scenario_id: str, version: Optional[int] = None) -> Optional[ScenarioVersion]:
        """Get a scenario by ID. If version is None, returns the latest version."""
        cursor = self.conn.cursor()
        if version is not None:
            cursor.execute('SELECT * FROM scenarios WHERE id = ? AND version = ?', (scenario_id, version))
        else:
            cursor.execute('SELECT * FROM scenarios WHERE id = ? AND is_latest = 1', (scenario_id,))
        row = cursor.fetchone()
        return self._row_to_scenario(row) if row else None
    
    def get_scenario_history(self, scenario_id: str) -> List[ScenarioVersion]:
        """Get all versions of a scenario, newest first."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM scenarios WHERE id = ? ORDER BY version DESC', (scenario_id,))
        return [self._row_to_scenario(row) for row in cursor.fetchall()]
    
    def create_scenario(self, data: Dict[str, Any], created_by: Optional[str] = None) -> ScenarioVersion:
        """Create a new scenario from JSON data."""
        return self._insert_scenario_from_dict(data, created_by)
    
    def update_scenario(self, scenario_id: str, data: Dict[str, Any], created_by: Optional[str] = None) -> Optional[ScenarioVersion]:
        """Update a scenario by creating a new version. Returns None if scenario doesn't exist."""
        existing = self.get_scenario(scenario_id)
        if not existing:
            return None
        
        # Merge the ID to ensure consistency
        data['id'] = scenario_id
        return self._insert_scenario_from_dict(data, created_by)
    
    def delete_scenario(self, scenario_id: str) -> bool:
        """Soft-delete a scenario (marks all versions as deleted)."""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE scenarios SET is_deleted = 1 WHERE id = ?', (scenario_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def restore_scenario(self, scenario_id: str) -> bool:
        """Restore a soft-deleted scenario."""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE scenarios SET is_deleted = 0 WHERE id = ?', (scenario_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def enable_scenario(self, scenario_id: str) -> bool:
        """Enable a scenario for use in session creation."""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE scenarios SET enabled = 1 WHERE id = ?', (scenario_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def disable_scenario(self, scenario_id: str) -> bool:
        """Disable a scenario from being used in session creation."""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE scenarios SET enabled = 0 WHERE id = ?', (scenario_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def diff_scenario(self, scenario_id: str, new_data: Dict[str, Any]) -> Dict[str, Any]:
        """Compare new data against the current version and return differences."""
        current = self.get_scenario(scenario_id)
        if not current:
            return {'error': 'Scenario not found', 'isNew': True, 'newData': new_data}
        
        diff = {
            'scenarioId': scenario_id,
            'currentVersion': current.version,
            'changes': [],
        }
        
        # Compare simple fields
        field_map = [
            ('title', 'title', current.title),
            ('description', 'description', current.description),
            ('waitingRoomMessage', 'waitingRoomMessage', current.waiting_room_message),
            ('presentModeInstructions', 'presentModeInstructions', current.present_mode_instructions),
        ]
        
        for json_key, label, current_val in field_map:
            new_val = new_data.get(json_key)
            if new_val != current_val:
                diff['changes'].append({
                    'field': label,
                    'type': 'modified',
                    'old': current_val,
                    'new': new_val,
                })
        
        # Compare settings
        new_settings = new_data.get('settings', {})
        if new_settings != current.settings:
            diff['changes'].append({
                'field': 'settings',
                'type': 'modified',
                'old': current.settings,
                'new': new_settings,
            })
        
        # Compare roles
        diff['roleChanges'] = self._diff_list(
            current.roles, new_data.get('roles', []), 'id', 'role'
        )
        
        # Compare sprints
        diff['sprintChanges'] = self._diff_list(
            current.sprints, new_data.get('sprints', []), 'id', 'sprint'
        )
        
        diff['hasChanges'] = bool(diff['changes'] or diff['roleChanges'] or diff['sprintChanges'])
        return diff
    
    def _diff_list(self, old_list: List[Dict], new_list: List[Dict], id_key: str, item_type: str) -> List[Dict]:
        """Compare two lists of dicts by ID and return changes."""
        changes = []
        old_by_id = {item.get(id_key): item for item in old_list}
        new_by_id = {item.get(id_key): item for item in new_list}
        
        # Find removed items
        for item_id in old_by_id:
            if item_id not in new_by_id:
                changes.append({
                    'type': 'removed',
                    'itemType': item_type,
                    'id': item_id,
                    'item': old_by_id[item_id],
                })
        
        # Find added items
        for item_id in new_by_id:
            if item_id not in old_by_id:
                changes.append({
                    'type': 'added',
                    'itemType': item_type,
                    'id': item_id,
                    'item': new_by_id[item_id],
                })
        
        # Find modified items
        for item_id in old_by_id:
            if item_id in new_by_id and old_by_id[item_id] != new_by_id[item_id]:
                changes.append({
                    'type': 'modified',
                    'itemType': item_type,
                    'id': item_id,
                    'old': old_by_id[item_id],
                    'new': new_by_id[item_id],
                })
        
        return changes
    
    def _row_to_scenario(self, row: sqlite3.Row) -> ScenarioVersion:
        """Convert a database row to a ScenarioVersion object."""
        row_keys = row.keys()
        return ScenarioVersion(
            id=row['id'],
            version=row['version'],
            title=row['title'],
            description=row['description'],
            waiting_room_message=row['waiting_room_message'],
            present_mode_instructions=row['present_mode_instructions'],
            settings=json.loads(row['settings_json']) if row['settings_json'] else {},
            roles=json.loads(row['roles_json']) if row['roles_json'] else [],
            sprints=json.loads(row['sprints_json']) if row['sprints_json'] else [],
            voter_types=json.loads(row['voter_types_json']) if 'voter_types_json' in row_keys and row['voter_types_json'] else [],
            is_latest=bool(row['is_latest']),
            is_deleted=bool(row['is_deleted']),
            enabled=bool(row['enabled']) if row['enabled'] is not None else True,
            created_at=row['created_at'],
            created_by=row['created_by'],
        )

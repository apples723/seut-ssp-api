"""SQLite-based repository for persistent session storage."""
import json
import os
import random
import re
import sqlite3
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

from .models import Session, Participant, ResponseItem, Sprint, ChatMessage, Vote, VoterInfo, VoterType, SprintQuestion, HeadToHead, RolePin
from .scenario_repository import ScenarioRepository, ScenarioVersion, load_scenario_from_file, parse_sprints_from_scenario
from .tutorial_repository import TutorialActivation, TutorialRepository, TutorialVersion
from .voting_repository import VotingRepository


# Database path - mounted as volume in Docker
DB_PATH = os.environ.get('DB_PATH', '/app/data/sessions.db')


def load_scenario(scenario_id: str) -> Optional[dict]:
    """Load a scenario - tries DB first, falls back to file."""
    # Try to load from database first
    try:
        scenario = repo.scenarios.get_scenario(scenario_id)
        if scenario:
            return scenario.to_scenario_json()
    except Exception:
        pass  # DB not initialized yet, fall back to file
    
    # Fall back to file-based loading
    return load_scenario_from_file(scenario_id)


class SqliteRepository:
    def __init__(self) -> None:
        # Ensure data directory exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.execute('PRAGMA foreign_keys = ON')  # Enforce foreign key constraints
        self.conn.execute('PRAGMA busy_timeout = 5000')
        self.conn.row_factory = sqlite3.Row

        # Tutorial lifecycle writes use explicit transactions. Keep them on a
        # dedicated connection so commits in session/scenario/voting code cannot
        # cross those transaction boundaries.
        if DB_PATH == ':memory:':
            tutorial_conn = self.conn
        else:
            tutorial_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            tutorial_conn.execute('PRAGMA foreign_keys = ON')
            tutorial_conn.execute('PRAGMA busy_timeout = 5000')
            tutorial_conn.row_factory = sqlite3.Row
        self._tutorial_conn = tutorial_conn
        
        # Initialize sub-repositories
        self.scenarios = ScenarioRepository(self.conn)
        self.tutorials = TutorialRepository(self._tutorial_conn)
        self.voting = VotingRepository(self.conn)
        
        self._init_schema()
        self._ensure_demo_session()

    # Input validation helpers
    def _validate_participant_name(self, name: str) -> str:
        """Validate and sanitize participant name."""
        if not name or len(name.strip()) == 0:
            raise ValueError("Name cannot be empty")
        if len(name) > 100:
            raise ValueError("Name too long (max 100 characters)")
        # Remove control characters
        sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
        return sanitized.strip()

    def _validate_pin(self, pin: str) -> bool:
        """Validate PIN format (4 digits)."""
        if not pin:
            return True  # PIN is optional
        return bool(re.match(r'^\d{4}$', pin))

    def _validate_text_input(self, text: str, max_length: int = 5000) -> str:
        """Validate and sanitize text inputs (responses, chat messages)."""
        if not text:
            raise ValueError("Text cannot be empty")
        if len(text) > max_length:
            raise ValueError(f"Text too long (max {max_length} characters)")
        return text.strip()

    def _validate_role(self, role: str) -> None:
        """Validate role is one of the allowed values."""
        allowed_roles = ['facilitator', 'player', 'viewer', 'voter']
        if role not in allowed_roles:
            raise ValueError(f"Invalid role: {role}. Must be one of {allowed_roles}")

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                scenario_id TEXT,
                scenario_version INTEGER,
                scenario_name TEXT,
                scenario_description TEXT,
                waiting_room_message TEXT,
                present_mode_instructions TEXT,
                current_sprint_index INTEGER DEFAULT 0,
                total_sprints INTEGER,
                timer_visible INTEGER DEFAULT 1,
                timer_seconds INTEGER,
                timer_running INTEGER DEFAULT 0,
                facilitator_pin TEXT,
                status TEXT DEFAULT 'active',
                started INTEGER DEFAULT 0,
                version INTEGER DEFAULT 0,
                sprints_json TEXT,
                created_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS scenarios (
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                waiting_room_message TEXT,
                present_mode_instructions TEXT,
                settings_json TEXT,
                roles_json TEXT,
                sprints_json TEXT,
                is_latest INTEGER DEFAULT 1,
                is_deleted INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created_at TEXT,
                created_by TEXT,
                PRIMARY KEY (id, version)
            );
            
            CREATE INDEX IF NOT EXISTS idx_scenarios_latest ON scenarios(id, is_latest) WHERE is_latest = 1;
            
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                name TEXT,
                role TEXT,
                assigned_role_name TEXT,
                assigned_role_id TEXT,
                role_type TEXT,
                role_description TEXT,
                private_info TEXT,
                ip_address TEXT,
                joined_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            
            CREATE TABLE IF NOT EXISTS responses (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                sprint_id TEXT,
                participant_name TEXT,
                participant_role_name TEXT,
                text TEXT,
                response_box TEXT,
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                sender_name TEXT,
                sender_role TEXT,
                text TEXT,
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            
            CREATE TABLE IF NOT EXISTS session_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT,
                session_code TEXT,
                data_json TEXT,
                timestamp TEXT
            );
            
            CREATE TABLE IF NOT EXISTS votes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                response_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                participant_role TEXT NOT NULL,
                sprint_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id),
                FOREIGN KEY (response_id) REFERENCES responses(id),
                FOREIGN KEY (participant_id) REFERENCES participants(id),
                UNIQUE(response_id, participant_id)
            );
            
            CREATE TABLE IF NOT EXISTS sprint_question_votes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sprint_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                choice_index INTEGER NOT NULL,
                choice_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(sprint_id, participant_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS head_to_head_votes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sprint_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                voter_type_id TEXT,
                voter_type_name TEXT,
                chosen_role_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(sprint_id, participant_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS scores (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                assigned_role_id TEXT NOT NULL,
                assigned_role_name TEXT NOT NULL,
                sprint_id TEXT NOT NULL,
                points INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS role_pins (
                session_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                role_name TEXT NOT NULL,
                pin TEXT NOT NULL,
                claimed_by_participant_id TEXT,
                claimed_by_name TEXT,
                claimed_at TEXT,
                PRIMARY KEY (session_id, role_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_id);
            CREATE INDEX IF NOT EXISTS idx_responses_session ON responses(session_id);
            CREATE INDEX IF NOT EXISTS idx_responses_sprint ON responses(session_id, sprint_id);
            CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_votes_response ON votes(response_id);
            CREATE INDEX IF NOT EXISTS idx_votes_session_sprint ON votes(session_id, sprint_id);
            CREATE INDEX IF NOT EXISTS idx_votes_participant ON votes(participant_id);
            CREATE INDEX IF NOT EXISTS idx_sq_votes_session_sprint ON sprint_question_votes(session_id, sprint_id);
            CREATE INDEX IF NOT EXISTS idx_sq_votes_participant ON sprint_question_votes(sprint_id, participant_id);
            CREATE INDEX IF NOT EXISTS idx_h2h_votes_session_sprint ON head_to_head_votes(session_id, sprint_id);
            CREATE INDEX IF NOT EXISTS idx_scores_session ON scores(session_id);
            CREATE INDEX IF NOT EXISTS idx_scores_participant ON scores(session_id, participant_id);
            CREATE INDEX IF NOT EXISTS idx_role_pins_session ON role_pins(session_id);
            CREATE INDEX IF NOT EXISTS idx_role_pins_pin ON role_pins(session_id, pin);
            
            -- Additional performance indexes
            CREATE INDEX IF NOT EXISTS idx_session_log_session ON session_log(session_code, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_session_log_timestamp ON session_log(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_scenarios_enabled ON scenarios(enabled, is_deleted, is_latest);
            CREATE INDEX IF NOT EXISTS idx_participants_role ON participants(session_id, role);
            CREATE INDEX IF NOT EXISTS idx_responses_participant ON responses(session_id, participant_name);
        ''')
        self.conn.commit()
        
        # Run migrations for existing databases
        self._run_migrations()
        
        # Seed scenarios from JSON files if table is empty
        self.scenarios.seed_scenarios_from_files()

    def _run_migrations(self) -> None:
        """Run schema migrations for existing databases."""
        cursor = self.conn.cursor()
        
        # Check sessions table columns
        cursor.execute("PRAGMA table_info(sessions)")
        session_columns = [row[1] for row in cursor.fetchall()]
        
        if 'scenario_version' not in session_columns:
            print("Migration: Adding scenario_version column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN scenario_version INTEGER')
            self.conn.commit()
        
        if 'present_mode_instructions' not in session_columns:
            print("Migration: Adding present_mode_instructions column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN present_mode_instructions TEXT')
            self.conn.commit()
        
        # Check if enabled column exists in scenarios table
        cursor.execute("PRAGMA table_info(scenarios)")
        scenario_columns = [row[1] for row in cursor.fetchall()]
        
        if 'enabled' not in scenario_columns:
            print("Migration: Adding enabled column to scenarios table")
            cursor.execute('ALTER TABLE scenarios ADD COLUMN enabled INTEGER DEFAULT 1')
            self.conn.commit()

        if 'voter_types_json' not in scenario_columns:
            print("Migration: Adding voter_types_json column to scenarios table")
            cursor.execute('ALTER TABLE scenarios ADD COLUMN voter_types_json TEXT')
            self.conn.commit()

        # Re-fetch session columns in case they changed above
        cursor.execute("PRAGMA table_info(sessions)")
        session_columns = [row[1] for row in cursor.fetchall()]

        if 'voting_enabled' not in session_columns:
            print("Migration: Adding voting_enabled column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN voting_enabled INTEGER DEFAULT 0')
            self.conn.commit()

        if 'voting_show_voters' not in session_columns:
            print("Migration: Adding voting_show_voters column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN voting_show_voters INTEGER DEFAULT 0')
            self.conn.commit()

        # Check responses table for participant_role_name column
        cursor.execute("PRAGMA table_info(responses)")
        response_columns = {row[1] for row in cursor.fetchall()}
        
        if 'participant_role_name' not in response_columns:
            print("Migration: Adding participant_role_name column to responses table")
            cursor.execute('ALTER TABLE responses ADD COLUMN participant_role_name TEXT')
            self.conn.commit()

        # Re-fetch session columns for voter types migration
        cursor.execute("PRAGMA table_info(sessions)")
        session_columns = [row[1] for row in cursor.fetchall()]

        if 'voter_types_json' not in session_columns:
            print("Migration: Adding voter_types_json column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN voter_types_json TEXT')
            self.conn.commit()

        # Check participants table for voter type columns
        cursor.execute("PRAGMA table_info(participants)")
        participant_columns = {row[1] for row in cursor.fetchall()}

        if 'voter_type_id' not in participant_columns:
            print("Migration: Adding voter_type_id column to participants table")
            cursor.execute('ALTER TABLE participants ADD COLUMN voter_type_id TEXT')
            self.conn.commit()

        if 'voter_type_name' not in participant_columns:
            print("Migration: Adding voter_type_name column to participants table")
            cursor.execute('ALTER TABLE participants ADD COLUMN voter_type_name TEXT')
            self.conn.commit()

        if 'assigned_role_id' not in participant_columns:
            print("Migration: Adding assigned_role_id column to participants table")
            cursor.execute('ALTER TABLE participants ADD COLUMN assigned_role_id TEXT')
            self.conn.commit()

        # Check votes table for voter type columns
        cursor.execute("PRAGMA table_info(votes)")
        vote_columns = {row[1] for row in cursor.fetchall()}

        if 'voter_type_id' not in vote_columns:
            print("Migration: Adding voter_type_id column to votes table")
            cursor.execute('ALTER TABLE votes ADD COLUMN voter_type_id TEXT')
            self.conn.commit()

        if 'voter_type_name' not in vote_columns:
            print("Migration: Adding voter_type_name column to votes table")
            cursor.execute('ALTER TABLE votes ADD COLUMN voter_type_name TEXT')
            self.conn.commit()

        # --- Agile Ethics Scoring migrations ---

        # Re-fetch session columns for scoring migrations
        cursor.execute("PRAGMA table_info(sessions)")
        session_columns = [row[1] for row in cursor.fetchall()]

        if 'sprint_question_phase' not in session_columns:
            print("Migration: Adding sprint_question_phase column to sessions table")
            cursor.execute("ALTER TABLE sessions ADD COLUMN sprint_question_phase TEXT DEFAULT 'inactive'")
            self.conn.commit()

        if 'scoreboard_visible' not in session_columns:
            print("Migration: Adding scoreboard_visible column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN scoreboard_visible INTEGER DEFAULT 0')
            self.conn.commit()

        # Create new scoring tables if they don't exist on older databases
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sprint_question_votes'")
        if not cursor.fetchone():
            print("Migration: Creating sprint_question_votes table")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sprint_question_votes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sprint_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL,
                    choice_index INTEGER NOT NULL,
                    choice_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(sprint_id, participant_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sq_votes_session_sprint ON sprint_question_votes(session_id, sprint_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sq_votes_participant ON sprint_question_votes(sprint_id, participant_id)')
            self.conn.commit()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='head_to_head_votes'")
        if not cursor.fetchone():
            print("Migration: Creating head_to_head_votes table")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS head_to_head_votes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sprint_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL,
                    voter_type_id TEXT,
                    voter_type_name TEXT,
                    chosen_role_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(sprint_id, participant_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_h2h_votes_session_sprint ON head_to_head_votes(session_id, sprint_id)')
            self.conn.commit()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scores'")
        if not cursor.fetchone():
            print("Migration: Creating scores table")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scores (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL,
                    assigned_role_id TEXT NOT NULL,
                    assigned_role_name TEXT NOT NULL,
                    sprint_id TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_scores_session ON scores(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_scores_participant ON scores(session_id, participant_id)')
            self.conn.commit()

        # --- Role PINs migration ---
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='role_pins'")
        if not cursor.fetchone():
            print("Migration: Creating role_pins table")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS role_pins (
                    session_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    pin TEXT NOT NULL,
                    claimed_by_participant_id TEXT,
                    claimed_by_name TEXT,
                    claimed_at TEXT,
                    PRIMARY KEY (session_id, role_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_role_pins_session ON role_pins(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_role_pins_pin ON role_pins(session_id, pin)')
            self.conn.commit()

        # --- Role Pins Toggle migration ---
        cursor.execute("PRAGMA table_info(sessions)")
        session_columns = [row[1] for row in cursor.fetchall()]

        if 'role_pins_enabled' not in session_columns:
            print("Migration: Adding role_pins_enabled column to sessions table")
            cursor.execute('ALTER TABLE sessions ADD COLUMN role_pins_enabled INTEGER DEFAULT 0')
            # Backfill: existing sessions with role_pins rows get enabled
            cursor.execute('''
                UPDATE sessions SET role_pins_enabled = 1
                WHERE session_id IN (SELECT DISTINCT session_id FROM role_pins)
            ''')
            self.conn.commit()

    def _ensure_demo_session(self) -> None:
        """Create demo session if it doesn't exist."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM sessions WHERE session_id = ?', ('DEMO-4829',))
        if cursor.fetchone():
            return
        
        scenario = self.get_scenario('mental-health-app')
        if scenario:
            sprints = [self._dict_to_sprint(s) for s in scenario.sprints]
            scenario_name = scenario.title
            scenario_description = scenario.description
            waiting_room_message = scenario.waiting_room_message
            present_mode_instructions = scenario.present_mode_instructions
        else:
            # Fall back to file if DB scenario not found
            file_scenario = load_scenario_from_file('mental-health-app')
            if file_scenario:
                sprints = parse_sprints_from_scenario(file_scenario)
                scenario_name = file_scenario.get('title', 'Mental Health App Role-Play')
                scenario_description = file_scenario.get('description')
                waiting_room_message = file_scenario.get('waitingRoomMessage')
                present_mode_instructions = file_scenario.get('presentModeInstructions')
            else:
                sprints = [Sprint(id='s1', name='Sprint 1', description='Default sprint', timer_enabled=True, timer_seconds=480)]
                scenario_name = 'Mental Health App Role-Play'
                scenario_description = None
                waiting_room_message = None
                present_mode_instructions = None
        
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute('''
            INSERT INTO sessions (session_id, scenario_id, scenario_version, scenario_name, scenario_description, 
                waiting_room_message, present_mode_instructions, current_sprint_index, total_sprints, timer_visible, 
                timer_seconds, sprints_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            'DEMO-4829', 'mental-health-app', 1,
            scenario_name,
            scenario_description,
            waiting_room_message,
            present_mode_instructions,
            0, len(sprints), 1, sprints[0].timer_seconds or 480,
            json.dumps([self._sprint_to_dict(s) for s in sprints]), now
        ))
        
        facilitator_id = 'p-facilitator'
        cursor.execute('''
            INSERT INTO participants (id, session_id, name, role, assigned_role_name, role_type, role_description, joined_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (facilitator_id, 'DEMO-4829', 'Facilitator Demo', 'facilitator', 'Facilitator', 'Facilitator', 
              'Controls sprint progression and settings.', now))
        
        cursor.execute('''
            INSERT INTO responses (id, session_id, sprint_id, participant_name, text, response_box, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ('r1', 'DEMO-4829', 's1', 'Jordan', 
              'We should prioritize a safe onboarding flow before growth features.', 'Board', now))
        
        self.conn.commit()

    # ==================== Scenario CRUD Methods (Delegated) ====================

    def list_scenarios(self, include_deleted: bool = False) -> List[ScenarioVersion]:
        """List all scenarios (latest versions only by default)."""
        return self.scenarios.list_scenarios(include_deleted)

    def get_scenario(self, scenario_id: str, version: Optional[int] = None) -> Optional[ScenarioVersion]:
        """Get a scenario by ID. If version is None, returns the latest version."""
        return self.scenarios.get_scenario(scenario_id, version)

    def get_scenario_history(self, scenario_id: str) -> List[ScenarioVersion]:
        """Get all versions of a scenario, newest first."""
        return self.scenarios.get_scenario_history(scenario_id)

    def create_scenario(self, data: Dict[str, Any], created_by: Optional[str] = None) -> ScenarioVersion:
        """Create a new scenario from JSON data."""
        return self.scenarios.create_scenario(data, created_by)

    def update_scenario(self, scenario_id: str, data: Dict[str, Any], created_by: Optional[str] = None) -> Optional[ScenarioVersion]:
        """Update a scenario by creating a new version. Returns None if scenario doesn't exist."""
        return self.scenarios.update_scenario(scenario_id, data, created_by)

    def delete_scenario(self, scenario_id: str) -> bool:
        """Soft-delete a scenario (marks all versions as deleted)."""
        return self.scenarios.delete_scenario(scenario_id)

    def restore_scenario(self, scenario_id: str) -> bool:
        """Restore a soft-deleted scenario."""
        return self.scenarios.restore_scenario(scenario_id)

    def enable_scenario(self, scenario_id: str) -> bool:
        """Enable a scenario for use in session creation."""
        return self.scenarios.enable_scenario(scenario_id)

    def disable_scenario(self, scenario_id: str) -> bool:
        """Disable a scenario from being used in session creation."""
        return self.scenarios.disable_scenario(scenario_id)

    def diff_scenario(self, scenario_id: str, new_data: Dict[str, Any]) -> Dict[str, Any]:
        """Compare new data against the current version and return differences."""
        return self.scenarios.diff_scenario(scenario_id, new_data)

    def _sprint_to_dict(self, sprint: Sprint) -> dict:
        d = {
            'id': sprint.id, 'name': sprint.name, 'description': sprint.description,
            'timer_enabled': sprint.timer_enabled, 'timer_seconds': sprint.timer_seconds,
            'response_boxes': sprint.response_boxes
        }
        if sprint.sprint_question is not None:
            d['sprint_question'] = {
                'prompt': sprint.sprint_question.prompt,
                'choices': sprint.sprint_question.choices,
            }
        if sprint.head_to_head is not None:
            d['head_to_head'] = {
                'prompt': sprint.head_to_head.prompt,
                'role_a': sprint.head_to_head.role_a,
                'role_b': sprint.head_to_head.role_b,
            }
        return d

    def _dict_to_sprint(self, d: dict) -> Sprint:
        # Deserialize sprint_question (snake_case from DB or camelCase from scenario JSON)
        sq_data = d.get('sprint_question') or d.get('sprintQuestion')
        sprint_question = None
        if sq_data:
            sprint_question = SprintQuestion(
                prompt=sq_data['prompt'],
                choices=sq_data['choices'],
            )

        # Deserialize head_to_head (snake_case from DB or camelCase from scenario JSON)
        h2h_data = d.get('head_to_head') or d.get('headToHead')
        head_to_head = None
        if h2h_data:
            head_to_head = HeadToHead(
                prompt=h2h_data['prompt'],
                role_a=h2h_data.get('role_a') or h2h_data.get('roleA', ''),
                role_b=h2h_data.get('role_b') or h2h_data.get('roleB', ''),
            )

        return Sprint(
            id=d['id'], name=d['name'], description=d['description'],
            timer_enabled=d.get('timer_enabled', False), timer_seconds=d.get('timer_seconds'),
            response_boxes=d.get('response_boxes'),
            sprint_question=sprint_question,
            head_to_head=head_to_head,
        )

    def _bump_version(self, session_id: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute('UPDATE sessions SET version = version + 1 WHERE session_id = ?', (session_id,))
        self.conn.commit()

    def _generate_session_code(self) -> str:
        cursor = self.conn.cursor()
        while True:
            letters = ''.join(random.choices(string.ascii_uppercase, k=4))
            digits = ''.join(random.choices(string.digits, k=4))
            code = f'{letters}-{digits}'
            cursor.execute('SELECT 1 FROM sessions WHERE session_id = ?', (code,))
            if not cursor.fetchone():
                return code

    def _log_event(self, event: str, session_code: str, data: dict = None) -> None:
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO session_log (event, session_code, data_json, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (event, session_code, json.dumps(data) if data else None, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def _build_session(self, row: sqlite3.Row, participant_id: Optional[str] = None) -> Session:
        """Build a Session object from a database row."""
        cursor = self.conn.cursor()
        session_id = row['session_id']
        
        # Load sprints from JSON
        sprints_json = row['sprints_json']
        sprints = [self._dict_to_sprint(d) for d in json.loads(sprints_json)] if sprints_json else []
        
        # Get current sprint
        current_index = row['current_sprint_index']
        current_sprint = sprints[current_index] if current_index < len(sprints) else Sprint(
            id='s1', name='Sprint 1', description='Default sprint', timer_enabled=True, timer_seconds=480
        )
        
        # Load participants
        cursor.execute('SELECT * FROM participants WHERE session_id = ?', (session_id,))
        participants = [Participant(
            id=p['id'], name=p['name'], role=p['role'],
            assigned_role_name=p['assigned_role_name'],
            assigned_role_id=p['assigned_role_id'] if 'assigned_role_id' in p.keys() else None,
            role_type=p['role_type'],
            role_description=p['role_description'], private_info=p['private_info'],
            ip_address=p['ip_address'], joined_at=p['joined_at'],
            voter_type_id=p['voter_type_id'] if 'voter_type_id' in p.keys() else None,
            voter_type_name=p['voter_type_name'] if 'voter_type_name' in p.keys() else None,
        ) for p in cursor.fetchall()]
        
        # Find the requesting participant or default to first facilitator
        participant = None
        if participant_id:
            participant = next((p for p in participants if p.id == participant_id), None)
        if not participant:
            participant = next((p for p in participants if p.role == 'facilitator'), None)
        if not participant and participants:
            participant = participants[0]
        if not participant:
            participant = Participant(id='unknown', name='Unknown', role='viewer')
        
        # Load responses for current sprint
        cursor.execute('SELECT * FROM responses WHERE session_id = ? AND sprint_id = ? ORDER BY created_at DESC',
                      (session_id, current_sprint.id))
        raw_responses = cursor.fetchall()
        responses = [ResponseItem(
            id=r['id'], participant_name=r['participant_name'], text=r['text'],
            sprint_id=r['sprint_id'], created_at=r['created_at'], response_box=r['response_box'],
            participant_role_name=r['participant_role_name'] if 'participant_role_name' in r.keys() else None
        ) for r in raw_responses]

        # Read voting settings (columns may not exist on older DBs — default to 0)
        row_keys = row.keys()
        voting_enabled = bool(row['voting_enabled']) if 'voting_enabled' in row_keys else False
        voting_show_voters = bool(row['voting_show_voters']) if 'voting_show_voters' in row_keys else False

        # Read voter types
        voter_types_raw = row['voter_types_json'] if 'voter_types_json' in row_keys else None
        voter_types = []
        if voter_types_raw:
            voter_types = [
                VoterType(id=vt['id'], name=vt['name'], description=vt.get('description'))
                for vt in json.loads(voter_types_raw)
            ]

        # Read sprint question phase and scoreboard visibility
        sprint_question_phase = row['sprint_question_phase'] if 'sprint_question_phase' in row_keys else 'inactive'
        scoreboard_visible = bool(row['scoreboard_visible']) if 'scoreboard_visible' in row_keys else False

        # Read role pins enabled setting
        role_pins_enabled = bool(row['role_pins_enabled']) if 'role_pins_enabled' in row_keys else False

        # Look up current participant's engineer vote for the current sprint
        engineer_vote_choice = None
        if participant_id and current_sprint:
            vote_row = cursor.execute(
                'SELECT choice_index FROM sprint_question_votes WHERE session_id = ? AND sprint_id = ? AND participant_id = ?',
                (session_id, current_sprint.id, participant_id)
            ).fetchone()
            if vote_row:
                engineer_vote_choice = vote_row['choice_index']

        # Attach vote data to responses
        if responses:
            response_ids = [r.id for r in responses]
            # Always include voters array for vote type breakdown, but conditionally include names
            # Facilitators always see voter identities; others only when show_voters is on
            show_voter_names = (participant.role == 'facilitator') or voting_show_voters
            vote_data = self.get_votes_for_responses(
                response_ids,
                participant_id=participant_id,
                include_voters=True,  # Always include for vote type breakdown
            )
            for r in responses:
                vd = vote_data.get(r.id, {})
                r.vote_count = vd.get('vote_count', 0)
                r.current_user_voted = vd.get('current_user_voted', False)
                # Always include voters array, but anonymize names if show_voter_names is False
                voters = vd.get('voters')
                if voters and not show_voter_names:
                    # Anonymize voter names by setting to empty string
                    r.voters = [
                        VoterInfo(
                            participant_id=v.participant_id,
                            participant_name='',  # Anonymize name
                            participant_role=v.participant_role,
                            created_at=v.created_at,
                            vote_id=v.vote_id,
                            voter_type_id=v.voter_type_id,
                            voter_type_name=v.voter_type_name,
                        )
                        for v in voters
                    ]
                else:
                    r.voters = voters

        # Load chat messages
        cursor.execute('SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC', (session_id,))
        chat_messages = [ChatMessage(
            id=m['id'], sender_name=m['sender_name'], sender_role=m['sender_role'],
            text=m['text'], created_at=m['created_at']
        ) for m in cursor.fetchall()]
        
        # Load scores for this session
        scores = self.get_scores(session_id)

        return Session(
            session_id=session_id,
            scenario_name=row['scenario_name'],
            scenario_description=row['scenario_description'],
            waiting_room_message=row['waiting_room_message'],
            present_mode_instructions=row['present_mode_instructions'] if 'present_mode_instructions' in row_keys else None,
            current_sprint_index=current_index,
            total_sprints=row['total_sprints'],
            timer_visible=bool(row['timer_visible']),
            timer_seconds=row['timer_seconds'],
            timer_running=bool(row['timer_running']),
            facilitator_pin=row['facilitator_pin'],
            status=row['status'],
            started=bool(row['started']),
            version=row['version'],
            created_at=row['created_at'],
            voting_enabled=voting_enabled,
            voting_show_voters=voting_show_voters,
            voter_types=voter_types,
            sprint_question_phase=sprint_question_phase,
            scoreboard_visible=scoreboard_visible,
            engineer_vote_choice=engineer_vote_choice,
            scores=scores,
            role_pins_enabled=role_pins_enabled,
            participant=participant,
            current_sprint=current_sprint,
            sprints=sprints,
            responses=responses,
            participants=participants,
            chat_messages=chat_messages,
        )

    def get_session(self) -> Session:
        """Get the default demo session (backwards compatibility)."""
        return self.get_session_by_code('DEMO-4829')

    def get_session_by_code(self, session_code: str, participant_id: Optional[str] = None) -> Optional[Session]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._build_session(row, participant_id)

    def create_session(self, facilitator_name: str, scenario_id: str, facilitator_pin: Optional[str] = None,
                       ip_address: Optional[str] = None, role_pins_enabled: bool = False) -> Session:
        """Create a new session with a generated code."""
        # Validate inputs
        facilitator_name = self._validate_participant_name(facilitator_name)
        if facilitator_pin and not self._validate_pin(facilitator_pin):
            raise ValueError("PIN must be exactly 4 digits")
        
        session_code = self._generate_session_code()
        now = datetime.now(timezone.utc).isoformat()
        
        # Try to load scenario from DB first
        scenario_version = self.get_scenario(scenario_id)
        if scenario_version:
            sprints = [self._dict_to_sprint(s) for s in scenario_version.sprints]
            scenario_name = scenario_version.title
            scenario_description = scenario_version.description
            waiting_room_message = scenario_version.waiting_room_message
            present_mode_instructions = scenario_version.present_mode_instructions
            timer_visible = scenario_version.settings.get('timerEnabled', True)
            version_num = scenario_version.version
            voter_types = scenario_version.voter_types  # List[dict]
        else:
            # Fall back to file-based loading
            scenario = load_scenario_from_file(scenario_id)
            if scenario:
                sprints = parse_sprints_from_scenario(scenario)
                scenario_name = scenario.get('title', 'Unknown Scenario')
                scenario_description = scenario.get('description')
                waiting_room_message = scenario.get('waitingRoomMessage')
                present_mode_instructions = scenario.get('presentModeInstructions')
                settings = scenario.get('settings', {})
                timer_visible = settings.get('timerEnabled', True)
                voter_types = scenario.get('voterTypes', [])
            else:
                sprints = [Sprint(id='s1', name='Sprint 1', description='Default sprint', timer_enabled=True, timer_seconds=480)]
                scenario_name = 'Unknown Scenario'
                scenario_description = None
                waiting_room_message = None
                present_mode_instructions = None
                timer_visible = True
                voter_types = []
            version_num = None

        voter_types_json = json.dumps(voter_types) if voter_types else None
        voting_enabled = bool(voter_types)

        first_sprint = sprints[0] if sprints else Sprint(id='s1', name='Sprint 1', description='Default', timer_enabled=True, timer_seconds=480)
        
        cursor = self.conn.cursor()
        try:
            cursor.execute('BEGIN TRANSACTION')
            cursor.execute('''
                INSERT INTO sessions (session_id, scenario_id, scenario_version, scenario_name, scenario_description,
                    waiting_room_message, present_mode_instructions, current_sprint_index, total_sprints, timer_visible,
                    timer_seconds, facilitator_pin, sprints_json, voter_types_json, voting_enabled, role_pins_enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (session_code, scenario_id, version_num, scenario_name, scenario_description, waiting_room_message,
                  present_mode_instructions, 0, len(sprints), 1 if timer_visible else 0, first_sprint.timer_seconds or 480,
                  facilitator_pin, json.dumps([self._sprint_to_dict(s) for s in sprints]),
                  voter_types_json, 1 if voting_enabled else 0, 1 if role_pins_enabled else 0, now))
            
            facilitator_id = str(uuid4())
            cursor.execute('''
                INSERT INTO participants (id, session_id, name, role, assigned_role_name, role_type, role_description, ip_address, joined_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (facilitator_id, session_code, facilitator_name, 'facilitator', 'Facilitator', 'Facilitator',
                  'Controls sprint progression and settings.', ip_address, now))
            
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        self._log_event('session_created', session_code, {'facilitator_name': facilitator_name, 'scenario_id': scenario_id})
        
        # Generate role PINs only when role_pins_enabled is True
        if role_pins_enabled:
            pin_count = self.generate_role_pins(session_code)
            if pin_count > 0:
                self._log_event('role_pins_generated', session_code, {'pin_count': pin_count})
        
        return self.get_session_by_code(session_code, facilitator_id)

    def list_sessions(self) -> list:
        """Return a summary list of all sessions for admin view."""
        cursor = self.conn.cursor()
        # Use JOIN to avoid N+1 query problem (was 201 queries for 100 sessions, now 1 query)
        cursor.execute('''
            SELECT 
                s.session_id,
                s.scenario_name,
                s.status,
                s.current_sprint_index,
                s.total_sprints,
                s.created_at,
                COUNT(DISTINCT p.id) as participant_count,
                COUNT(DISTINCT r.id) as response_count
            FROM sessions s
            LEFT JOIN participants p ON s.session_id = p.session_id
            LEFT JOIN responses r ON s.session_id = r.session_id
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
        ''')
        result = []
        for row in cursor.fetchall():
            result.append({
                'sessionId': row['session_id'],
                'scenarioName': row['scenario_name'],
                'status': row['status'],
                'participantCount': row['participant_count'],
                'responseCount': row['response_count'],
                'currentSprintIndex': row['current_sprint_index'],
                'totalSprints': row['total_sprints'],
                'createdAt': row['created_at'],
            })
        return result

    def has_facilitator(self, session_code: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM participants WHERE session_id = ? AND role = ?', (session_code, 'facilitator'))
        return cursor.fetchone() is not None

    def check_facilitator_pin(self, session_code: str, pin: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('SELECT facilitator_pin FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row or not row['facilitator_pin']:
            return False
        return row['facilitator_pin'] == pin

    def replace_facilitator(self, session_code: str, name: str, ip_address: Optional[str] = None) -> Optional[Participant]:
        # Validate input
        name = self._validate_participant_name(name)
        
        cursor = self.conn.cursor()
        try:
            cursor.execute('BEGIN TRANSACTION')
            cursor.execute('DELETE FROM participants WHERE session_id = ? AND role = ?', (session_code, 'facilitator'))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        return self.add_participant(session_code, name, 'facilitator', ip_address)

    def add_participant(self, session_code: str, name: str, role: str, ip_address: Optional[str] = None,
                        voter_type_id: Optional[str] = None, voter_type_name: Optional[str] = None,
                        assigned_role_name: Optional[str] = None, assigned_role_id: Optional[str] = None,
                        role_type: Optional[str] = None,
                        role_description: Optional[str] = None, private_info: Optional[str] = None) -> Participant:
        """Add a participant to a session and return the participant object.
        
        For players joining with a role PIN, the role assignment fields can be provided directly.
        """
        # Validate inputs
        name = self._validate_participant_name(name)
        self._validate_role(role)
        
        now = datetime.now(timezone.utc).isoformat()
        participant_id = str(uuid4())
        
        # Use provided role assignment fields if given, otherwise use defaults
        if assigned_role_name is None:
            # Players leave role fields blank so they can pick a scenario role later.
            # Facilitators and viewers get their system-level role info immediately.
            if role == 'player':
                assigned_role_name = None
                assigned_role_id = None
                role_type = 'Player'
                role_description = None
                private_info = None
            else:
                role_names = {'facilitator': 'Facilitator', 'viewer': 'Viewer'}
                role_descriptions = {
                    'facilitator': 'Controls sprint progression and settings.',
                    'viewer': 'Observes the session without submitting responses.',
                }
                assigned_role_name = role_names.get(role, role)
                role_type = role.capitalize()
                role_description = role_descriptions.get(role, '')
                private_info = None
        
        cursor = self.conn.cursor()
        try:
            cursor.execute('BEGIN TRANSACTION')
            cursor.execute('''
                INSERT INTO participants (id, session_id, name, role, assigned_role_name, assigned_role_id, role_type, role_description, private_info, ip_address, joined_at, voter_type_id, voter_type_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (participant_id, session_code, name, role, assigned_role_name, assigned_role_id, role_type,
                  role_description, private_info, ip_address, now, voter_type_id, voter_type_name))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        self._bump_version(session_code)
        self._log_event('participant_joined', session_code, {'participant_name': name, 'role': role, 'ip_address': ip_address})
        
        return Participant(
            id=participant_id, name=name, role=role,
            assigned_role_name=assigned_role_name, assigned_role_id=assigned_role_id,
            role_type=role_type,
            role_description=role_description, private_info=private_info,
            ip_address=ip_address, joined_at=now,
            voter_type_id=voter_type_id, voter_type_name=voter_type_name,
        )

    def get_session_scenario_info(self, session_code: str) -> Optional[dict]:
        """Return the scenario_id and scenario_version for a session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT scenario_id, scenario_version FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return None
        return {'scenario_id': row['scenario_id'], 'scenario_version': row['scenario_version']}

    def get_voter_types_for_session(self, session_code: str) -> List[VoterType]:
        """Return the voter types configured for a session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT voter_types_json FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row or not row['voter_types_json']:
            return []
        data = json.loads(row['voter_types_json'])
        return [VoterType(id=vt['id'], name=vt['name'], description=vt.get('description')) for vt in data]

    def is_role_pins_enabled(self, session_code: str) -> bool:
        """Check whether role PIN-based assignment is enabled for a session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT role_pins_enabled FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return False
        return bool(row['role_pins_enabled'])

    def get_session_participants(self, session_code: str) -> List[Participant]:
        """Return all participants for a session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM participants WHERE session_id = ?', (session_code,))
        return [Participant(
            id=p['id'], name=p['name'], role=p['role'],
            assigned_role_name=p['assigned_role_name'],
            assigned_role_id=p['assigned_role_id'] if 'assigned_role_id' in p.keys() else None,
            role_type=p['role_type'],
            role_description=p['role_description'], private_info=p['private_info'],
            ip_address=p['ip_address'], joined_at=p['joined_at'],
            voter_type_id=p['voter_type_id'] if 'voter_type_id' in p.keys() else None,
            voter_type_name=p['voter_type_name'] if 'voter_type_name' in p.keys() else None,
        ) for p in cursor.fetchall()]

    def update_participant_role(self, participant_id: str, assigned_role_name: str,
                                role_description: str, private_info: Optional[str],
                                session_code: str, assigned_role_id: Optional[str] = None) -> bool:
        """Update a participant's scenario role assignment."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE participants SET assigned_role_name = ?, assigned_role_id = ?, role_description = ?, private_info = ?
            WHERE id = ? AND session_id = ?
        ''', (assigned_role_name, assigned_role_id, role_description, private_info, participant_id, session_code))
        self.conn.commit()
        if cursor.rowcount > 0:
            self._bump_version(session_code)
            return True
        return False


    def remove_participant(self, participant_id: str, session_code: str) -> bool:
        """Remove a participant from a session."""
        cursor = self.conn.cursor()
        cursor.execute(
            'DELETE FROM participants WHERE id = ? AND session_id = ?',
            (participant_id, session_code),
        )
        self.conn.commit()
        if cursor.rowcount > 0:
            self._bump_version(session_code)
            self._log_event('participant_removed', session_code, {'participant_id': participant_id})
            return True
        return False


    def end_session(self, session_code: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute('UPDATE sessions SET status = ? WHERE session_id = ?', ('ended', session_code))
        self.conn.commit()
        if cursor.rowcount > 0:
            self._bump_version(session_code)
            self._log_event('session_ended', session_code)
            return True
        return False

    def start_session(self, session_code: str) -> bool:
        cursor = self.conn.cursor()
        
        # Ensure role PINs exist (idempotent safety net — PINs are normally generated at session creation)
        # Only generate PINs when role_pins_enabled=True for the session
        if self.is_role_pins_enabled(session_code):
            self.generate_role_pins(session_code)
        
        cursor.execute('UPDATE sessions SET started = 1 WHERE session_id = ?', (session_code,))
        self.conn.commit()
        if cursor.rowcount > 0:
            self._bump_version(session_code)
            self._log_event('session_started', session_code)
            return True
        return False

    def add_chat_message(self, session_code: str, sender_name: str, sender_role: str, text: str) -> Optional[ChatMessage]:
        # Validate inputs
        sender_name = self._validate_participant_name(sender_name)
        text = self._validate_text_input(text, max_length=5000)
        
        now = datetime.now(timezone.utc).isoformat()
        message_id = str(uuid4())
        
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO chat_messages (id, session_id, sender_name, sender_role, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (message_id, session_code, sender_name, sender_role, text, now))
        self.conn.commit()
        
        self._bump_version(session_code)
        
        return ChatMessage(id=message_id, sender_name=sender_name, sender_role=sender_role, text=text, created_at=now)

    def get_session_log(self) -> list:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM session_log ORDER BY timestamp DESC LIMIT 1000')
        return [{'event': r['event'], 'session_code': r['session_code'], 
                 'data': json.loads(r['data_json']) if r['data_json'] else None,
                 'timestamp': r['timestamp']} for r in cursor.fetchall()]

    def get_participant_role_name(self, session_code: str, participant_id: str) -> Optional[str]:
        """Get the assigned role name for a participant."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT assigned_role_name FROM participants WHERE session_id = ? AND id = ?',
                      (session_code, participant_id))
        row = cursor.fetchone()
        return row['assigned_role_name'] if row else None

    def add_response(self, participant_name: str, text: str, response_box: Optional[str] = None,
                     participant_role_name: Optional[str] = None) -> ResponseItem:
        """Add a response to the default demo session (backwards compatibility)."""
        return self.add_response_to_session('DEMO-4829', participant_name, text, response_box, participant_role_name)

    def add_response_to_session(self, session_code: str, participant_name: str, text: str, 
                                 response_box: Optional[str] = None,
                                 participant_role_name: Optional[str] = None) -> Optional[ResponseItem]:
        """Add a response to a specific session."""
        # Validate inputs
        participant_name = self._validate_participant_name(participant_name)
        text = self._validate_text_input(text, max_length=5000)
        
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_sprint_index, sprints_json FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return None
        
        sprints = json.loads(row['sprints_json']) if row['sprints_json'] else []
        current_index = row['current_sprint_index']
        sprint_id = sprints[current_index]['id'] if current_index < len(sprints) else 's1'
        
        now = datetime.now(timezone.utc).isoformat()
        response_id = str(uuid4())
        
        cursor.execute('''
            INSERT INTO responses (id, session_id, sprint_id, participant_name, participant_role_name, text, response_box, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (response_id, session_code, sprint_id, participant_name, participant_role_name, text, response_box, now))
        self.conn.commit()
        
        self._bump_version(session_code)
        
        return ResponseItem(id=response_id, participant_name=participant_name, text=text,
                           sprint_id=sprint_id, created_at=now, response_box=response_box,
                           participant_role_name=participant_role_name)

    def delete_response(self, session_code: str, response_id: str) -> bool:
        """Delete a response and all associated votes. Returns True if deleted, False if not found."""
        cursor = self.conn.cursor()
        
        # First check if the response exists and belongs to this session
        cursor.execute('SELECT id FROM responses WHERE id = ? AND session_id = ?', (response_id, session_code))
        if not cursor.fetchone():
            return False
        
        # Delete all votes associated with this response
        cursor.execute('DELETE FROM votes WHERE response_id = ?', (response_id,))
        
        # Delete the response
        cursor.execute('DELETE FROM responses WHERE id = ? AND session_id = ?', (response_id, session_code))
        self.conn.commit()
        
        self._bump_version(session_code)
        return True

    def next_sprint(self) -> Session:
        """Advance to the next sprint in the default demo session (backwards compatibility)."""
        return self.next_sprint_for_session('DEMO-4829')

    def next_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Advance to the next sprint for a specific session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_sprint_index, total_sprints, sprints_json FROM sessions WHERE session_id = ?', 
                      (session_code,))
        row = cursor.fetchone()
        if not row:
            return None
        
        sprints = json.loads(row['sprints_json']) if row['sprints_json'] else []
        next_index = min(row['current_sprint_index'] + 1, row['total_sprints'] - 1)
        
        # Get timer seconds from next sprint
        timer_seconds = 420
        if next_index < len(sprints):
            timer_seconds = sprints[next_index].get('timer_seconds') or 420
        
        cursor.execute('''
            UPDATE sessions SET current_sprint_index = ?, timer_seconds = ?, timer_running = 0
            WHERE session_id = ?
        ''', (next_index, timer_seconds, session_code))
        self.conn.commit()
        
        self._bump_version(session_code)
        self._log_event('sprint_advanced', session_code, {'new_index': next_index})
        
        return self.get_session_by_code(session_code)

    def prev_sprint_for_session(self, session_code: str) -> Optional[Session]:
        """Go back to the previous sprint for a specific session."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT current_sprint_index, sprints_json FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return None
        
        sprints = json.loads(row['sprints_json']) if row['sprints_json'] else []
        prev_index = max(row['current_sprint_index'] - 1, 0)
        
        timer_seconds = 420
        if prev_index < len(sprints):
            timer_seconds = sprints[prev_index].get('timer_seconds') or 420
        
        cursor.execute('''
            UPDATE sessions SET current_sprint_index = ?, timer_seconds = ?, timer_running = 0
            WHERE session_id = ?
        ''', (prev_index, timer_seconds, session_code))
        self.conn.commit()
        
        self._bump_version(session_code)
        self._log_event('sprint_back', session_code, {'new_index': prev_index})
        
        return self.get_session_by_code(session_code)

    def update_timer(self, session_code: str, action: str, seconds: Optional[int] = None) -> Optional[Session]:
        """Update timer state for a session."""
        cursor = self.conn.cursor()
        
        if action == 'start':
            cursor.execute('UPDATE sessions SET timer_running = 1, timer_visible = 1 WHERE session_id = ?', (session_code,))
        elif action == 'stop':
            cursor.execute('UPDATE sessions SET timer_running = 0 WHERE session_id = ?', (session_code,))
        elif action == 'set' and seconds is not None and seconds >= 0:
            cursor.execute('UPDATE sessions SET timer_seconds = ?, timer_running = 0 WHERE session_id = ?', (seconds, session_code))
        elif action == 'hide':
            cursor.execute('UPDATE sessions SET timer_visible = 0, timer_running = 0 WHERE session_id = ?', (session_code,))
        elif action == 'show':
            cursor.execute('UPDATE sessions SET timer_visible = 1 WHERE session_id = ?', (session_code,))
        
        self.conn.commit()
        self._bump_version(session_code)
        
        return self.get_session_by_code(session_code)

    # ==================== Voting Methods (Delegated) ====================

    def add_vote(
        self,
        session_code: str,
        response_id: str,
        participant_id: str,
        participant_name: str,
        participant_role: str,
        voter_type_id: Optional[str] = None,
        voter_type_name: Optional[str] = None,
    ) -> bool:
        """Add a vote to a response. Returns True if added, False if already exists (idempotent)."""
        result = self.voting.add_vote(session_code, response_id, participant_id, participant_name, participant_role,
                                      voter_type_id=voter_type_id, voter_type_name=voter_type_name)
        if result:
            self._bump_version(session_code)
        return result

    def remove_vote(self, session_code: str, response_id: str, participant_id: str) -> bool:
        """Remove a participant's vote from a response. Returns True if deleted, False if not found."""
        result = self.voting.remove_vote(session_code, response_id, participant_id)
        if result:
            self._bump_version(session_code)
        return result

    def remove_vote_by_id(self, vote_id: str) -> Optional[Dict[str, Any]]:
        """Remove a specific vote by ID. Returns vote details for logging, or None if not found."""
        vote_details = self.voting.remove_vote_by_id(vote_id)
        if vote_details:
            self._bump_version(vote_details['session_id'])
        return vote_details

    def get_votes_for_responses(
        self,
        response_ids: List[str],
        participant_id: Optional[str] = None,
        include_voters: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Get vote data for multiple responses."""
        return self.voting.get_votes_for_responses(response_ids, participant_id, include_voters)

    def set_voting_enabled(self, session_code: str, enabled: bool) -> bool:
        """Enable or disable voting for a session. Returns True on success."""
        result = self.voting.set_voting_enabled(session_code, enabled)
        if result:
            self._bump_version(session_code)
        return result

    def toggle_voting_show_voters(self, session_code: str) -> bool:
        """Toggle voter visibility setting. Returns new boolean state."""
        result = self.voting.toggle_voting_show_voters(session_code)
        self._bump_version(session_code)
        return result

    def get_vote_summary_by_role(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Get vote summary grouped by participant_role for a session/sprint."""
        return self.voting.get_vote_summary_by_role(session_code, sprint_id)

    def get_vote_summary_by_voter_type(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Get vote summary grouped by voter_type_id/voter_type_name for a session/sprint."""
        return self.voting.get_vote_summary_by_voter_type(session_code, sprint_id)

    # ==================== Sprint Question / Scoring Methods ====================

    def submit_engineer_vote(self, session_code: str, sprint_id: str, participant_id: str,
                             participant_name: str, choice_index: int, choice_text: str,
                             event_name: str = 'engineer_vote_submitted') -> None:
        """Upsert an engineer (player) sprint question vote. One vote per (sprint_id, participant_id)."""
        cursor = self.conn.cursor()
        # Look up session_id (which is session_code in this schema)
        cursor.execute('SELECT session_id FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return
        session_id = row['session_id']
        now = datetime.now(timezone.utc).isoformat()
        vote_id = str(uuid4())
        cursor.execute('''
            INSERT OR REPLACE INTO sprint_question_votes
                (id, session_id, sprint_id, participant_id, participant_name, choice_index, choice_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (vote_id, session_id, sprint_id, participant_id, participant_name, choice_index, choice_text, now))
        self.conn.commit()
        self._bump_version(session_code)
        self._log_event(event_name, session_code, {
            'participant_id': participant_id,
            'participant_name': participant_name,
            'sprint_id': sprint_id,
            'choice_index': choice_index,
            'choice_text': choice_text,
        })

    def get_engineer_vote(self, session_code: str, sprint_id: str, participant_id: str) -> Optional[Dict[str, Any]]:
        """Return a single participant's sprint question vote, or None."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM sprint_question_votes
            WHERE session_id = ? AND sprint_id = ? AND participant_id = ?
        ''', (session_code, sprint_id, participant_id))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'id': row['id'],
            'sessionId': row['session_id'],
            'sprintId': row['sprint_id'],
            'participantId': row['participant_id'],
            'participantName': row['participant_name'],
            'choiceIndex': row['choice_index'],
            'choiceText': row['choice_text'],
            'createdAt': row['created_at'],
        }

    def get_all_engineer_votes(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Return all sprint question votes for a session/sprint, ordered by created_at."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM sprint_question_votes
            WHERE session_id = ? AND sprint_id = ?
            ORDER BY created_at ASC
        ''', (session_code, sprint_id))
        return [{
            'id': row['id'],
            'sessionId': row['session_id'],
            'sprintId': row['sprint_id'],
            'participantId': row['participant_id'],
            'participantName': row['participant_name'],
            'choiceIndex': row['choice_index'],
            'choiceText': row['choice_text'],
            'createdAt': row['created_at'],
        } for row in cursor.fetchall()]

    def submit_head_to_head_vote(self, session_code: str, sprint_id: str, participant_id: str,
                                  participant_name: str, voter_type_id: Optional[str],
                                  voter_type_name: Optional[str], chosen_role_id: str) -> None:
        """Upsert a head-to-head vote. One vote per (sprint_id, participant_id)."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT session_id FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return
        session_id = row['session_id']
        now = datetime.now(timezone.utc).isoformat()
        vote_id = str(uuid4())
        cursor.execute('''
            INSERT OR REPLACE INTO head_to_head_votes
                (id, session_id, sprint_id, participant_id, participant_name,
                 voter_type_id, voter_type_name, chosen_role_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (vote_id, session_id, sprint_id, participant_id, participant_name,
              voter_type_id, voter_type_name, chosen_role_id, now))
        self.conn.commit()
        self._bump_version(session_code)
        self._log_event('head_to_head_vote_submitted', session_code, {
            'participant_id': participant_id,
            'participant_name': participant_name,
            'sprint_id': sprint_id,
            'chosen_role_id': chosen_role_id,
            'voter_type_id': voter_type_id,
            'voter_type_name': voter_type_name,
        })

    def get_head_to_head_votes(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """Return all head-to-head votes for a session/sprint."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM head_to_head_votes
            WHERE session_id = ? AND sprint_id = ?
            ORDER BY created_at ASC
        ''', (session_code, sprint_id))
        return [{
            'id': row['id'],
            'sessionId': row['session_id'],
            'sprintId': row['sprint_id'],
            'participantId': row['participant_id'],
            'participantName': row['participant_name'],
            'voterTypeId': row['voter_type_id'],
            'voterTypeName': row['voter_type_name'],
            'chosenRoleId': row['chosen_role_id'],
            'createdAt': row['created_at'],
        } for row in cursor.fetchall()]

    def calculate_and_store_scores(self, session_code: str, sprint_id: str) -> None:
        """Compute and persist scores for a sprint.

        1. Tally voter (stakeholder) sprint question votes → majority_choice
        2. Award 1 point to each player whose choice matches majority_choice
        3. Tally head-to-head votes → winning role (lexicographic tiebreak)
        4. Award 1 point to the player assigned the winning role
        5. Increment session version
        """
        cursor = self.conn.cursor()

        # Double-reveal guard: skip if scores already exist for this sprint
        cursor.execute(
            'SELECT COUNT(*) as cnt FROM scores WHERE session_id = ? AND sprint_id = ?',
            (session_code, sprint_id))
        if cursor.fetchone()['cnt'] > 0:
            return  # Already scored this sprint

        now = datetime.now(timezone.utc).isoformat()

        # --- Build role_name → role_id mapping from scenario ---
        info = self.get_session_scenario_info(session_code)
        role_name_to_id: Dict[str, str] = {}
        if info and info['scenario_id']:
            scenario = self.get_scenario(info['scenario_id'], info['scenario_version'])
            if scenario:
                for r in scenario.roles:
                    role_name_to_id[r.get('name', '')] = r.get('id', '')

        # --- Sprint question scoring (2-of-3 group rule) ---
        # Get all voter (stakeholder) votes with voter_type_id via JOIN to participants
        cursor.execute('''
            SELECT sqv.choice_index, sqv.participant_id, sqv.participant_name,
                   p.voter_type_id, p.voter_type_name
            FROM sprint_question_votes sqv
            JOIN participants p ON sqv.participant_id = p.id AND sqv.session_id = p.session_id
            WHERE sqv.session_id = ? AND sqv.sprint_id = ? AND p.role = 'voter'
        ''', (session_code, sprint_id))
        voter_votes = cursor.fetchall()

        # Compute engineer majority (needed for tiebreaker)
        cursor.execute('''
            SELECT sqv.choice_index FROM sprint_question_votes sqv
            JOIN participants p ON sqv.participant_id = p.id AND sqv.session_id = p.session_id
            WHERE sqv.session_id = ? AND sqv.sprint_id = ? AND p.role = 'player'
        ''', (session_code, sprint_id))
        engineer_vote_rows = cursor.fetchall()

        engineer_tally: Dict[int, int] = {}
        for v in engineer_vote_rows:
            choice = v['choice_index']
            engineer_tally[choice] = engineer_tally.get(choice, 0) + 1

        engineer_majority = None
        if engineer_tally:
            max_eng = max(engineer_tally.values())
            tied_eng = [c for c, cnt in engineer_tally.items() if cnt == max_eng]
            if len(tied_eng) == 1:
                engineer_majority = tied_eng[0]

        # Determine winning answer using 2-of-3 group rule
        winning_answer = None
        group_positions: Dict[str, int] = {}  # voter_type_id → winning choice_index

        if voter_votes:
            # Group votes by voter_type_id
            groups: Dict[str, Dict[int, int]] = {}  # voter_type_id → {choice_index: count}
            for v in voter_votes:
                vt_id = v['voter_type_id']
                if vt_id is None:
                    continue  # Skip voters without a group assignment
                if vt_id not in groups:
                    groups[vt_id] = {}
                choice = v['choice_index']
                groups[vt_id][choice] = groups[vt_id].get(choice, 0) + 1

            # Determine each group's position (internal majority)
            # Build voter_type_id → name lookup from voter votes
            vt_id_to_name: Dict[str, str] = {}
            for v in voter_votes:
                vt_id = v['voter_type_id']
                if vt_id and vt_id not in vt_id_to_name:
                    vt_id_to_name[vt_id] = v['voter_type_name'] or vt_id

            for vt_id, tally in groups.items():
                if not tally:
                    continue
                max_count = max(tally.values())
                tied_choices = [c for c, cnt in tally.items() if cnt == max_count]
                if len(tied_choices) == 1:
                    group_positions[vt_id] = tied_choices[0]
                else:
                    # Internal tie — use engineer tiebreaker
                    if engineer_majority is not None:
                        group_positions[vt_id] = engineer_majority
                        self._log_event('group_tie_resolved_by_engineers', session_code, {
                            'sprint_id': sprint_id,
                            'voter_type_id': vt_id,
                            'group_name': vt_id_to_name.get(vt_id, vt_id),
                            'resolved_choice': engineer_majority,
                        })
                    else:
                        # Engineers also tied — exclude this group
                        self._log_event('scoring_tiebreaker_unresolved', session_code, {
                            'sprint_id': sprint_id,
                            'voter_type_id': vt_id,
                            'group_name': vt_id_to_name.get(vt_id, vt_id),
                            'reason': 'both_group_and_engineers_tied',
                        })

            # Apply 2-of-3 rule: count how many groups chose each option
            choice_group_count: Dict[int, int] = {}  # choice_index → number of groups
            for vt_id, choice in group_positions.items():
                choice_group_count[choice] = choice_group_count.get(choice, 0) + 1

            # Find the winning answer (choice with 2+ groups)
            for choice, count in choice_group_count.items():
                if count >= 2:
                    winning_answer = choice
                    break

            # Fallback: if fewer than 2 groups have votes, use overall majority
            if winning_answer is None and len(group_positions) < 2:
                overall_tally: Dict[int, int] = {}
                for v in voter_votes:
                    choice = v['choice_index']
                    overall_tally[choice] = overall_tally.get(choice, 0) + 1
                if overall_tally:
                    winning_answer = max(overall_tally, key=lambda k: overall_tally[k])
                self._log_event('scoring_fallback_overall_majority', session_code, {
                    'sprint_id': sprint_id,
                    'reason': 'fewer_than_2_groups_with_votes',
                    'groups_with_votes': len(group_positions),
                })

        # Award engineer points based on winning_answer
        if winning_answer is not None:
            # Get all player (engineer) votes
            cursor.execute('''
                SELECT sqv.* FROM sprint_question_votes sqv
                JOIN participants p ON sqv.participant_id = p.id AND sqv.session_id = p.session_id
                WHERE sqv.session_id = ? AND sqv.sprint_id = ? AND p.role = 'player'
            ''', (session_code, sprint_id))
            player_votes = cursor.fetchall()

            for pv in player_votes:
                if pv['choice_index'] == winning_answer:
                    pid = pv['participant_id']
                    pname = pv['participant_name']
                    # Look up participant's assigned role
                    cursor.execute(
                        'SELECT assigned_role_name FROM participants WHERE id = ? AND session_id = ?',
                        (pid, session_code))
                    p_row = cursor.fetchone()
                    assigned_role_name = p_row['assigned_role_name'] if p_row else ''
                    assigned_role_id = role_name_to_id.get(assigned_role_name, '')

                    cursor.execute('''
                        INSERT INTO scores (id, session_id, participant_id, participant_name,
                            assigned_role_id, assigned_role_name, sprint_id, points, reason, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (str(uuid4()), session_code, pid, pname,
                          assigned_role_id, assigned_role_name or '',
                          sprint_id, 1, 'Matched stakeholder group majority on corporate choice', now))

        # --- Group scoring ---
        if winning_answer is not None:
            # Get canonical voter type names for display
            voter_types = self.get_voter_types_for_session(session_code)
            vt_name_map = {vt.id: vt.name for vt in voter_types}

            for vt_id, choice in group_positions.items():
                if choice == winning_answer:
                    vt_name = vt_name_map.get(vt_id, vt_id)
                    cursor.execute('''
                        INSERT INTO scores (id, session_id, participant_id, participant_name,
                            assigned_role_id, assigned_role_name, sprint_id, points, reason, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (str(uuid4()), session_code, vt_id, vt_name,
                          'group', vt_name,
                          sprint_id, 1, 'Group majority matched winning answer on corporate choice', now))

        # --- Individual voter scoring ---
        if winning_answer is not None:
            for vote in voter_votes:
                if vote['voter_type_id'] is None:
                    continue  # Skip voters without a group — edge case only
                if vote['choice_index'] == winning_answer:
                    pid = vote['participant_id']
                    pname = vote['participant_name']
                    vt_id = vote['voter_type_id']
                    vt_name = vote['voter_type_name'] or 'Voter'
                    cursor.execute('''
                        INSERT INTO scores (id, session_id, participant_id, participant_name,
                            assigned_role_id, assigned_role_name, sprint_id, points, reason, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (str(uuid4()), session_code, pid, pname,
                          vt_id, vt_name,
                          sprint_id, 1, 'Individual vote matched winning answer on corporate choice', now))

        # --- Head-to-head scoring ---
        cursor.execute('''
            SELECT chosen_role_id FROM head_to_head_votes
            WHERE session_id = ? AND sprint_id = ?
        ''', (session_code, sprint_id))
        h2h_votes = cursor.fetchall()

        if h2h_votes:
            h2h_tally: Dict[str, int] = {}
            for v in h2h_votes:
                rid = v['chosen_role_id']
                h2h_tally[rid] = h2h_tally.get(rid, 0) + 1

            # Winner: highest count; on tie, lexicographically lower role ID wins
            max_count = max(h2h_tally.values())
            tied_roles = [rid for rid, cnt in h2h_tally.items() if cnt == max_count]
            winning_role_id = min(tied_roles)

            # Find the player assigned the winning role
            # Build reverse mapping: role_id → role_name
            role_id_to_name = {v: k for k, v in role_name_to_id.items()}
            winning_role_name = role_id_to_name.get(winning_role_id, '')

            # Find participant with that assigned_role_name and role='player'
            cursor.execute('''
                SELECT id, name, assigned_role_name FROM participants
                WHERE session_id = ? AND role = 'player' AND assigned_role_name = ?
            ''', (session_code, winning_role_name))
            winner = cursor.fetchone()

            if winner:
                cursor.execute('''
                    INSERT INTO scores (id, session_id, participant_id, participant_name,
                        assigned_role_id, assigned_role_name, sprint_id, points, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (str(uuid4()), session_code, winner['id'], winner['name'],
                      winning_role_id, winner['assigned_role_name'] or '',
                      sprint_id, 1, 'Won head-to-head stakeholder vote', now))

        self.conn.commit()
        self._bump_version(session_code)

        # Compute score counts by category for logging
        engineer_scores_count = 0
        group_scores_count = 0
        voter_scores_count = 0
        cursor.execute(
            'SELECT reason FROM scores WHERE session_id = ? AND sprint_id = ?',
            (session_code, sprint_id))
        for score_row in cursor.fetchall():
            reason = score_row['reason']
            if reason == 'Matched stakeholder group majority on corporate choice':
                engineer_scores_count += 1
            elif reason == 'Group majority matched winning answer on corporate choice':
                group_scores_count += 1
            elif reason == 'Individual vote matched winning answer on corporate choice':
                voter_scores_count += 1

        # Determine which groups agreed with the winning answer
        groups_agreeing = []
        if winning_answer is not None and voter_votes:
            for vt_id, choice in group_positions.items():
                if choice == winning_answer:
                    groups_agreeing.append(vt_id)

        self._log_event('scores_computed', session_code, {
            'sprint_id': sprint_id,
            'winning_answer': winning_answer,
            'groups_agreeing': groups_agreeing,
            'engineer_scores_count': engineer_scores_count,
            'group_scores_count': group_scores_count,
            'voter_scores_count': voter_scores_count,
        })

    def get_scores(self, session_code: str) -> List[Dict[str, Any]]:
        """Return scores grouped by participant with totalPoints and breakdown array."""
        cursor = self.conn.cursor()
        # Get aggregated totals per participant
        cursor.execute('''
            SELECT participant_id, participant_name, assigned_role_id, assigned_role_name,
                   SUM(points) as total_points
            FROM scores
            WHERE session_id = ?
            GROUP BY participant_id
            ORDER BY total_points DESC
        ''', (session_code,))
        participants_agg = cursor.fetchall()

        result = []
        for pa in participants_agg:
            pid = pa['participant_id']
            # Get breakdown for this participant
            cursor.execute('''
                SELECT sprint_id, points, reason FROM scores
                WHERE session_id = ? AND participant_id = ?
                ORDER BY created_at ASC
            ''', (session_code, pid))
            breakdown = [{
                'sprintId': row['sprint_id'],
                'points': row['points'],
                'reason': row['reason'],
            } for row in cursor.fetchall()]

            result.append({
                'participantId': pa['participant_id'],
                'participantName': pa['participant_name'],
                'assignedRoleId': pa['assigned_role_id'],
                'assignedRoleName': pa['assigned_role_name'],
                'totalPoints': pa['total_points'],
                'breakdown': breakdown,
            })

        return result

    def set_sprint_question_phase(self, session_code: str, phase: str) -> None:
        """Set the sprint question phase. Validates against the five allowed values."""
        valid_phases = ('inactive', 'engineer_vote', 'discussion', 'stakeholder_vote', 'reveal')
        if phase not in valid_phases:
            raise ValueError(f"Invalid phase: {phase}. Must be one of {valid_phases}")
        # Capture previous phase for logging
        previous_phase = self.get_sprint_question_phase(session_code)
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE sessions SET sprint_question_phase = ? WHERE session_id = ?',
            (phase, session_code))
        self.conn.commit()
        self._bump_version(session_code)
        self._log_event('phase_changed', session_code, {
            'from_phase': previous_phase,
            'to_phase': phase,
        })

    def get_sprint_question_phase(self, session_code: str) -> str:
        """Return the current sprint question phase, defaulting to 'inactive'."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT sprint_question_phase FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row or not row['sprint_question_phase']:
            return 'inactive'
        return row['sprint_question_phase']

    def set_scoreboard_visible(self, session_code: str, visible: bool) -> None:
        """Update scoreboard visibility and increment version."""
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE sessions SET scoreboard_visible = ? WHERE session_id = ?',
            (1 if visible else 0, session_code))
        self.conn.commit()
        self._bump_version(session_code)
        self._log_event('scoreboard_toggled', session_code, {
            'visible': visible,
        })

    # ==================== Role PIN Methods ====================

    def _generate_unique_pin(self, existing_pins: set) -> str:
        """Generate a 4-digit zero-padded PIN not in the existing set."""
        while True:
            pin = f"{random.randint(0, 9999):04d}"
            if pin not in existing_pins:
                return pin

    def generate_role_pins(self, session_code: str) -> int:
        """Generate unique PINs for all player roles in the session's scenario.
        
        Returns the count of PINs generated. Idempotent — skips if PINs already exist.
        """
        cursor = self.conn.cursor()
        
        # Check if PINs already exist for this session (idempotent)
        cursor.execute('SELECT COUNT(*) as cnt FROM role_pins WHERE session_id = ?', (session_code,))
        if cursor.fetchone()['cnt'] > 0:
            return 0  # PINs already generated
        
        # Get scenario info for this session
        info = self.get_session_scenario_info(session_code)
        if not info or not info['scenario_id']:
            return 0
        
        # Load scenario to get player roles
        scenario = self.get_scenario(info['scenario_id'], info['scenario_version'])
        if not scenario:
            return 0
        
        # Filter to player roles only
        player_roles = [r for r in scenario.roles if r.get('roleType') == 'player']
        if not player_roles:
            return 0
        
        # Generate unique PINs for each role
        existing_pins: set = set()
        pins_to_insert = []
        
        for role in player_roles:
            pin = self._generate_unique_pin(existing_pins)
            existing_pins.add(pin)
            pins_to_insert.append((
                session_code,
                role.get('id', ''),
                role.get('name', ''),
                pin,
            ))
        
        # Insert all PINs
        try:
            cursor.execute('BEGIN TRANSACTION')
            cursor.executemany('''
                INSERT INTO role_pins (session_id, role_id, role_name, pin)
                VALUES (?, ?, ?, ?)
            ''', pins_to_insert)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        return len(pins_to_insert)

    def validate_role_pin(self, session_code: str, pin: str) -> Optional[RolePin]:
        """Look up a PIN in the session. Returns RolePin if found, None otherwise."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT session_id, role_id, role_name, pin, 
                   claimed_by_participant_id, claimed_by_name, claimed_at
            FROM role_pins
            WHERE session_id = ? AND pin = ?
        ''', (session_code, pin))
        row = cursor.fetchone()
        if not row:
            return None
        return RolePin(
            session_id=row['session_id'],
            role_id=row['role_id'],
            role_name=row['role_name'],
            pin=row['pin'],
            claimed_by_participant_id=row['claimed_by_participant_id'],
            claimed_by_name=row['claimed_by_name'],
            claimed_at=row['claimed_at'],
        )

    def claim_role_pin(self, session_code: str, role_id: str, 
                       participant_id: str, participant_name: str) -> bool:
        """Mark a role as claimed by a participant. Returns True on success."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE role_pins 
            SET claimed_by_participant_id = ?, claimed_by_name = ?, claimed_at = ?
            WHERE session_id = ? AND role_id = ?
        ''', (participant_id, participant_name, now, session_code, role_id))
        self.conn.commit()
        return cursor.rowcount > 0

    def release_role_pin(self, session_code: str, participant_id: str) -> bool:
        """Release a role claim when a player is removed. Returns True if a claim was released."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE role_pins 
            SET claimed_by_participant_id = NULL, claimed_by_name = NULL, claimed_at = NULL
            WHERE session_id = ? AND claimed_by_participant_id = ?
        ''', (session_code, participant_id))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_role_pins(self, session_code: str) -> List[RolePin]:
        """Get all role PINs for a session with claim status."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT session_id, role_id, role_name, pin, 
                   claimed_by_participant_id, claimed_by_name, claimed_at
            FROM role_pins
            WHERE session_id = ?
            ORDER BY role_name
        ''', (session_code,))
        return [
            RolePin(
                session_id=row['session_id'],
                role_id=row['role_id'],
                role_name=row['role_name'],
                pin=row['pin'],
                claimed_by_participant_id=row['claimed_by_participant_id'],
                claimed_by_name=row['claimed_by_name'],
                claimed_at=row['claimed_at'],
            )
            for row in cursor.fetchall()
        ]

    def get_role_pin_count(self, session_code: str) -> int:
        """Return total count of role PINs for a session. Used to check if PINs exist."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) as cnt FROM role_pins WHERE session_id = ?', (session_code,))
        return cursor.fetchone()['cnt']

    def get_unclaimed_role_count(self, session_code: str) -> int:
        """Return count of unclaimed roles. Used to determine if 'Player' option should show."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as cnt FROM role_pins
            WHERE session_id = ? AND claimed_by_participant_id IS NULL
        ''', (session_code,))
        return cursor.fetchone()['cnt']

    def get_unclaimed_roles(self, session_code: str) -> List[Dict[str, Any]]:
        """Return unclaimed roles with their details (id, name, description).
        
        Used to show available roles when a player needs to enter a role PIN.
        """
        cursor = self.conn.cursor()
        
        # Get unclaimed role IDs from role_pins
        cursor.execute('''
            SELECT role_id, role_name FROM role_pins
            WHERE session_id = ? AND claimed_by_participant_id IS NULL
            ORDER BY role_name
        ''', (session_code,))
        unclaimed_pins = cursor.fetchall()
        
        if not unclaimed_pins:
            return []
        
        # Get scenario info to retrieve role descriptions
        info = self.get_session_scenario_info(session_code)
        if not info or not info['scenario_id']:
            # Return basic info without descriptions
            return [{'id': row['role_id'], 'name': row['role_name'], 'description': ''} for row in unclaimed_pins]
        
        scenario = self.get_scenario(info['scenario_id'], info['scenario_version'])
        if not scenario:
            return [{'id': row['role_id'], 'name': row['role_name'], 'description': ''} for row in unclaimed_pins]
        
        # Build a map of role_id -> description from scenario
        role_descriptions = {r.get('id'): r.get('description', '') for r in scenario.roles}
        
        return [
            {
                'id': row['role_id'],
                'name': row['role_name'],
                'description': role_descriptions.get(row['role_id'], ''),
            }
            for row in unclaimed_pins
        ]

    def is_facilitator(self, session_code: str, participant_id: str) -> bool:
        """Check if a participant is the facilitator for a session."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 1 FROM participants
            WHERE session_id = ? AND id = ? AND role = 'facilitator'
        ''', (session_code, participant_id))
        return cursor.fetchone() is not None

    def replace_player_for_role(self, session_code: str, role_id: str,
                                new_participant_name: str, ip_address: Optional[str] = None) -> Optional[Participant]:
        """Replace the current claimant with a new participant (reconnection).
        
        Removes old participant, creates new one with same role assignment,
        and updates the role_pins claim fields.
        """
        cursor = self.conn.cursor()
        
        # Get the role PIN record to find the old claimant and role info
        cursor.execute('''
            SELECT role_id, role_name, claimed_by_participant_id, claimed_by_name
            FROM role_pins
            WHERE session_id = ? AND role_id = ?
        ''', (session_code, role_id))
        role_pin_row = cursor.fetchone()
        if not role_pin_row:
            return None
        
        old_participant_id = role_pin_row['claimed_by_participant_id']
        role_name = role_pin_row['role_name']
        
        # Get the scenario info to retrieve role details (description, privateInfo)
        info = self.get_session_scenario_info(session_code)
        role_description = None
        private_info = None
        if info and info['scenario_id']:
            scenario = self.get_scenario(info['scenario_id'], info['scenario_version'])
            if scenario:
                for r in scenario.roles:
                    if r.get('id') == role_id:
                        role_description = r.get('description')
                        private_info = r.get('privateInfo')
                        break
        
        now = datetime.now(timezone.utc).isoformat()
        new_participant_id = str(uuid4())
        
        try:
            cursor.execute('BEGIN TRANSACTION')
            
            # Remove old participant if exists
            if old_participant_id:
                cursor.execute(
                    'DELETE FROM participants WHERE id = ? AND session_id = ?',
                    (old_participant_id, session_code)
                )
            
            # Create new participant with the role assignment
            cursor.execute('''
                INSERT INTO participants (id, session_id, name, role, assigned_role_name, assigned_role_id,
                    role_type, role_description, private_info, ip_address, joined_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (new_participant_id, session_code, new_participant_name, 'player',
                  role_name, role_id, 'player', role_description, private_info, ip_address, now))
            
            # Update role_pins claim fields
            cursor.execute('''
                UPDATE role_pins 
                SET claimed_by_participant_id = ?, claimed_by_name = ?, claimed_at = ?
                WHERE session_id = ? AND role_id = ?
            ''', (new_participant_id, new_participant_name, now, session_code, role_id))
            
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
        self._bump_version(session_code)
        
        return Participant(
            id=new_participant_id,
            name=new_participant_name,
            role='player',
            assigned_role_name=role_name,
            assigned_role_id=role_id,
            role_type='player',
            role_description=role_description,
            private_info=private_info,
            ip_address=ip_address,
            joined_at=now,
        )

    # ==================== Tutorial Administration ====================

    def list_tutorials(self) -> List[TutorialVersion]:
        return self.tutorials.list_tutorials()

    def get_tutorial(self, tutorial_id: str, version: Optional[int] = None) -> Optional[TutorialVersion]:
        return self.tutorials.get_tutorial(tutorial_id, version)

    def get_published_tutorial(self, tutorial_id: str) -> Optional[TutorialVersion]:
        return self.tutorials.get_published_tutorial(tutorial_id)

    def get_tutorial_activation(self) -> TutorialActivation:
        return self.tutorials.get_tutorial_activation()

    def activate_tutorial(self, tutorial_id: str,
                          expected_activation_revision: int) -> TutorialActivation:
        return self.tutorials.activate_tutorial(tutorial_id, expected_activation_revision)

    def get_tutorial_history(self, tutorial_id: str) -> List[TutorialVersion]:
        return self.tutorials.get_tutorial_history(tutorial_id)

    def create_tutorial(self, config: Dict[str, Any], created_by: Optional[str] = None) -> TutorialVersion:
        return self.tutorials.create_tutorial(config, created_by)

    def update_tutorial(self, tutorial_id: str, config: Dict[str, Any], expected_revision: int,
                        created_by: Optional[str] = None) -> Optional[TutorialVersion]:
        return self.tutorials.update_tutorial(tutorial_id, config, expected_revision, created_by)

    def publish_tutorial(self, tutorial_id: str, version: int,
                         expected_revision: int) -> Optional[TutorialVersion]:
        return self.tutorials.publish_tutorial(tutorial_id, version, expected_revision)

    def disable_tutorial(self, tutorial_id: str,
                         expected_revision: int) -> Optional[TutorialVersion]:
        return self.tutorials.disable_tutorial(tutorial_id, expected_revision)


# Initialize SQLite repository - fail fast if unable to connect
try:
    repo = SqliteRepository()
except Exception as e:
    print(f"FATAL: Could not initialize SQLite repository: {e}")
    print(f"Database path: {DB_PATH}")
    print("Ensure the data directory exists and is writable.")
    raise  # Fail fast - don't start the application

"""Voting repository for managing response voting functionality."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

from .models import VoterInfo


class VotingRepository:
    """Repository for voting operations on responses."""
    
    def __init__(self, conn: sqlite3.Connection):
        """Initialize with a database connection."""
        self.conn = conn
    
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
        """Add a vote to a response. Returns True if added, False if already exists (idempotent).
        
        Idempotency Enforcement:
        The votes table has a UNIQUE constraint on (response_id, participant_id) which prevents
        duplicate votes from the same participant on the same response. When a duplicate vote
        submission occurs, the database raises an IntegrityError which is caught and handled
        gracefully by returning False. This ensures idempotent behavior - duplicate submissions
        do not create duplicate records and do not raise errors to the client.
        
        This is correct behavior: the first vote succeeds (returns True), and subsequent duplicate
        submissions are silently ignored (return False), maintaining exactly one vote per
        participant per response.
        """
        cursor = self.conn.cursor()

        # Get sprint_id from current session state
        cursor.execute('SELECT current_sprint_index, sprints_json FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return False
        sprints = json.loads(row['sprints_json']) if row['sprints_json'] else []
        current_index = row['current_sprint_index']
        sprint_id = sprints[current_index]['id'] if current_index < len(sprints) else 's1'

        vote_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            cursor.execute('''
                INSERT INTO votes (id, session_id, response_id, participant_id, participant_name, participant_role, sprint_id, created_at, voter_type_id, voter_type_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (vote_id, session_code, response_id, participant_id, participant_name, participant_role, sprint_id, now, voter_type_id, voter_type_name))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # UNIQUE constraint violation on (response_id, participant_id) — duplicate vote detected.
            # This enforces idempotency: returning False indicates the vote already exists,
            # but this is not an error condition. The client can treat this as a successful
            # idempotent operation (no duplicate record created, no error raised).
            return False
    
    def remove_vote(self, session_code: str, response_id: str, participant_id: str) -> bool:
        """Remove a participant's vote from a response. Returns True if deleted, False if not found."""
        cursor = self.conn.cursor()
        cursor.execute(
            'DELETE FROM votes WHERE session_id = ? AND response_id = ? AND participant_id = ?',
            (session_code, response_id, participant_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0
    
    def remove_vote_by_id(self, vote_id: str) -> Optional[Dict[str, Any]]:
        """Remove a specific vote by ID. Returns vote details for logging, or None if not found."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM votes WHERE id = ?', (vote_id,))
        row = cursor.fetchone()
        if not row:
            return None

        vote_details = {
            'id': row['id'],
            'session_id': row['session_id'],
            'response_id': row['response_id'],
            'participant_id': row['participant_id'],
            'participant_name': row['participant_name'],
            'participant_role': row['participant_role'],
            'sprint_id': row['sprint_id'],
            'created_at': row['created_at'],
        }

        cursor.execute('DELETE FROM votes WHERE id = ?', (vote_id,))
        self.conn.commit()
        return vote_details
    
    def get_votes_for_responses(
        self,
        response_ids: List[str],
        participant_id: Optional[str] = None,
        include_voters: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get vote data for multiple responses.
        Returns dict keyed by response_id with vote_count, current_user_voted, voters.
        """
        if not response_ids:
            return {}

        cursor = self.conn.cursor()
        placeholders = ','.join('?' * len(response_ids))

        # Get vote counts per response
        cursor.execute(
            f'SELECT response_id, COUNT(*) as vote_count FROM votes WHERE response_id IN ({placeholders}) GROUP BY response_id',
            response_ids,
        )
        counts: Dict[str, int] = {row['response_id']: row['vote_count'] for row in cursor.fetchall()}

        # Check if current user voted on each response
        user_voted: Dict[str, bool] = {}
        if participant_id:
            cursor.execute(
                f'SELECT response_id FROM votes WHERE response_id IN ({placeholders}) AND participant_id = ?',
                response_ids + [participant_id],
            )
            voted_ids = {row['response_id'] for row in cursor.fetchall()}
            user_voted = {rid: (rid in voted_ids) for rid in response_ids}

        # Fetch voter details if requested
        voters_by_response: Dict[str, List[VoterInfo]] = {}
        if include_voters:
            cursor.execute(
                f'SELECT response_id, id, participant_id, participant_name, participant_role, created_at, voter_type_id, voter_type_name FROM votes WHERE response_id IN ({placeholders}) ORDER BY created_at ASC',
                response_ids,
            )
            for row in cursor.fetchall():
                rid = row['response_id']
                if rid not in voters_by_response:
                    voters_by_response[rid] = []
                row_keys = row.keys()
                voters_by_response[rid].append(VoterInfo(
                    participant_id=row['participant_id'],
                    participant_name=row['participant_name'],
                    participant_role=row['participant_role'],
                    created_at=row['created_at'],
                    vote_id=row['id'],
                    voter_type_id=row['voter_type_id'] if 'voter_type_id' in row_keys else None,
                    voter_type_name=row['voter_type_name'] if 'voter_type_name' in row_keys else None,
                ))

        result: Dict[str, Dict[str, Any]] = {}
        for rid in response_ids:
            result[rid] = {
                'vote_count': counts.get(rid, 0),
                'current_user_voted': user_voted.get(rid, False),
                'voters': voters_by_response.get(rid, []) if include_voters else None,
            }
        return result
    
    def set_voting_enabled(self, session_code: str, enabled: bool) -> bool:
        """Enable or disable voting for a session. Returns True on success."""
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE sessions SET voting_enabled = ? WHERE session_id = ?',
            (1 if enabled else 0, session_code),
        )
        self.conn.commit()
        return cursor.rowcount > 0
    
    def toggle_voting_show_voters(self, session_code: str) -> bool:
        """Toggle voter visibility setting. Returns new boolean state."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT voting_show_voters FROM sessions WHERE session_id = ?', (session_code,))
        row = cursor.fetchone()
        if not row:
            return False
        new_value = 0 if row['voting_show_voters'] else 1
        cursor.execute(
            'UPDATE sessions SET voting_show_voters = ? WHERE session_id = ?',
            (new_value, session_code),
        )
        self.conn.commit()
        return bool(new_value)
    
    def get_vote_summary_by_role(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """
        Get vote summary grouped by participant_role for a session/sprint.
        Returns list of {role_name, vote_count, response_count}.
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT
                participant_role AS role_name,
                COUNT(*) AS vote_count,
                COUNT(DISTINCT response_id) AS response_count
            FROM votes
            WHERE session_id = ? AND sprint_id = ?
            GROUP BY participant_role
            ORDER BY vote_count DESC
        ''', (session_code, sprint_id))
        return [
            {
                'role_name': row['role_name'],
                'vote_count': row['vote_count'],
                'response_count': row['response_count'],
            }
            for row in cursor.fetchall()
        ]

    def get_vote_summary_by_voter_type(self, session_code: str, sprint_id: str) -> List[Dict[str, Any]]:
        """
        Get vote summary grouped by voter_type_id and voter_type_name for a session/sprint.
        Returns list of {voterTypeName, voteCount, responseCount}.
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT voter_type_id, voter_type_name,
                   COUNT(*) AS vote_count,
                   COUNT(DISTINCT response_id) AS response_count
            FROM votes
            WHERE session_id = ? AND sprint_id = ?
            GROUP BY voter_type_id, voter_type_name
            ORDER BY vote_count DESC
        ''', (session_code, sprint_id))
        return [
            {
                'voterTypeName': row['voter_type_name'],
                'voteCount': row['vote_count'],
                'responseCount': row['response_count'],
            }
            for row in cursor.fetchall()
        ]

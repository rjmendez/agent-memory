#!/usr/bin/env python3
"""
MrPink Memory Database Client

Provides ORM-like access to the memory database with common queries
and mutation patterns.
"""

import json
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from enum import Enum
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.errors import UniqueViolation, ForeignKeyViolation

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    DECISION = "decision"
    FINDING = "finding"
    CONTACT = "contact"
    CONTEXT = "context"
    LESSON = "lesson"
    TODO = "todo"
    NOTE = "note"
    WORKFLOW = "workflow"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, Enum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    PAID = "paid"
    DISPUTED = "disputed"


class DecisionStatus(str, Enum):
    PENDING = "pending"
    IMPLEMENTED = "implemented"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    DEFERRED = "deferred"


class ContactRole(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    SERVICE = "service"
    EXTERNAL = "external"


class ContactProtocol(str, Enum):
    A2A = "a2a"
    MATRIX = "matrix"
    EMAIL = "email"
    SMS = "sms"
    API = "api"


class MrPinkMemory:
    """MrPink Memory Database Client"""
    
    def __init__(self, 
                 host: str = "localhost",
                 port: int = 5433,
                 database: str = "mrpink_memory",
                 user: str = "mrpink",
                 password: str = "MrPink-Memory-Secure-2026"):
        """Initialize connection to memory database."""
        self.conn_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password,
        }
        self.conn = None
        self.connect()
    
    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(**self.conn_params)
            self.conn.autocommit = False
            logger.info("Connected to MrPink memory database")
        except psycopg2.Error as e:
            logger.error(f"Failed to connect to memory database: {e}")
            raise
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Closed memory database connection")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # =====================================================================
    # MEMORY OPERATIONS
    # =====================================================================
    
    def add_memory(self,
                   title: str,
                   content: str,
                   memory_type: MemoryType = MemoryType.NOTE,
                   importance: int = 3,
                   tags: Optional[List[str]] = None,
                   source_file: Optional[str] = None) -> UUID:
        """Add a new memory."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO memories (title, content, type, importance, tags, source_file)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (title, content, memory_type.value, importance, tags or [], source_file))
            result = cur.fetchone()
            self.conn.commit()
            return result['id']
    
    def search_memories(self, 
                       query: str,
                       memory_type: Optional[MemoryType] = None,
                       limit: int = 10) -> List[Dict[str, Any]]:
        """Full-text search memories."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """
                SELECT m.id, m.title, m.content, m.type, m.created_at, m.importance
                FROM memories m
                JOIN full_text_search fts ON m.id = fts.memory_id
                WHERE to_tsvector('english', fts.content) @@ 
                      plainto_tsquery('english', %s)
            """
            params = [query]
            
            if memory_type:
                sql += " AND m.type = %s"
                params.append(memory_type.value)
            
            sql += " ORDER BY m.importance DESC, m.created_at DESC LIMIT %s"
            params.append(limit)
            
            cur.execute(sql, params)
            return cur.fetchall()
    
    def get_memory(self, memory_id: UUID) -> Optional[Dict[str, Any]]:
        """Get a specific memory."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM memories WHERE id = %s", (memory_id,))
            return cur.fetchone()
    
    def update_memory(self, memory_id: UUID, **updates) -> bool:
        """Update a memory."""
        allowed_fields = {'title', 'content', 'importance', 'tags', 'archived'}
        updates = {k: v for k, v in updates.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
        sql = f"UPDATE memories SET {set_clause} WHERE id = %s"
        
        with self.conn.cursor() as cur:
            cur.execute(sql, (*updates.values(), memory_id))
            self.conn.commit()
            return cur.rowcount > 0
    
    # =====================================================================
    # FINDING OPERATIONS
    # =====================================================================
    
    def add_finding(self,
                   program: str,
                   title: str,
                   description: str,
                   severity: FindingSeverity,
                   cvss_score: Optional[float] = None,
                   poc_path: Optional[str] = None,
                   full_report_path: Optional[str] = None,
                   memory_id: Optional[UUID] = None) -> UUID:
        """Add a new finding."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO findings 
                (program, title, description, severity, cvss_score, poc_path, 
                 full_report_path, memory_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (program, title, description, severity.value, cvss_score, 
                  poc_path, full_report_path, memory_id))
            result = cur.fetchone()
            self.conn.commit()
            return result['id']
    
    def get_findings_by_program(self, program: str) -> List[Dict[str, Any]]:
        """Get all findings for a program."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM findings 
                WHERE program = %s 
                ORDER BY date_discovered DESC
            """, (program,))
            return cur.fetchall()
    
    def get_findings_ready_for_submission(self) -> List[Dict[str, Any]]:
        """Get findings ready to submit."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ready_for_submission ORDER BY severity DESC")
            return cur.fetchall()
    
    def update_finding_status(self, 
                             finding_id: UUID, 
                             status: FindingStatus,
                             **extra_fields) -> bool:
        """Update finding status and optional fields."""
        with self.conn.cursor() as cur:
            fields = {'status': status.value}
            fields.update(extra_fields)
            
            set_clause = ", ".join(f"{k} = %s" for k in fields.keys())
            sql = f"UPDATE findings SET {set_clause} WHERE id = %s"
            
            cur.execute(sql, (*fields.values(), finding_id))
            self.conn.commit()
            return cur.rowcount > 0
    
    def get_program_stats(self) -> List[Dict[str, Any]]:
        """Get statistics by program."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM findings_by_program")
            return cur.fetchall()
    
    # =====================================================================
    # DECISION OPERATIONS
    # =====================================================================
    
    def add_decision(self,
                    context: str,
                    decision: str,
                    reasoning: Optional[str] = None,
                    alternatives: Optional[str] = None) -> UUID:
        """Record a decision."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO decisions (context, decision, reasoning, 
                                       alternatives_considered)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (context, decision, reasoning, alternatives))
            result = cur.fetchone()
            self.conn.commit()
            return result['id']
    
    def get_pending_decisions(self) -> List[Dict[str, Any]]:
        """Get decisions still pending or deferred."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pending_decisions")
            return cur.fetchall()
    
    def close_decision(self, 
                      decision_id: UUID, 
                      outcome: str,
                      status: DecisionStatus = DecisionStatus.COMPLETED) -> bool:
        """Close a decision with outcome."""
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE decisions 
                SET outcome = %s, status = %s 
                WHERE id = %s
            """, (outcome, status.value, decision_id))
            self.conn.commit()
            return cur.rowcount > 0
    
    # =====================================================================
    # CONTACT OPERATIONS
    # =====================================================================
    
    def add_contact(self,
                   name: str,
                   role: ContactRole,
                   address: str,
                   protocol: ContactProtocol,
                   trust_level: int = 3,
                   notes: Optional[str] = None) -> UUID:
        """Add a contact."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO contacts (name, role, address, protocol, trust_level, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (name, role.value, address, protocol.value, trust_level, notes))
            result = cur.fetchone()
            self.conn.commit()
            return result['id']
    
    def get_contacts_by_role(self, role: ContactRole) -> List[Dict[str, Any]]:
        """Get all contacts with a specific role."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM contacts WHERE role = %s", (role.value,))
            return cur.fetchall()
    
    def get_stale_contacts(self) -> List[Dict[str, Any]]:
        """Get contacts not contacted in 24+ hours."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stale_contacts")
            return cur.fetchall()
    
    def update_contact_last_contacted(self, contact_id: UUID) -> bool:
        """Update last_contacted timestamp."""
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE contacts 
                SET last_contacted = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (contact_id,))
            self.conn.commit()
            return cur.rowcount > 0
    
    # =====================================================================
    # ANALYTICS
    # =====================================================================
    
    def get_earnings_summary(self) -> Dict[str, Any]:
        """Get earnings summary across all programs."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                  COUNT(*) FILTER (WHERE status = 'paid') AS total_paid,
                  COALESCE(SUM(actual_payout), 0) AS total_earned,
                  AVG(EXTRACT(DAY FROM (date_paid - date_discovered))) 
                    FILTER (WHERE date_paid IS NOT NULL) AS avg_days_to_payout
                FROM findings
            """)
            return cur.fetchone()
    
    def get_weekly_productivity(self, weeks: int = 12) -> List[Dict[str, Any]]:
        """Get weekly finding statistics."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT 
                  DATE_TRUNC('week', date_discovered)::DATE AS week,
                  COUNT(*) AS findings,
                  COUNT(*) FILTER (WHERE severity = 'critical') AS critical,
                  COUNT(*) FILTER (WHERE severity = 'high') AS high,
                  AVG(cvss_score) AS avg_cvss
                FROM findings
                WHERE date_discovered > CURRENT_DATE - INTERVAL '{weeks} weeks'
                GROUP BY week
                ORDER BY week DESC
            """)
            return cur.fetchall()


if __name__ == '__main__':
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    with MrPinkMemory() as db:
        # Add a memory
        memory_id = db.add_memory(
            title="Matrix Deployment",
            content="Successfully migrated from IRC to Matrix. All 4 rooms online.",
            memory_type=MemoryType.DECISION,
            tags=["infrastructure", "matrix", "completed"]
        )
        print(f"Added memory: {memory_id}")
        
        # Add a finding
        finding_id = db.add_finding(
            program="Paxos",
            title="Share Accounting Drift",
            description="Share accounting drift in reward distribution.",
            severity=FindingSeverity.LOW,
            cvss_score=3.7
        )
        print(f"Added finding: {finding_id}")
        
        # Search
        results = db.search_memories("matrix")
        print(f"Found {len(results)} memories matching 'matrix'")
        
        # Stats
        stats = db.get_earnings_summary()
        print(f"Earnings: {stats}")

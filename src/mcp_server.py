#!/usr/bin/env python3
"""
MrPink Memory MCP Server
Exposes mrpink_memory PostgreSQL as MCP tools for direct agent access.
"""

import sys
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional
from fastmcp import FastMCP

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "mrpink_memory",
    "user": "mrpink",
    "password": "MrPink-Memory-Secure-2026",
}

mcp = FastMCP("mrpink-memory")


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ── Tasks ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_tasks(status: Optional[str] = None, owner: Optional[str] = None) -> str:
    """
    Get tasks from mrpink_memory. Optionally filter by status (pending, running,
    blocked, done, cancelled) and/or owner (agent name like MrPink, Charlie, Iris).
    Returns live data from PostgreSQL — not cached.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT title, status, owner, priority, description,
                       blocked_by, result_summary, deadline, updated_at
                FROM tasks
                WHERE 1=1
            """
            params = []
            if status:
                query += " AND status = %s"
                params.append(status)
            if owner:
                query += " AND owner ILIKE %s"
                params.append(owner)
            query += " ORDER BY priority DESC, updated_at DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            # Serialize datetimes
            result = []
            for row in rows:
                r = dict(row)
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                result.append(r)
            return json.dumps(result, indent=2)


@mcp.tool()
def update_task(title: str, status: Optional[str] = None,
                result_summary: Optional[str] = None,
                blocked_by: Optional[str] = None) -> str:
    """
    Update a task by title. Can update status, result_summary, and/or blocked_by.
    Use this to record current findings — not assumptions from memory.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            fields = []
            params = []
            if status is not None:
                fields.append("status = %s")
                params.append(status)
            if result_summary is not None:
                fields.append("result_summary = %s")
                params.append(result_summary)
            if blocked_by is not None:
                fields.append("blocked_by = %s")
                params.append(blocked_by)
            if not fields:
                return json.dumps({"error": "No fields to update"})
            fields.append("updated_at = NOW()")
            params.append(title)
            query = f"UPDATE tasks SET {', '.join(fields)} WHERE title = %s"
            cur.execute(query, params)
            affected = cur.rowcount
            conn.commit()
            return json.dumps({"updated": affected, "title": title})


# ── Memories ───────────────────────────────────────────────────────────────────

@mcp.tool()
def search_memories(query: str, limit: int = 10) -> str:
    """
    Full-text search across all memories in mrpink_memory.
    Returns title, type, content, tags, importance, and created_at.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT m.title, m.type, m.content, m.tags, m.importance, m.created_at
                FROM memories m
                JOIN full_text_search fts ON fts.memory_id = m.id
                WHERE fts.search_vector @@ plainto_tsquery('english', %s)
                   OR m.title ILIKE %s
                   OR m.content ILIKE %s
                ORDER BY m.importance DESC, m.created_at DESC
                LIMIT %s
            """, (query, f"%{query}%", f"%{query}%", limit))
            rows = cur.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                result.append(r)
            return json.dumps(result, indent=2)


@mcp.tool()
def add_memory(title: str, content: str, memory_type: str = "note",
               importance: int = 3, tags: Optional[str] = None) -> str:
    """
    Add a new memory to mrpink_memory. memory_type: note, decision, lesson,
    credential, todo, finding. importance: 1-5. tags: comma-separated string.
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO memories (type, title, content, importance, tags)
                VALUES (%s::memory_type, %s, %s, %s, %s)
                RETURNING id
            """, (memory_type, title, content, importance, tag_list))
            new_id = cur.fetchone()[0]
            conn.commit()
            return json.dumps({"created": str(new_id), "title": title})


# ── Findings ───────────────────────────────────────────────────────────────────

@mcp.tool()
def get_findings(program: Optional[str] = None, severity: Optional[str] = None,
                 status: Optional[str] = None) -> str:
    """
    Get security findings. Filter by program, severity (critical, high, medium,
    low, info), or status (new, triaged, submitted, accepted, duplicate, na).
    Returns live data from PostgreSQL.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = "SELECT program, title, description, severity, status, created_at FROM findings WHERE 1=1"
            params = []
            if program:
                query += " AND program ILIKE %s"
                params.append(f"%{program}%")
            if severity:
                query += " AND severity = %s"
                params.append(severity)
            if status:
                query += " AND status = %s"
                params.append(status)
            query += " ORDER BY severity DESC, created_at DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                result.append(r)
            return json.dumps(result, indent=2)


# ── Key Facts / Vault ──────────────────────────────────────────────────────────

@mcp.tool()
def get_key_facts(key_prefix: Optional[str] = None) -> str:
    """
    Get key facts stored in mrpink_memory. Optionally filter by key prefix
    (e.g. 'infra:', 'agent:', 'legal:'). Does NOT return encrypted credential values.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT title, content, tags, importance, created_at
                FROM memories
                WHERE type != 'credential'
                  AND archived = false
            """
            params = []
            if key_prefix:
                query += " AND (title ILIKE %s OR content ILIKE %s)"
                params.extend([f"{key_prefix}%", f"%{key_prefix}%"])
            query += " ORDER BY importance DESC, created_at DESC LIMIT 50"
            cur.execute(query, params)
            rows = cur.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
                result.append(r)
            return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

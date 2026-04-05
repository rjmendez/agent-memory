#!/usr/bin/env python3
"""
Session Memory Loader — Load critical context from PostgreSQL instead of MEMORY.md.
Called at session startup to populate memory with relevant facts.
Returns structured JSON for agent initialization.
"""

import subprocess
import psycopg2
import psycopg2.extras
import json
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,
    'database': 'mrpink_memory',
    'user': 'mrpink',
    'password': 'MrPink-Memory-Secure-2026'
}

def connect():
    return psycopg2.connect(**DB_CONFIG)

def load_session_memory():
    """Load minimal, targeted memory for session startup."""
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        
        memory = {
            'timestamp': datetime.now().isoformat(),
            'agent': 'MrPink',
            'session_context': {}
        }
        
        # 1. TEAM ROSTER (current status)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT agent_name, status, deployment, host, model, last_heartbeat
                FROM agent_status
                ORDER BY agent_name
            """)
            memory['team'] = cur.fetchall()
        
        # 2. ACTIVE TASKS (pending + running + blocked)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, status, owner, priority, deadline, blocked_by
                FROM tasks
                WHERE status IN ('pending', 'dispatched', 'running', 'blocked')
                ORDER BY priority DESC, created_at ASC
                LIMIT 10
            """)
            memory['active_tasks'] = cur.fetchall()
            for task in memory['active_tasks']:
                task['id'] = str(task['id'])
                if task['deadline']:
                    task['deadline'] = task['deadline'].isoformat()
        
        # 3. CRITICAL FACTS (credentials, legal holds, milestones)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT key, type, confidence FROM facts
                WHERE type IN ('credential', 'secret', 'compliance')
                AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY confidence DESC
            """)
            memory['critical_facts'] = {row['key']: row['type'] for row in cur.fetchall()}
        
        # 4. LEGAL HOLDS (compliance deadlines)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT subject, expires_date, reason
                FROM legal_holds
                WHERE status = 'active' AND expires_date >= CURRENT_DATE
                ORDER BY expires_date ASC
            """)
            memory['legal_holds'] = cur.fetchall()
            for hold in memory['legal_holds']:
                hold['expires_date'] = hold['expires_date'].isoformat()
        
        # 5. RECENT DECISIONS (last 7 days)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT decision, reasoning, status, date_made
                FROM decisions
                WHERE date_made >= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY date_made DESC
                LIMIT 5
            """)
            memory['recent_decisions'] = cur.fetchall()
            for decision in memory['recent_decisions']:
                decision['date_made'] = decision['date_made'].isoformat()
        
        # 6. AGENT ADDRESSES (for mesh dispatch)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT name, role, address, protocol, trust_level
                FROM contacts
                WHERE role = 'agent'
                ORDER BY trust_level DESC
            """)
            memory['agent_contacts'] = cur.fetchall()
        
        # 7. KEY CREDENTIALS REFERENCES (paths, not values)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT key, source
                FROM facts
                WHERE type IN ('credential', 'secret')
                AND expires_at IS NULL OR expires_at > NOW()
            """)
            memory['vault_paths'] = {row['key']: f"vault:{row['source']}" for row in cur.fetchall()}

        conn.close()

        # 8. KNOWN REPOS (from GitHub — prevents re-creating existing repos)
        memory['known_repos'] = _fetch_known_repos()

        return memory
    
    except Exception as e:
        print(f"Error loading session memory: {e}")
        raise


def _fetch_known_repos() -> list:
    """Fetch rjmendez GitHub repos so the agent knows what already exists."""
    try:
        result = subprocess.run(
            ['gh', 'repo', 'list', 'rjmendez', '--limit', '100',
             '--json', 'name,description,updatedAt'],
            capture_output=True, text=True, timeout=15
        )
        repos = json.loads(result.stdout or '[]')
        return [{'name': r['name'], 'description': r.get('description', ''),
                 'updated': r.get('updatedAt', '')[:10]} for r in repos]
    except Exception:
        return []  # Non-fatal — gh CLI may not be available


def format_for_display(memory):
    """Format loaded memory for human-readable output."""
    
    lines = [
        f"\n🧠 SESSION MEMORY LOADED — {memory['timestamp']}",
        f"\n👥 TEAM STATUS ({len(memory['team'])} agents):",
    ]
    
    for agent in memory['team']:
        status_icon = '🟢' if agent['status'] == 'online' else '🔴'
        lines.append(f"  {status_icon} {agent['agent_name']:12} | {agent['status']:10} | {agent['deployment']:10} | {agent['model'] or 'n/a'}")
    
    if memory['active_tasks']:
        lines.append(f"\n📋 ACTIVE TASKS ({len(memory['active_tasks'])} items):")
        for task in memory['active_tasks'][:5]:
            priority_icon = '🚨' if task['priority'] == 'critical' else '⚡' if task['priority'] == 'urgent' else '📌'
            lines.append(f"  {priority_icon} {task['title'][:40]:40} | {task['status']:10} | @{task['owner']}")
            if task['blocked_by']:
                lines.append(f"     ⚠️  Blocked by: {task['blocked_by']}")
    
    if memory['legal_holds']:
        lines.append(f"\n⚖️  LEGAL HOLDS ({len(memory['legal_holds'])} active):")
        for hold in memory['legal_holds']:
            lines.append(f"  📌 {hold['subject']}: Expires {hold['expires_date']}")
            lines.append(f"     Reason: {hold['reason']}")
    
    if memory['critical_facts']:
        lines.append(f"\n🔐 CRITICAL FACTS ({len(memory['critical_facts'])} stored in Vault)")

    if memory.get('known_repos'):
        lines.append(f"\n🐙 KNOWN REPOS ({len(memory['known_repos'])} in rjmendez/):")
        for r in memory['known_repos']:
            desc = f" — {r['description']}" if r['description'] else ''
            lines.append(f"  • {r['name']}{desc}")
        lines.append(f"  ⚠️  Check this list before creating any new repo.")
    
    return '\n'.join(lines)

def main():
    memory = load_session_memory()
    print(format_for_display(memory))
    return memory

if __name__ == '__main__':
    main()

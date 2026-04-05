#!/usr/bin/env python3
"""
pre_task_check.py — Anti-drift check: query DB + agent-core before starting any significant task.

This script is the fix for "memory drift" — the failure mode where an agent
re-invents prior work, creates duplicate repos, or presents old work as new
because it didn't check what already exists before acting.

USAGE:
    # Before starting any task involving infrastructure, code, or deployment:
    python3 pre_task_check.py "what you're about to do"

    # Example:
    python3 pre_task_check.py "create agent-memory repo"
    python3 pre_task_check.py "build matrix federation"
    python3 pre_task_check.py "deploy wireguard vpn"

OUTPUT:
    Prints a summary of what already exists related to the topic:
    - Known repos (from DB facts + git)
    - Active/completed tasks
    - agent-core knowledge matches
    - Recent memories

The agent MUST read this output and reconcile before proceeding.
If something already exists: build on it, don't rebuild it.
"""

import sys
import subprocess
import psycopg2
import psycopg2.extras
from pathlib import Path

DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,
    'database': 'mrpink_memory',
    'user': 'mrpink',
    'password': 'MrPink-Memory-Secure-2026',
}

AGENT_CORE_QUERY = str(
    Path(__file__).parent.parent.parent.parent /
    '.openclaw/workspace/skills/agent-core-library/scripts/query.py'
)
# Fallback absolute path
AGENT_CORE_QUERY_ABS = '/home/rjmendez/.openclaw/workspace/skills/agent-core-library/scripts/query.py'


def query_db(topic: str) -> dict:
    results = {
        'tasks': [],
        'memories': [],
        'facts': [],
        'repos': [],
    }
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Tasks matching topic
        cur.execute("""
            SELECT title, status, owner, priority
            FROM tasks
            WHERE title ILIKE %s OR description ILIKE %s
            ORDER BY CASE status
                WHEN 'running' THEN 1 WHEN 'pending' THEN 2
                WHEN 'blocked' THEN 3 WHEN 'done' THEN 4 ELSE 5 END
        """, (f'%{topic}%', f'%{topic}%'))
        results['tasks'] = [dict(r) for r in cur.fetchall()]

        # Memories matching topic (FTS)
        cur.execute("""
            SELECT m.title, m.type, m.importance, m.created_at,
                   LEFT(m.content, 200) AS snippet
            FROM memories m
            JOIN full_text_search fts ON m.id = fts.memory_id
            WHERE to_tsvector('english', fts.content) @@
                  plainto_tsquery('english', %s)
            AND m.archived = FALSE
            ORDER BY m.importance DESC, m.created_at DESC
            LIMIT 5
        """, (topic,))
        results['memories'] = [dict(r) for r in cur.fetchall()]

        # Facts/repos mentioning topic
        cur.execute("""
            SELECT key, type, value, source
            FROM facts
            WHERE key ILIKE %s OR value ILIKE %s
        """, (f'%{topic}%', f'%{topic}%'))
        results['facts'] = [dict(r) for r in cur.fetchall()]

        cur.close()
        conn.close()
    except Exception as e:
        print(f"  ⚠️  DB query failed: {e}", file=sys.stderr)

    return results


def query_agent_core(topic: str) -> str:
    script = AGENT_CORE_QUERY_ABS
    if not Path(script).exists():
        return "  ⚠️  agent-core query script not found"
    try:
        result = subprocess.run(
            [sys.executable, script, topic, '--top', '3'],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip() or result.stderr.strip() or "  (no results)"
    except Exception as e:
        return f"  ⚠️  agent-core query failed: {e}"


def check_github_repos(topic: str) -> list:
    """Check if any GitHub repos related to topic already exist."""
    try:
        result = subprocess.run(
            ['gh', 'repo', 'list', 'rjmendez', '--limit', '50', '--json',
             'name,description,updatedAt', '--jq',
             f'[.[] | select(.name | ascii_downcase | contains("{topic.lower()}"))]'],
            capture_output=True, text=True, timeout=15
        )
        import json
        repos = json.loads(result.stdout or '[]')
        return repos
    except Exception:
        return []


def run_check(topic: str):
    print(f"\n{'='*60}")
    print(f"🔍 PRE-TASK CHECK: \"{topic}\"")
    print(f"{'='*60}")
    print("Checking DB, agent-core, and GitHub before proceeding...\n")

    # 1. DB: Tasks
    db = query_db(topic)

    if db['tasks']:
        print(f"📋 EXISTING TASKS ({len(db['tasks'])}):")
        for t in db['tasks']:
            icon = '🟢' if t['status'] == 'done' else '🟡' if t['status'] == 'running' else '🔴'
            print(f"  {icon} [{t['status'].upper()}] {t['title']} (owner: {t['owner']}, priority: {t['priority']})")
        print()

    # 2. DB: Memories
    if db['memories']:
        print(f"🧠 RELATED MEMORIES ({len(db['memories'])}):")
        for m in db['memories']:
            date = m['created_at'].strftime('%Y-%m-%d') if m['created_at'] else '?'
            print(f"  • [{date}] {m['title']} (importance: {m['importance']}/5)")
            print(f"    {m['snippet'][:150]}...")
        print()

    # 3. DB: Facts
    if db['facts']:
        print(f"📌 RELATED FACTS:")
        for f in db['facts']:
            print(f"  • {f['key']} = {f['value']}")
        print()

    # 4. GitHub repos
    repos = check_github_repos(topic)
    if repos:
        print(f"🐙 EXISTING GITHUB REPOS:")
        for r in repos:
            print(f"  • rjmendez/{r['name']} — {r.get('description', '(no description)')}")
            print(f"    Last updated: {r.get('updatedAt', '?')}")
        print()

    # 5. agent-core
    print(f"📚 AGENT-CORE KNOWLEDGE:")
    ac = query_agent_core(topic)
    print(ac)
    print()

    # Summary verdict
    has_existing = bool(db['tasks'] or db['memories'] or repos or db['facts'])
    print(f"{'='*60}")
    if has_existing:
        print(f"⚠️  STOP — PRIOR WORK EXISTS. Review above before proceeding.")
        print(f"   Build on what exists. Do not recreate, do not re-announce as new.")
    else:
        print(f"✅ No prior work found for \"{topic}\". Safe to proceed as new work.")
    print(f"{'='*60}\n")

    return has_existing


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 pre_task_check.py \"topic or task description\"")
        sys.exit(1)
    topic = ' '.join(sys.argv[1:])
    has_existing = run_check(topic)
    sys.exit(1 if has_existing else 0)

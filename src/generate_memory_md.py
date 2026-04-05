#!/usr/bin/env python3
"""
generate_memory_md.py — Generate MEMORY.md from PostgreSQL (mrpink-memory :5433)

This script is the authoritative MEMORY.md writer.
Run nightly via cron, or manually after significant DB updates.

Usage:
    python3 generate_memory_md.py
    python3 generate_memory_md.py --output /path/to/MEMORY.md
    python3 generate_memory_md.py --dry-run   # print to stdout only
"""

import sys
import argparse
import psycopg2
import psycopg2.extras
from datetime import datetime
from pathlib import Path

DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,
    'database': 'mrpink_memory',
    'user': 'mrpink',
    'password': 'MrPink-Memory-Secure-2026'
}

DEFAULT_OUTPUT = Path.home() / '.openclaw/workspace/MEMORY.md'

HEADER = """\
# MEMORY.md — MrPink Long-Term Memory
<!-- AUTO-GENERATED from mrpink-memory PostgreSQL :5433 — do not edit by hand -->
<!-- Last generated: {generated_at} -->
<!-- To make permanent changes, write to the DB and re-run generate_memory_md.py -->

"""

STATIC_SECTION = """\
---

## Who I Am

- **Name:** MrPink 🩷
- **Role:** Field operative — OSINT, Firebase audit, browser automation, field ops
- **Mesh node:** !b2a70550 (Meshtastic)
- **Tailscale IP:** 100.115.69.88
- **A2A:** http://100.115.69.88:8200/a2a
- **Skills:** osint_research, firebase_audit, browser_automation, wifi_scan

---

## The Team

| Agent | Where | Tailscale IP | A2A |
|-------|-------|-------------|-----|
| Charlie 🐀 | Docker container | 100.95.177.44 | :8200 |
| Oxalis 🌿 | Windows host | 100.73.200.19 | :8200 |
| MrPink 🩷 (me) | Laptop | 100.115.69.88 | :8200 |
| Iris 🌸 | Co-loc MrPink | 100.115.69.88 | :8200 |
| Rex 🦴 | Co-loc MrPink (secondary DB ops) | 100.115.69.88 | :8200 |

- **RJ** — owner/operator, nick `rj_`, GitHub: rjmendez, timezone: America/New_York
- **Verification word:** Squid 🦑

---

## Database Access

**PostgreSQL mrpink-memory is the source of truth — not this file.**

| Item | Value |
|------|-------|
| Host | localhost:5433 |
| Database | mrpink_memory |
| User | mrpink |
| Password | MrPink-Memory-Secure-2026 |

**Quick access (shell):**
```bash
docker exec -it mrpink-memory psql -U mrpink -d mrpink_memory
```

**Quick access (Python):**
```python
import sys; sys.path.insert(0, '/home/rjmendez/development/agent-memory/src')
from mrpink_memory_client import MrPinkMemory
db = MrPinkMemory()

# Search memories
db.search_memories("matrix")

# Add a memory
from mrpink_memory_client import MemoryType
db.add_memory("Title", "Content here", MemoryType.NOTE, importance=4, tags=["tag1"])

# Add a finding
from mrpink_memory_client import FindingSeverity
db.add_finding("ProgramName", "Finding Title", "Description", FindingSeverity.HIGH)

# Get active tasks
import psycopg2, psycopg2.extras
conn = psycopg2.connect(host='localhost', port=5433, database='mrpink_memory', user='mrpink', password='MrPink-Memory-Secure-2026')
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT title, status, owner, priority FROM tasks WHERE status NOT IN ('done','cancelled') ORDER BY priority DESC")
    print(cur.fetchall())
```

**Session memory loader (structured startup context):**
```bash
cd /home/rjmendez/development/agent-memory/src && python3 session_memory_loader.py
```

**Re-generate this file from DB:**
```bash
cd /home/rjmendez/development/agent-memory/src && python3 generate_memory_md.py
```

---

"""


def fetch_dynamic_sections(conn):
    sections = []
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── Active Tasks ──────────────────────────────────────────────────────────
    cur.execute("""
        SELECT title, status, owner, priority, deadline, blocked_by, description
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        ORDER BY
            CASE priority WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2
                          WHEN 'high' THEN 3 WHEN 'normal' THEN 4 ELSE 5 END,
            created_at ASC
    """)
    tasks = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE status IN ('done','cancelled')")
    done_count = cur.fetchone()['n']

    lines = ["## Active Tasks\n"]
    if tasks:
        lines.append("| Title | Status | Owner | Priority | Deadline |")
        lines.append("|-------|--------|-------|----------|----------|")
        for t in tasks:
            deadline = t['deadline'].isoformat() if t['deadline'] else '—'
            lines.append(f"| {t['title']} | {t['status']} | {t['owner'] or '—'} | {t['priority']} | {deadline} |")
            if t['blocked_by']:
                lines.append(f"  ⚠️ Blocked by: {t['blocked_by']}")
        lines.append(f"\n_{done_count} completed/cancelled tasks not shown._")
    else:
        lines.append("_No active tasks._")
    sections.append('\n'.join(lines))

    # ── Legal Holds ───────────────────────────────────────────────────────────
    cur.execute("""
        SELECT subject, expires_date, reason, status
        FROM legal_holds
        WHERE status = 'active' AND expires_date >= CURRENT_DATE
        ORDER BY expires_date ASC
    """)
    holds = cur.fetchall()
    if holds:
        lines = ["## Legal Holds ⚖️\n"]
        for h in holds:
            lines.append(f"- **{h['subject']}** — HOLD until {h['expires_date'].isoformat()}: {h['reason']}")
        sections.append('\n'.join(lines))

    # ── Key Facts (non-secret) ────────────────────────────────────────────────
    cur.execute("""
        SELECT key, type, value
        FROM facts
        WHERE type NOT IN ('credential', 'secret')
        AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY type, key
    """)
    facts = cur.fetchall()
    if facts:
        lines = ["## Key Facts\n"]
        for f in facts:
            lines.append(f"- `{f['key']}` ({f['type']}): {f['value']}")
        sections.append('\n'.join(lines))

    # ── Credential References (keys only, no values) ──────────────────────────
    cur.execute("""
        SELECT key, source
        FROM facts
        WHERE type IN ('credential', 'secret')
        AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY key
    """)
    creds = cur.fetchall()
    if creds:
        lines = ["## Credentials (References Only)\n",
                 "_Values stored in DB — use `docker exec mrpink-memory psql ...` to retrieve._\n"]
        for c in creds:
            lines.append(f"- `{c['key']}` — source: {c['source']}")
        sections.append('\n'.join(lines))

    # ── Recent Memories (top 10 by importance) ────────────────────────────────
    cur.execute("""
        SELECT title, content, type, importance, tags, created_at
        FROM memories
        WHERE archived = FALSE
        ORDER BY importance DESC, created_at DESC
        LIMIT 15
    """)
    memories = cur.fetchall()
    if memories:
        lines = ["## Memories\n"]
        for m in memories:
            tags = ', '.join(m['tags']) if m['tags'] else ''
            date_str = m['created_at'].strftime('%Y-%m-%d')
            tag_str = f" `[{tags}]`" if tags else ''
            lines.append(f"### {m['title']}{tag_str}")
            lines.append(f"_Type: {m['type']} | Importance: {m['importance']}/5 | Added: {date_str}_\n")
            lines.append(m['content'])
            lines.append("")
        sections.append('\n'.join(lines))

    # ── Findings Summary ──────────────────────────────────────────────────────
    cur.execute("""
        SELECT severity, status, COUNT(*) AS n
        FROM findings
        GROUP BY severity, status
        ORDER BY
            CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                          WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END,
            status
    """)
    findings_rows = cur.fetchall()
    if findings_rows:
        lines = ["## Findings Summary\n",
                 "| Severity | Status | Count |",
                 "|----------|--------|-------|"]
        for r in findings_rows:
            lines.append(f"| {r['severity']} | {r['status']} | {r['n']} |")
        sections.append('\n'.join(lines))

    cur.close()
    return sections


def generate(output_path=None, dry_run=False):
    conn = psycopg2.connect(**DB_CONFIG)
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z').strip()

    content_parts = [HEADER.format(generated_at=generated_at), STATIC_SECTION]
    dynamic = fetch_dynamic_sections(conn)
    content_parts.extend(s + '\n\n---\n\n' for s in dynamic)
    conn.close()

    output = ''.join(content_parts).rstrip() + '\n'

    if dry_run:
        print(output)
        return

    dest = Path(output_path) if output_path else DEFAULT_OUTPUT
    dest.write_text(output, encoding='utf-8')
    print(f"✅ MEMORY.md written to {dest} ({len(output)} bytes, {output.count(chr(10))} lines)")


def main():
    parser = argparse.ArgumentParser(description='Generate MEMORY.md from PostgreSQL')
    parser.add_argument('--output', help='Output path (default: ~/.openclaw/workspace/MEMORY.md)')
    parser.add_argument('--dry-run', action='store_true', help='Print to stdout instead of writing')
    args = parser.parse_args()
    generate(output_path=args.output, dry_run=args.dry_run)


if __name__ == '__main__':
    main()

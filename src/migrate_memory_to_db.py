#!/usr/bin/env python3
"""
Migrate MEMORY.md into PostgreSQL mrpink-memory database.
Parses structured memory and inserts into agent_status, tasks, facts, legal_holds, decisions.
"""

import psycopg2
import psycopg2.extras
from datetime import datetime, date
import json
import sys
from pathlib import Path

DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,
    'database': 'mrpink_memory',
    'user': 'mrpink',
    'password': 'MrPink-Memory-Secure-2026'
}

def connect():
    return psycopg2.connect(**DB_CONFIG)

def migrate_agent_status(conn):
    """Populate agent_status table from known agents."""
    
    agents = [
        ('MrPink', 'online', 'laptop', '100.115.69.88', 'anthropic/claude-haiku-4-5', 
         ['OSINT', 'Firebase audit', 'browser automation']),
        ('Charlie', 'online', 'k3s', '100.95.177.44', 'anthropic/claude-opus-4-1',
         ['Pipeline ops', 'findings DB', 'dispatch routing']),
        ('Oxalis', 'online', 'k3s', '100.73.200.19', 'anthropic/claude-opus-4-1',
         ['GPU inference', 'Docker ops', 'batch processing']),
        ('Iris', 'offline', 'stub', '100.115.69.88', None,
         ['Deep code review', 'PoC development']),
        ('Rex', 'offline', 'stub', '100.115.69.88', None,
         ['Secondary DB ops', 'batch processing']),
    ]
    
    with conn.cursor() as cur:
        for name, status, deployment, host, model, capabilities in agents:
            cur.execute("""
                INSERT INTO agent_status (agent_name, status, deployment, host, model, capabilities, last_heartbeat)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (agent_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    deployment = EXCLUDED.deployment,
                    host = EXCLUDED.host,
                    model = EXCLUDED.model,
                    capabilities = EXCLUDED.capabilities
            """, (name, status, deployment, host, model, capabilities, datetime.now() if status == 'online' else None))
    
    conn.commit()
    print(f"✅ Populated agent_status ({len(agents)} agents)")

def migrate_facts(conn):
    """Populate facts table from MEMORY.md key information."""
    
    facts_data = [
        # Team structure
        ('agent:charlie:token:a2a', '5d107a28e07ca079070e27327a8adaedd8d367124dfbbc669a843e335c01d474', 
         'credential', 0.95, 'MEMORY.md', None, 'mrpink', 'A2A token for Charlie'),
        ('agent:oxalis:token:a2a', 'oxalis-a2a-5d107a28e07ca079070e27327a8adaedd8d367124dfbbc669a843e335c01d474',
         'credential', 0.95, 'MEMORY.md', None, 'mrpink', 'A2A token for Oxalis'),
        ('agent:rex:token:a2a', 'rex-a2a-5d107a28e07ca079070e27327a8adaedd8d367124dfbbc669a843e335c01d474',
         'credential', 0.95, 'MEMORY.md', None, 'mrpink', 'A2A token for Rex'),
        ('agent:mrpink:token:inbound', '95dd2314249fc989ff29e9c2a69da28e7b6a2dc4268f596e9d9a142bed3e5568',
         'credential', 0.95, 'MEMORY.md', None, 'mrpink', 'A2A inbound token for MrPink'),
        
        # Firebase pipeline
        ('firebase:queue:pending', '6610', 'metric', 0.9, 'MEMORY.md', 
         (datetime.now().replace(hour=0, minute=0, second=0)).isoformat(), 'mrpink', 'Pending Firebase DBs'),
        ('firebase:scanned:total', '190', 'metric', 0.9, 'MEMORY.md',
         (datetime.now().replace(hour=0, minute=0, second=0)).isoformat(), 'mrpink', 'Firebase targets scanned'),
        ('firebase:findings:critical', '7', 'metric', 0.9, 'MEMORY.md',
         (datetime.now().replace(hour=0, minute=0, second=0)).isoformat(), 'mrpink', 'Critical findings'),
        
        # Legal holds
        ('legal:ual:expiry', '2026-04-28', 'date', 1.0, 'MEMORY.md', 
         datetime.strptime('2026-04-28', '%Y-%m-%d').date(), 'mrpink', 'UAL data legal hold expires'),
        
        # Verification
        ('security:verification:word', 'Squid 🦑', 'secret', 1.0, 'MEMORY.md', None, 'mrpink', 'RJ identity verification'),
        
        # Infrastructure
        ('infra:synapse:server_name', 'floppydicks.net', 'text', 1.0, 'MEMORY.md', None, 'mrpink', 'Matrix server name'),
        ('infra:tailscale:mrpink:ip', '100.115.69.88', 'ip', 1.0, 'MEMORY.md', None, 'mrpink', 'MrPink Tailscale IP'),
        ('infra:cloudflare:zone_id', '1b475d6bda176910525427704f96e05d', 'credential', 0.95, 'MEMORY.md', None, 'mrpink', 'Cloudflare zone ID'),
    ]
    
    with conn.cursor() as cur:
        for key, value, ftype, confidence, source, expires, owner, tags_str in facts_data:
            tags = [tag.strip() for tag in tags_str.split(',')]
            cur.execute("""
                INSERT INTO facts (key, value, type, confidence, source, expires_at, owner, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    confidence = EXCLUDED.confidence,
                    expires_at = EXCLUDED.expires_at
            """, (key, value, ftype, confidence, source, expires, owner, tags))
    
    conn.commit()
    print(f"✅ Populated facts ({len(facts_data)} facts)")

def migrate_legal_holds(conn):
    """Populate legal_holds table."""
    
    holds = [
        ('UAL bucket data', 'data_retention', 'RJ', date(2026, 3, 30), date(2026, 4, 28),
         'Legal hold issued - no scanning/access until 2026-04-28', 'active'),
    ]
    
    with conn.cursor() as cur:
        for subject, hold_type, issued_by, issued_date, expires_date, reason, status in holds:
            cur.execute("""
                INSERT INTO legal_holds (subject, hold_type, issued_by, issued_date, expires_date, reason, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (subject, hold_type, issued_by, issued_date, expires_date, reason, status))
    
    conn.commit()
    print(f"✅ Populated legal_holds ({len(holds)} holds)")

def migrate_tasks_from_memory(conn):
    """Populate tasks table with in-flight work from MEMORY.md."""
    
    tasks_data = [
        ('Paxos Phase 2 Analysis', 'Deep-dive PoC development for high-risk contracts',
         'running', 'Iris', 'MrPink', 'urgent', '2026-04-06', 
         'PaxosTokenClaimableRewards.sol exploit', None, 'bug_bounty,paxos'),
        
        ('Coinbase Tier 0 Research', 'Analyze Base, cbBTC, cbETH contracts',
         'pending', 'Iris', 'MrPink', 'urgent', '2026-04-06',
         'Waiting for RJ KYC approval', None, 'bug_bounty,coinbase'),
        
        ('WireGuard VPN Deployment', 'Deploy private mesh for agent-to-agent comms',
         'blocked', 'MrPink', 'RJ', 'high', '2026-04-06',
         'Terraform ready, awaiting RJ AWS secret key', None, 'infrastructure,mesh'),
        
        ('Charlie Matrix Connectivity', 'Verify Charlie reaches Oxalis Synapse via k3s',
         'done', 'Charlie', 'RJ', 'high', None,
         'Channel setup complete, agent online', '2026-04-05T02:00:00', 'matrix,comms'),
        
        ('Firebase Audit Queue Processing', 'Continuous scanning of 6,610 pending buckets',
         'running', 'Charlie', 'MrPink', 'normal', None,
         'Queue active, scanning at 8 workers', None, 'firebase,audit'),
        
        ('Obsidian Gateway Origin Fix', 'Update gateway config for null origin from Electron',
         'done', 'MrPink', 'RJ', 'normal', None,
         'Fixed: allowedOrigins=["null"]', '2026-04-04T23:55:00', 'infra,obsidian'),
    ]
    
    with conn.cursor() as cur:
        for title, desc, status, owner, dispatched_by, priority, deadline, notes, completed_at, tags_str in tasks_data:
            tags = [tag.strip() for tag in tags_str.split(',')]
            deadline_dt = datetime.fromisoformat(deadline) if deadline else None
            completed_dt = datetime.fromisoformat(completed_at) if completed_at else None
            
            cur.execute("""
                INSERT INTO tasks (title, description, status, owner, dispatched_by, priority, deadline, result_summary, completed_at, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (title, desc, status, owner, dispatched_by, priority, deadline_dt, notes, completed_dt, tags))
    
    conn.commit()
    print(f"✅ Populated tasks ({len(tasks_data)} tasks)")

def main():
    try:
        conn = connect()
        print("Connected to mrpink-memory database")
        
        # Clear test table
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_table")
        conn.commit()
        
        # Migrate data
        migrate_agent_status(conn)
        migrate_facts(conn)
        migrate_legal_holds(conn)
        migrate_tasks_from_memory(conn)
        
        # Summary
        with conn.cursor() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM agent_status) as agents, (SELECT COUNT(*) FROM facts) as facts, (SELECT COUNT(*) FROM tasks) as tasks, (SELECT COUNT(*) FROM legal_holds) as holds")
            result = cur.fetchone()
            print(f"\n📊 Migration complete:")
            print(f"   Agents: {result[0]}")
            print(f"   Facts: {result[1]}")
            print(f"   Tasks: {result[2]}")
            print(f"   Legal holds: {result[3]}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()

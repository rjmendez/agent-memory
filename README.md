# agent-memory

Distributed agent memory system for OpenClaw mesh agents. Unified interface for session-scoped, mesh-wide, and persistent knowledge storage.

## Components

- **mrpink_memory_client.py** — PostgreSQL memory database client (reads/writes facts, decisions, findings)
- **session_memory_loader.py** — Load targeted session context (~50 lines, efficient)
- **mesh_memory_sync.py** — RSA-signed memory sync via A2A Redis streams (cross-agent consensus)
- **migrate_memory_to_db.py** — One-time migration from markdown MEMORY.md → PostgreSQL

## Architecture

- **Backend:** PostgreSQL 15 (mrpink-memory container, :5433)
- **Mesh sync:** Redis streams + RSA-2048 signing
- **Features:** Full-text search, semantic relationships, audit logging, temporal analysis

## Usage

```python
from src.mrpink_memory_client import MrPinkMemory

db = MrPinkMemory()

# Store a fact
db.add_memory('finding', 'Paxos contract vulnerability in delegatecall', 'critical')

# Search
results = db.search_memories('paxos vulnerability')

# Query by type
findings = db.get_memories_by_type('finding')
```

## Setup

1. PostgreSQL 15+ running on localhost:5433
2. Database: `mrpink_memory`
3. User: `mrpink`
4. Password: Set in env var `MRPINK_MEMORY_PASSWORD`

## Future

- [ ] Agent memory skill (clawhub)
- [ ] Obsidian plugin for live sync
- [ ] Multi-agent consensus voting on facts
- [ ] Temporal analysis dashboard

#!/usr/bin/env python3
"""
mesh_inbox_daemon.py — Event-driven A2A inbox listener for MrPink.

Subscribes to Redis keyspace notifications on mesh:queue:mrpink.
Fires immediately when a message is pushed — no polling, no cron.

Replaces the heartbeat-cron approach for inbox processing.

Usage:
    python3 mesh_inbox_daemon.py [--once] [--dry-run]

Deployment:
    systemctl --user start mrpink-inbox-daemon
    (see mesh_inbox_daemon.service)

Architecture:
    Producer → LPUSH mesh:queue:<agent>
                     ↓ Redis keyspace event fires instantly
    Daemon   → wakes → LRANGE + processes → dispatches to handler
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Optional

import redis

# ── Config ─────────────────────────────────────────────────────────────────────

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
QUEUE_KEY = "mesh:queue:mrpink"
KEYSPACE_CHANNEL = f"__keyspace@{REDIS_DB}__:{QUEUE_KEY}"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    format="%(asctime)s [inbox-daemon] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=getattr(logging, LOG_LEVEL),
)
log = logging.getLogger(__name__)

# ── Skill handlers ─────────────────────────────────────────────────────────────

def handle_mcp_survey(msg: dict, dry_run: bool = False) -> None:
    """Someone sent us an MCP survey response — log it."""
    body = msg.get("input", {})
    log.info(f"MCP survey from {msg.get('from')}: {str(body)[:200]}")
    if not dry_run:
        _store_memory(
            title=f"MCP survey response from {msg.get('from', 'unknown')}",
            content=json.dumps(body),
            tags="mcp,survey,response"
        )


def handle_osint_alert(msg: dict, dry_run: bool = False) -> None:
    """Iris sent an OSINT alert — log and forward to Charlie for ingestion."""
    inp = msg.get("input", {})
    domain = inp.get("domain", "unknown")
    findings = inp.get("findings", 0)
    log.info(f"OSINT alert: {domain} — {findings} findings")
    if not dry_run:
        # Forward to Charlie via Redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
        r.lpush("mesh:queue:charlie", json.dumps({
            "from": "mrpink",
            "to": "charlie",
            "skill_id": "ingest_osint",
            "timestamp": _now_iso(),
            "input": inp,
        }))
        log.info(f"Forwarded {domain} OSINT to Charlie")


def handle_default(msg: dict, dry_run: bool = False) -> None:
    """Catch-all for unknown skill IDs — log and store."""
    skill = msg.get("skill_id", "unknown")
    log.info(f"Unhandled skill '{skill}' from {msg.get('from', '?')}: {str(msg)[:200]}")
    if not dry_run:
        _store_memory(
            title=f"Unhandled inbox message: {skill}",
            content=json.dumps(msg),
            tags=f"inbox,unhandled,{skill}"
        )


HANDLERS = {
    "mcp_survey": handle_mcp_survey,
    "osint_alert": handle_osint_alert,
}


# ── Core processing ────────────────────────────────────────────────────────────

def process_queue(r: redis.Redis, dry_run: bool = False) -> int:
    """Drain the queue, dispatch each message to its handler. Returns count processed."""
    processed = 0
    while True:
        raw = r.rpop(QUEUE_KEY)
        if raw is None:
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"Malformed message (not JSON): {raw[:100]}")
            continue

        skill = msg.get("skill_id", "unknown")
        handler = HANDLERS.get(skill, handle_default)
        log.info(f"Processing [{skill}] from {msg.get('from', '?')}")
        try:
            handler(msg, dry_run=dry_run)
        except Exception as e:
            log.error(f"Handler error for [{skill}]: {e}")
        processed += 1

    return processed


# ── Event loop ─────────────────────────────────────────────────────────────────

def run(once: bool = False, dry_run: bool = False) -> None:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

    # Ensure keyspace notifications are enabled
    current = r.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
    if "K" not in current or "l" not in current:
        log.info(f"Enabling keyspace notifications (was: '{current}')")
        r.config_set("notify-keyspace-events", "Kl")

    # Drain any messages already in queue on startup
    n = process_queue(r, dry_run=dry_run)
    if n:
        log.info(f"Startup drain: processed {n} queued messages")

    if once:
        return

    # Subscribe to keyspace events on our queue key
    ps = r.pubsub()
    ps.subscribe(KEYSPACE_CHANNEL)
    log.info(f"Listening on {KEYSPACE_CHANNEL} ...")

    reconnect_delay = 1
    while True:
        try:
            msg = ps.get_message(ignore_subscribe_messages=True, timeout=30.0)
            if msg is None:
                # Timeout — heartbeat, check queue anyway in case we missed an event
                depth = r.llen(QUEUE_KEY)
                if depth > 0:
                    log.debug(f"Heartbeat: {depth} items in queue")
                    process_queue(r, dry_run=dry_run)
                continue

            # Any operation on our queue key (lpush, rpush) → drain it
            op = msg.get("data", "")
            if op in ("lpush", "rpush", "linsert"):
                log.debug(f"Keyspace event: {op} on {QUEUE_KEY}")
                process_queue(r, dry_run=dry_run)

            reconnect_delay = 1  # reset on success

        except redis.exceptions.ConnectionError as e:
            log.warning(f"Redis connection lost: {e}. Reconnecting in {reconnect_delay}s...")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
            try:
                r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
                ps = r.pubsub()
                ps.subscribe(KEYSPACE_CHANNEL)
            except Exception:
                pass

        except KeyboardInterrupt:
            log.info("Shutting down.")
            ps.close()
            break


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _store_memory(title: str, content: str, tags: str = "") -> None:
    try:
        subprocess.run(
            ["mcporter", "call", "mrpink-memory.add_memory",
             "--args", json.dumps({
                 "title": title, "content": content,
                 "memory_type": "note", "importance": 3, "tags": tags
             })],
            capture_output=True, timeout=10
        )
    except Exception as e:
        log.warning(f"Memory store failed: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MrPink event-driven inbox daemon")
    parser.add_argument("--once", action="store_true", help="Drain queue once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Process but don't mutate state")
    args = parser.parse_args()
    run(once=args.once, dry_run=args.dry_run)

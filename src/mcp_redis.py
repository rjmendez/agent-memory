#!/usr/bin/env python3
"""
MrPink Redis/A2A MCP Server
Exposes MrPink-redis for A2A mesh communication and queue inspection.
No password on this Redis instance.
"""

import json
import redis
from datetime import datetime
from fastmcp import FastMCP

REDIS_CONFIG = {"host": "localhost", "port": 6379, "decode_responses": True}

mcp = FastMCP("mrpink-redis")


def get_redis():
    return redis.Redis(**REDIS_CONFIG)


# ── Inbox / Queue ──────────────────────────────────────────────────────────────

@mcp.tool()
def read_inbox(agent: str = "mrpink", count: int = 10, peek: bool = True) -> str:
    """
    Read messages from an agent's inbox queue (mesh:queue:<agent>).
    peek=True (default): non-destructive read. peek=False: pop messages off queue.
    Returns up to `count` messages as JSON.
    """
    r = get_redis()
    key = f"mesh:queue:{agent}"
    if peek:
        raw = r.lrange(key, 0, count - 1)
    else:
        raw = []
        for _ in range(count):
            msg = r.lpop(key)
            if msg is None:
                break
            raw.append(msg)

    messages = []
    for item in raw:
        try:
            messages.append(json.loads(item))
        except Exception:
            messages.append({"raw": item})
    return json.dumps({"key": key, "count": len(messages), "messages": messages}, indent=2)


@mcp.tool()
def send_message(to: str, skill_id: str, payload: str,
                 from_agent: str = "mrpink") -> str:
    """
    Send an A2A message to an agent's inbox (mesh:queue:<to>).
    payload must be a JSON string. skill_id is the target skill/action.
    """
    r = get_redis()
    key = f"mesh:queue:{to}"
    msg = {
        "from": from_agent,
        "to": to,
        "skill_id": skill_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "input": json.loads(payload),
    }
    r.rpush(key, json.dumps(msg))
    qlen = r.llen(key)
    return json.dumps({"sent": True, "key": key, "queue_depth": qlen})


@mcp.tool()
def list_queues() -> str:
    """
    List all known mesh queues and their current depths.
    Includes mesh:queue:*, mesh:inbox:*, and a2a:*:inbox keys.
    """
    r = get_redis()
    queue_patterns = ["mesh:queue:*", "mesh:inbox:*", "a2a:*:inbox"]
    result = {}
    for pattern in queue_patterns:
        for key in r.scan_iter(pattern):
            ktype = r.type(key)
            if ktype == "list":
                result[key] = r.llen(key)
            else:
                result[key] = f"type:{ktype}"
    return json.dumps(result, indent=2)


@mcp.tool()
def read_chat(channel: str = "general", count: int = 20) -> str:
    """
    Read recent messages from a mesh chat stream (mesh:chat:<channel>).
    Valid channels: general, ops, findings, ops:debug.
    Returns latest `count` messages in chronological order.
    """
    r = get_redis()
    key = f"mesh:chat:{channel}"
    ktype = r.type(key)
    if ktype == "stream":
        entries = r.xrevrange(key, count=count)
        messages = []
        for entry_id, fields in reversed(entries):
            messages.append({"id": entry_id, **fields})
        return json.dumps({"key": key, "count": len(messages), "messages": messages}, indent=2)
    elif ktype == "list":
        raw = r.lrange(key, -count, -1)
        messages = []
        for item in raw:
            try:
                messages.append(json.loads(item))
            except Exception:
                messages.append({"raw": item})
        return json.dumps({"key": key, "count": len(messages), "messages": messages}, indent=2)
    else:
        return json.dumps({"error": f"Unexpected key type '{ktype}' for {key}"})


@mcp.tool()
def get_key(key: str) -> str:
    """
    Get the value of a specific Redis key by name.
    Returns value, type, and TTL. Use list_queues() first to find key names.
    """
    r = get_redis()
    ktype = r.type(key)
    ttl = r.ttl(key)
    if ktype == "string":
        value = r.get(key)
    elif ktype == "list":
        value = r.lrange(key, 0, 9)
    elif ktype == "hash":
        value = r.hgetall(key)
    elif ktype == "set":
        value = list(r.smembers(key))
    elif ktype == "zset":
        value = r.zrange(key, 0, 9, withscores=True)
    else:
        value = f"<type:{ktype}>"
    return json.dumps({"key": key, "type": ktype, "ttl": ttl, "value": value}, indent=2)


# ── Validation ─────────────────────────────────────────────────────────────────

@mcp.tool()
def validate() -> str:
    """
    Self-test all Redis MCP tools against the live instance.
    Returns pass/fail for each tool.
    """
    results = {}

    # 1. list_queues
    try:
        data = json.loads(list_queues())
        assert isinstance(data, dict)
        results["list_queues"] = f"PASS ({len(data)} queues found)"
    except Exception as e:
        results["list_queues"] = f"FAIL: {e}"

    # 2. read_inbox (peek)
    try:
        data = json.loads(read_inbox("mrpink", count=5, peek=True))
        assert "messages" in data
        results["read_inbox"] = f"PASS ({data['count']} messages in mrpink inbox)"
    except Exception as e:
        results["read_inbox"] = f"FAIL: {e}"

    # 3. send_message + verify depth increases
    try:
        r = get_redis()
        before = r.llen("mesh:queue:mrpink")
        send_message("mrpink", "mcp_validate", '{"test": true}', from_agent="mrpink")
        after = r.llen("mesh:queue:mrpink")
        assert after == before + 1
        # Clean up sentinel
        r.lrem("mesh:queue:mrpink", 1, json.dumps({
            "from": "mrpink", "to": "mrpink", "skill_id": "mcp_validate",
        }, separators=(",", ":")))
        # Pop last item added (sentinel cleanup - best effort)
        r.rpop("mesh:queue:mrpink")
        results["send_message"] = "PASS (enqueue + depth verified)"
    except Exception as e:
        results["send_message"] = f"FAIL: {e}"

    # 4. read_chat
    try:
        data = json.loads(read_chat("general", count=5))
        assert "messages" in data
        results["read_chat"] = f"PASS ({data['count']} messages in general)"
    except Exception as e:
        results["read_chat"] = f"FAIL: {e}"

    # 5. get_key
    try:
        data = json.loads(get_key("mesh:chat:general"))
        assert "type" in data
        results["get_key"] = f"PASS (type={data['type']})"
    except Exception as e:
        results["get_key"] = f"FAIL: {e}"

    passed = sum(1 for v in results.values() if v.startswith("PASS"))
    total = len(results)
    return json.dumps({"summary": f"{passed}/{total} tools passing", "tools": results}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

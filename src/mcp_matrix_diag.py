#!/usr/bin/env python3
"""
MrPink Matrix Diagnostics MCP Server
Diagnostic tooling for the OpenClaw Matrix event routing pipeline.
Inspects sync state, device registration, room timelines, and inbound turn delivery.
NOT a general messaging tool — use the OpenClaw Matrix channel plugin for that.
"""

import json
import urllib.request
import urllib.parse
import urllib.error
import datetime
import os
from typing import Optional
from fastmcp import FastMCP

# Config — mirrors openclaw.json
HOMESERVER = "http://localhost:8008"
ACCESS_TOKEN = "syt_bXJwaW5r_qsVhuwIHBqBYuOXnjEst_3W1YXl"
USER_ID = "@mrpink:mrpink.floppydicks.net"
BOT_STORAGE_PATH = os.path.expanduser(
    "~/.openclaw/matrix/accounts/default/"
    "localhost_8008__mrpink_mrpink.floppydicks.net/0e5dbd8365a5323b/bot-storage.json"
)
STORAGE_META_PATH = os.path.expanduser(
    "~/.openclaw/matrix/accounts/default/"
    "localhost_8008__mrpink_mrpink.floppydicks.net/0e5dbd8365a5323b/storage-meta.json"
)

mcp = FastMCP("mrpink-matrix-diag")


def matrix_get(path: str, params: dict = None) -> dict:
    url = f"{HOMESERVER}/_matrix/client/v3/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {ACCESS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


def matrix_put(path: str, body: dict) -> dict:
    url = f"{HOMESERVER}/_matrix/client/v3/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {ACCESS_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


def fmt_ts(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


# ── Sync State ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_sync_state() -> str:
    """
    Compare OpenClaw's saved sync token against current Synapse state.
    Detects: stale tokens, missed events, rooms with pending activity.
    Key diagnostic for 'zero inbound turns' after gateway restart.
    """
    result = {}

    # Load saved state
    try:
        with open(BOT_STORAGE_PATH) as f:
            saved = json.load(f)
        saved_token = saved.get("savedSync", {}).get("nextBatch")
        result["saved_token"] = saved_token
    except Exception as e:
        result["saved_token"] = None
        result["saved_token_error"] = str(e)
        saved_token = None

    # Sync from saved token
    if saved_token:
        since_sync = matrix_get("sync", {"timeout": "0", "since": saved_token})
        new_token = since_sync.get("next_batch")
        result["token_is_current"] = new_token == saved_token or since_sync.get("next_batch", "").split("_")[1] == saved_token.split("_")[1]

        joined = since_sync.get("rooms", {}).get("join", {})
        pending = {rid: len(rdata.get("timeline", {}).get("events", []))
                   for rid, rdata in joined.items()
                   if rdata.get("timeline", {}).get("events")}
        result["events_pending_since_saved_token"] = pending
        result["current_token"] = new_token
        result["token_gap"] = f"saved={saved_token} → current={new_token}"
    else:
        result["events_pending_since_saved_token"] = "unknown (no saved token)"

    # Fresh sync (no token) to get full current state
    fresh = matrix_get("sync", {"timeout": "0"})
    fresh_token = fresh.get("next_batch")
    result["fresh_sync_token"] = fresh_token
    joined_fresh = fresh.get("rooms", {}).get("join", {})
    result["joined_room_count"] = len(joined_fresh)

    # Room activity summary from fresh sync
    room_summary = {}
    for rid, rdata in joined_fresh.items():
        events = rdata.get("timeline", {}).get("events", [])
        latest = None
        if events:
            last = events[-1]
            latest = {
                "ts": fmt_ts(last.get("origin_server_ts", 0)),
                "sender": last.get("sender"),
                "type": last.get("type"),
                "body": last.get("content", {}).get("body", "")[:80],
            }
        room_summary[rid] = {"event_count": len(events), "latest": latest}
    result["room_summary"] = room_summary

    return json.dumps(result, indent=2)


# ── Device State ───────────────────────────────────────────────────────────────

@mcp.tool()
def check_device_state() -> str:
    """
    Check registered Matrix devices for mrpink and compare against
    OpenClaw's persisted device ID. Detects ghost devices from restarts
    where deviceId was not persisted (null in storage-meta.json).
    """
    result = {}

    # Load storage meta
    try:
        with open(STORAGE_META_PATH) as f:
            meta = json.load(f)
        result["persisted_device_id"] = meta.get("deviceId")
        result["created_at"] = meta.get("createdAt")
        result["homeserver"] = meta.get("homeserver")
    except Exception as e:
        result["storage_meta_error"] = str(e)

    # Whoami (active device)
    whoami = matrix_get("account/whoami")
    result["active_device_id"] = whoami.get("device_id")
    result["user_id"] = whoami.get("user_id")

    # All registered devices
    devices = matrix_get("devices")
    device_list = devices.get("devices", [])
    result["registered_devices"] = [
        {
            "device_id": d.get("device_id"),
            "display_name": d.get("display_name"),
            "last_seen_ts": fmt_ts(d["last_seen_ts"]) if d.get("last_seen_ts") else None,
            "last_seen_ip": d.get("last_seen_ip"),
        }
        for d in device_list
    ]
    result["device_count"] = len(device_list)

    # Diagnosis
    persisted = result.get("persisted_device_id")
    active = result.get("active_device_id")
    if persisted is None:
        result["diagnosis"] = "WARNING: deviceId is null in storage-meta — OpenClaw is not persisting device ID. Each restart may register a new device, causing sync token mismatches."
    elif persisted != active:
        result["diagnosis"] = f"WARNING: persisted device ({persisted}) differs from active device ({active}). Sync state may be for wrong device."
    else:
        result["diagnosis"] = f"OK: device ID consistent ({active})"

    return json.dumps(result, indent=2)


# ── Room Timeline ──────────────────────────────────────────────────────────────

@mcp.tool()
def get_room_timeline(room_id: str, limit: int = 20, since_token: Optional[str] = None) -> str:
    """
    Fetch recent timeline events for a room directly from Synapse.
    room_id: Matrix room ID (e.g. '!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net')
    since_token: optional sync token to fetch events from (uses messages API)
    Useful for verifying what Synapse has vs what OpenClaw received.
    """
    encoded = urllib.parse.quote(room_id)

    params = {"limit": str(limit), "dir": "b"}
    if since_token:
        params["from"] = since_token

    data = matrix_get(f"rooms/{encoded}/messages", params)
    if "error" in data:
        return json.dumps(data)

    events = []
    for e in reversed(data.get("chunk", [])):
        events.append({
            "event_id": e.get("event_id"),
            "ts": fmt_ts(e.get("origin_server_ts", 0)),
            "sender": e.get("sender"),
            "type": e.get("type"),
            "body": e.get("content", {}).get("body", e.get("content", {}).get("msgtype", ""))[:120],
        })

    return json.dumps({
        "room_id": room_id,
        "event_count": len(events),
        "start": data.get("start"),
        "end": data.get("end"),
        "events": events,
    }, indent=2)


# ── Inbound Turn Test ──────────────────────────────────────────────────────────

@mcp.tool()
def test_inbound_pipeline(room_id: str) -> str:
    """
    End-to-end inbound turn test: sends a sentinel message to the room,
    then immediately polls sync to verify the event appears in the pipeline.
    Confirms whether Synapse → OpenClaw sync delivery is working.
    room_id: Matrix room ID to test against.
    """
    result = {}
    encoded = urllib.parse.quote(room_id)

    # Get current sync token before sending
    pre_sync = matrix_get("sync", {"timeout": "0"})
    pre_token = pre_sync.get("next_batch")
    result["pre_send_token"] = pre_token

    # Send sentinel
    import time
    txn_id = f"mcp_diag_{int(time.time())}"
    send_result = matrix_put(
        f"rooms/{encoded}/send/m.room.message/{txn_id}",
        {"msgtype": "m.text", "body": f"[MCP diagnostic sentinel — {txn_id}]"}
    )
    result["send_result"] = send_result
    sentinel_event_id = send_result.get("event_id")

    if "error" in send_result:
        result["diagnosis"] = "FAIL: could not send sentinel message"
        return json.dumps(result, indent=2)

    # Poll sync from pre-send token — should see our event
    time.sleep(0.5)
    post_sync = matrix_get("sync", {"timeout": "2000", "since": pre_token})
    post_token = post_sync.get("next_batch")
    result["post_send_token"] = post_token

    joined = post_sync.get("rooms", {}).get("join", {})
    room_data = joined.get(room_id, {})
    tl_events = room_data.get("timeline", {}).get("events", [])
    event_ids = [e.get("event_id") for e in tl_events]

    result["events_in_sync_response"] = len(tl_events)
    result["sentinel_in_sync"] = sentinel_event_id in event_ids

    if result["sentinel_in_sync"]:
        result["diagnosis"] = "OK: event sent and immediately visible in sync response. Pipeline is healthy up to Synapse delivery."
    else:
        result["diagnosis"] = (
            "WARNING: sentinel sent successfully but NOT seen in sync poll. "
            "Event routing between Synapse and OpenClaw may be broken. "
            f"Expected event_id: {sentinel_event_id}. Got: {event_ids}"
        )

    return json.dumps(result, indent=2)


# ── Room List ──────────────────────────────────────────────────────────────────

@mcp.tool()
def list_joined_rooms() -> str:
    """
    List all rooms mrpink is joined to with names, member counts,
    and latest message timestamp. Cross-references against openclaw.json allowlist.
    """
    allowlist = [
        "!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net",
        "!WDBmFuwXaHNHAQsHkn:mrpink.floppydicks.net",
        "!uhetEhEsQFuSDUrDjQ:mrpink.floppydicks.net",
        "!CyEhClVOGkRUpptEbR:mrpink.floppydicks.net",
        "!GufWcKYPGuizdsEpwO:mrpink.floppydicks.net",
    ]

    joined = matrix_get("joined_rooms")
    room_ids = joined.get("joined_rooms", [])
    rooms = []

    for rid in room_ids:
        encoded = urllib.parse.quote(rid)
        name_resp = matrix_get(f"rooms/{encoded}/state/m.room.name/")
        name = name_resp.get("name", "(no name)")
        members_resp = matrix_get(f"joined_rooms/{encoded}/members") if False else matrix_get(f"rooms/{encoded}/joined_members")
        member_count = len(members_resp.get("joined", {}))

        rooms.append({
            "room_id": rid,
            "name": name,
            "member_count": member_count,
            "in_allowlist": rid in allowlist,
        })

    not_in_allowlist = [r for r in rooms if not r["in_allowlist"]]
    result = {
        "room_count": len(rooms),
        "allowlist_gaps": [r["room_id"] for r in not_in_allowlist],
        "rooms": rooms,
    }
    return json.dumps(result, indent=2)


# ── Validation ─────────────────────────────────────────────────────────────────

@mcp.tool()
def validate() -> str:
    """
    Self-test all Matrix diagnostic tools. Non-destructive except test_inbound_pipeline
    which sends a sentinel message — skipped here, call it manually when needed.
    """
    results = {}

    # 1. get_sync_state
    try:
        data = json.loads(get_sync_state())
        assert "saved_token" in data
        assert "joined_room_count" in data
        results["get_sync_state"] = f"PASS (joined={data['joined_room_count']}, token={data.get('saved_token','none')})"
    except Exception as e:
        results["get_sync_state"] = f"FAIL: {e}"

    # 2. check_device_state
    try:
        data = json.loads(check_device_state())
        assert "device_count" in data
        diag = data.get("diagnosis", "")
        results["check_device_state"] = f"PASS ({data['device_count']} devices) — {diag}"
    except Exception as e:
        results["check_device_state"] = f"FAIL: {e}"

    # 3. get_room_timeline
    try:
        ops_room = "!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net"
        data = json.loads(get_room_timeline(ops_room, limit=5))
        assert "events" in data
        results["get_room_timeline"] = f"PASS ({data['event_count']} events from #ops)"
    except Exception as e:
        results["get_room_timeline"] = f"FAIL: {e}"

    # 4. list_joined_rooms
    try:
        data = json.loads(list_joined_rooms())
        assert "room_count" in data
        gaps = data.get("allowlist_gaps", [])
        results["list_joined_rooms"] = f"PASS ({data['room_count']} rooms, {len(gaps)} not in allowlist)"
    except Exception as e:
        results["list_joined_rooms"] = f"FAIL: {e}"

    # 5. test_inbound_pipeline — skipped (sends a message)
    results["test_inbound_pipeline"] = "SKIP (sends sentinel message — call manually)"

    passed = sum(1 for v in results.values() if v.startswith("PASS"))
    skipped = sum(1 for v in results.values() if v.startswith("SKIP"))
    total = len(results)
    return json.dumps({
        "summary": f"{passed}/{total - skipped} tools passing ({skipped} skipped)",
        "tools": results
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

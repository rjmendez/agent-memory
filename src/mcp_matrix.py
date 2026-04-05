#!/usr/bin/env python3
"""
MrPink Matrix MCP Server
Sends and reads Matrix messages via the OpenClaw CLI plugin.
Uses `openclaw message send --channel matrix` — no token management needed.
The OpenClaw process owns the authenticated Matrix client session.

Rooms (mrpink.floppydicks.net):
  #ops        !eKfMkpexDtvssFqmsp:mrpink.floppydicks.net
  #general    !WDBmFuwXaHNHAQsHkn:mrpink.floppydicks.net
  #findings   !uhetEhEsQFuSDUrDjQ:mrpink.floppydicks.net
  #voting     !CyEhClVOGkRUpptEbR:mrpink.floppydicks.net
  #disclosures-internal  !GufWcKYPGuizdsEpwO:mrpink.floppydicks.net
"""

import json
import subprocess
from typing import Optional
from fastmcp import FastMCP

mcp = FastMCP("mrpink-matrix")

# Known rooms — accept shorthand or full ID
ROOMS = {
    "ops":                   "!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net",
    "#ops":                  "!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net",
    "general":               "!WDBmFuwXaHNHAQsHkn:mrpink.floppydicks.net",
    "#general":              "!WDBmFuwXaHNHAQsHkn:mrpink.floppydicks.net",
    "findings":              "!uhetEhEsQFuSDUrDjQ:mrpink.floppydicks.net",
    "#findings":             "!uhetEhEsQFuSDUrDjQ:mrpink.floppydicks.net",
    "voting":                "!CyEhClVOGkRUpptEbR:mrpink.floppydicks.net",
    "#voting":               "!CyEhClVOGkRUpptEbR:mrpink.floppydicks.net",
    "disclosures-internal":  "!GufWcKYPGuizdsEpwO:mrpink.floppydicks.net",
    "#disclosures-internal": "!GufWcKYPGuizdsEpwO:mrpink.floppydicks.net",
}

def _resolve_room(room: str) -> str:
    """Resolve shorthand room name or pass through full room ID."""
    return ROOMS.get(room, room)


def _run_openclaw(args: list[str], timeout: int = 30) -> dict:
    """Run openclaw CLI and return parsed JSON output."""
    try:
        r = subprocess.run(
            ["openclaw"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        # Strip plugin warning lines before parsing JSON
        lines = [l for l in stdout.splitlines() if not l.startswith("[plugins]") and not l.startswith("[Matrix")]
        clean = "\n".join(lines).strip()
        if clean:
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                return {"ok": r.returncode == 0, "raw": clean, "stderr": stderr}
        return {"ok": r.returncode == 0, "stderr": stderr}
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


# ── Send ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def send_message(room: str, message: str, reply_to: Optional[str] = None) -> str:
    """
    Send a message to a Matrix room via the OpenClaw plugin.
    room: shorthand (#ops, #general, #findings, #voting, #disclosures-internal)
          or full room ID (!roomid:server).
    reply_to: optional event ID to thread-reply to.
    Returns message ID and room ID on success.
    """
    target = _resolve_room(room)
    args = ["message", "send", "--channel", "matrix", "-t", target, "-m", message, "--json"]
    if reply_to:
        args += ["--reply-to", reply_to]
    result = _run_openclaw(args)
    return json.dumps(result, indent=2)


@mcp.tool()
def send_to_agent(agent_matrix_id: str, message: str) -> str:
    """
    Send a direct message to an agent by Matrix user ID.
    Examples: @charlie:oxalis.floppydicks.net, @oxalis:oxalis.floppydicks.net
    Returns message ID on success.
    """
    args = ["message", "send", "--channel", "matrix", "-t", agent_matrix_id, "-m", message, "--json"]
    result = _run_openclaw(args)
    return json.dumps(result, indent=2)


@mcp.tool()
def broadcast(message: str, rooms: Optional[list[str]] = None) -> str:
    """
    Send a message to multiple rooms at once.
    rooms: list of room shorthands or IDs. Defaults to [#ops, #general].
    Returns per-room results.
    """
    targets = rooms or ["ops", "general"]
    results = {}
    for room in targets:
        target = _resolve_room(room)
        args = ["message", "send", "--channel", "matrix", "-t", target, "-m", message, "--json"]
        results[room] = _run_openclaw(args)
    return json.dumps(results, indent=2)


# ── Read ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def read_room(room: str, limit: int = 20) -> str:
    """
    Read recent messages from a Matrix room.
    room: shorthand (#ops, etc.) or full room ID.
    limit: number of messages to return (default 20, max 100).
    Returns messages with sender, timestamp, and body.
    """
    target = _resolve_room(room)
    limit = min(limit, 100)
    args = ["message", "read", "--channel", "matrix", "-t", target, "--json",
            "--limit", str(limit)]
    result = _run_openclaw(args, timeout=15)
    return json.dumps(result, indent=2)


# ── React ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def react(room: str, event_id: str, emoji: str) -> str:
    """
    React to a Matrix message with an emoji.
    room: shorthand or full room ID.
    event_id: the $event_id of the message to react to.
    emoji: single emoji character or shortcode.
    """
    target = _resolve_room(room)
    args = ["message", "react", "--channel", "matrix",
            "-t", target, "--message-id", event_id, "--emoji", emoji, "--json"]
    result = _run_openclaw(args)
    return json.dumps(result, indent=2)


# ── Rooms ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_rooms() -> str:
    """
    List all known Matrix rooms with their IDs and shorthand names.
    Returns the static room registry plus shorthand mapping.
    """
    rooms = {
        "#ops": "!eKfMkpexDtvssFqmsp:mrpink.floppydicks.net",
        "#general": "!WDBmFuwXaHNHAQsHkn:mrpink.floppydicks.net",
        "#findings": "!uhetEhEsQFuSDUrDjQ:mrpink.floppydicks.net",
        "#voting": "!CyEhClVOGkRUpptEbR:mrpink.floppydicks.net",
        "#disclosures-internal": "!GufWcKYPGuizdsEpwO:mrpink.floppydicks.net",
    }
    agents = {
        "@charlie": "@charlie:oxalis.floppydicks.net",
        "@oxalis": "@oxalis:oxalis.floppydicks.net",
        "@mrpink": "@mrpink:mrpink.floppydicks.net",
        "@rjmendez": "@rjmendez:botnet.floppydicks.net",
    }
    return json.dumps({"rooms": rooms, "known_agents": agents}, indent=2)


@mcp.tool()
def validate() -> str:
    """
    Validate the MCP server by sending a dry-run message to #ops.
    Returns ok=True if the OpenClaw matrix plugin is reachable.
    """
    target = _resolve_room("ops")
    args = ["message", "send", "--channel", "matrix",
            "-t", target, "-m", "[mrpink-matrix MCP validate]", "--dry-run", "--json"]
    result = _run_openclaw(args, timeout=35)
    healthy = result.get("dryRun") is True or result.get("ok") is True
    return json.dumps({"healthy": healthy, "result": result}, indent=2)


if __name__ == "__main__":
    mcp.run()

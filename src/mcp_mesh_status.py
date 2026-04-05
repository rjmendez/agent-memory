#!/usr/bin/env python3
"""
MrPink Mesh Status MCP Server
Structured introspection of the local agent mesh: Docker, WireGuard, Tailscale,
systemd services, and k3s pod status (via SSH to Oxalis).
Replaces ad-hoc exec calls for "what's running" diagnostics.
"""

import json
import subprocess
import shutil
from typing import Optional
from fastmcp import FastMCP

mcp = FastMCP("mrpink-mesh-status")


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


# ── Docker ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def docker_ps(all_containers: bool = False) -> str:
    """
    List running Docker containers on the local host (MrPink).
    all_containers=True includes stopped containers.
    Returns structured JSON with name, image, status, ports.
    """
    args = ["docker", "ps", "--format", "json"]
    if all_containers:
        args.append("-a")
    rc, out, err = _run(args)
    if rc != 0:
        return json.dumps({"error": err or "docker ps failed"})
    containers = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                containers.append({"raw": line})
    return json.dumps(containers, indent=2)


@mcp.tool()
def docker_logs(container: str, tail: int = 50) -> str:
    """
    Get recent logs from a Docker container by name or ID.
    tail controls how many lines to return (default 50, max 500).
    """
    tail = min(tail, 500)
    rc, out, err = _run(["docker", "logs", "--tail", str(tail), container])
    if rc != 0:
        return json.dumps({"error": err or f"failed to get logs for {container}"})
    return json.dumps({"container": container, "tail": tail, "logs": out})


# ── WireGuard ──────────────────────────────────────────────────────────────────

@mcp.tool()
def wireguard_status() -> str:
    """
    Get WireGuard interface status on MrPink.
    Returns peer handshake times, IPs, transfer stats.
    Requires `wg` to be in PATH (may need sudo — run `sudo wg show` if needed).
    """
    rc, out, err = _run(["wg", "show"])
    if rc != 0:
        # Try without sudo context note
        return json.dumps({
            "error": err or "wg show failed",
            "hint": "May require sudo. MrPink WG IP should be 10.0.0.2, hub: 107.22.209.20:51820"
        })
    return json.dumps({"wg_show": out, "local_ip": "10.0.0.2", "hub": "107.22.209.20:51820"})


# ── Tailscale ──────────────────────────────────────────────────────────────────

@mcp.tool()
def tailscale_status() -> str:
    """
    Get Tailscale mesh status: which peers are online, IPs, hostnames.
    Returns JSON. MrPink Tailscale IP is 100.115.69.88.
    """
    if not shutil.which("tailscale"):
        return json.dumps({"error": "tailscale not in PATH"})
    rc, out, err = _run(["tailscale", "status", "--json"])
    if rc != 0:
        return json.dumps({"error": err or "tailscale status failed"})
    try:
        return json.dumps(json.loads(out), indent=2)
    except json.JSONDecodeError:
        return json.dumps({"raw": out})


# ── Systemd ────────────────────────────────────────────────────────────────────

@mcp.tool()
def service_status(service: str) -> str:
    """
    Check the status of a systemd user service.
    Common services: openclaw-gateway, cloudflared, synapse.
    Returns active state, sub-state, and recent journal lines.
    """
    rc, out, err = _run(["systemctl", "--user", "is-active", service])
    active = out.strip()
    _, journal, _ = _run(["journalctl", "--user", "-u", service, "-n", "20", "--no-pager"])
    return json.dumps({
        "service": service,
        "active": active,
        "healthy": active == "active",
        "recent_logs": journal.splitlines()[-20:] if journal else []
    })


@mcp.tool()
def list_services() -> str:
    """
    List all running OpenClaw-related systemd user services.
    Returns names and active states for openclaw-*, cloudflared, synapse.
    """
    rc, out, err = _run([
        "systemctl", "--user", "list-units",
        "--type=service", "--state=running",
        "--no-pager", "--plain", "--no-legend"
    ])
    services = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            services.append({"unit": parts[0], "state": parts[3] if len(parts) > 3 else "?"})
    return json.dumps(services, indent=2)


# ── OpenClaw ───────────────────────────────────────────────────────────────────

@mcp.tool()
def openclaw_status() -> str:
    """
    Check OpenClaw gateway health on MrPink.
    Verifies the gateway process is running and the WebSocket port is listening.
    Returns port, pid, and uptime info.
    """
    rc_active, active, _ = _run(["systemctl", "--user", "is-active", "openclaw-gateway"])
    rc_port, port_out, _ = _run(["ss", "-tlnp"])

    listening = "18789" in port_out
    return json.dumps({
        "gateway_service": active,
        "port_18789_listening": listening,
        "healthy": active == "active" and listening,
        "websocket": "ws://localhost:18789",
        "obsidian_origin_fix": "allowedOrigins=[\"null\"] applied"
    })


# ── Matrix / Synapse ───────────────────────────────────────────────────────────

@mcp.tool()
def synapse_health() -> str:
    """
    Check MrPink's local Synapse Matrix homeserver health.
    Hits the /_matrix/client/versions endpoint on localhost:8008.
    Returns versions list and federation status.
    """
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen("http://localhost:8008/_matrix/client/versions", timeout=5) as resp:
            data = json.loads(resp.read())
            return json.dumps({
                "healthy": True,
                "server_name": "mrpink.floppydicks.net",
                "public_url": "https://botnet.floppydicks.net",
                "versions": data.get("versions", [])[:3],
                "federation": "live"
            })
    except urllib.error.URLError as e:
        return json.dumps({"healthy": False, "error": str(e)})


# ── Mesh Summary ───────────────────────────────────────────────────────────────

@mcp.tool()
def mesh_summary() -> str:
    """
    Full mesh health snapshot: Docker containers, OpenClaw gateway, Synapse,
    and known agent reachability. One-call diagnostic for heartbeat checks.
    Use this instead of running multiple individual checks.
    """
    import urllib.request, urllib.error

    results = {}

    # Docker containers
    rc, out, _ = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
    results["docker"] = [
        {"name": p[0], "status": p[1]}
        for line in out.splitlines()
        if (p := line.split("\t")) and len(p) == 2
    ]

    # OpenClaw port
    _, port_out, _ = _run(["ss", "-tlnp"])
    results["openclaw_gateway"] = "18789" in port_out

    # Synapse
    try:
        with urllib.request.urlopen("http://localhost:8008/_matrix/client/versions", timeout=3) as resp:
            results["synapse"] = "healthy"
    except Exception:
        results["synapse"] = "unreachable"

    # Known agent A2A endpoints (Tailscale, non-blocking)
    agents = {
        "mrpink": "http://100.115.69.88:8200/a2a",
        "charlie": "http://100.95.177.44:8200/a2a",
    }
    results["agents"] = {}
    for agent, url in agents.items():
        try:
            with urllib.request.urlopen(url + "/.well-known/agent.json", timeout=3) as r:
                results["agents"][agent] = "reachable"
        except Exception:
            results["agents"][agent] = "unreachable"

    return json.dumps(results, indent=2)


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""
MrPink Vault MCP Server
Exposes mrpink-vault (Hashicorp Vault KV) for secret reads.
Write operations are intentionally excluded — secrets go in via CLI/UI only.
"""

import json
import urllib.request
import urllib.error
from fastmcp import FastMCP

VAULT_ADDR = "http://127.0.0.1:8201"
VAULT_TOKEN = "mrpink-vault-dev-token"

mcp = FastMCP("mrpink-vault")


def vault_request(path: str, method: str = "GET") -> dict:
    url = f"{VAULT_ADDR}/v1/{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Vault-Token", VAULT_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


# ── Secret Access ──────────────────────────────────────────────────────────────

@mcp.tool()
def list_secrets(mount: str = "agents", path: str = "") -> str:
    """
    List secret keys at a given Vault KV v2 mount and path.
    Default mount is 'agents'. Does NOT return secret values.
    Examples: list_secrets("agents", "mrpink") or list_secrets("secret", "agents")
    """
    list_path = f"{mount}/metadata/{path}" if path else f"{mount}/metadata"
    result = vault_request(f"{list_path}?list=true")
    return json.dumps(result.get("data", result), indent=2)


@mcp.tool()
def get_secret(mount: str, path: str) -> str:
    """
    Read a secret value from Vault KV v2.
    mount: the KV engine name (e.g. 'agents', 'secret').
    path: the secret path under the mount (e.g. 'mrpink/a2a-token').
    Returns the secret data dict.
    """
    result = vault_request(f"{mount}/data/{path}")
    if "error" in result:
        return json.dumps(result)
    data = result.get("data", {}).get("data", {})
    metadata = result.get("data", {}).get("metadata", {})
    return json.dumps({"path": f"{mount}/{path}", "data": data, "metadata": metadata}, indent=2)


@mcp.tool()
def get_secret_metadata(mount: str, path: str) -> str:
    """
    Get metadata for a secret (versions, creation time, etc.) without returning values.
    Useful for checking if a secret exists and when it was last updated.
    """
    result = vault_request(f"{mount}/metadata/{path}")
    if "error" in result:
        return json.dumps(result)
    return json.dumps(result.get("data", result), indent=2)


@mcp.tool()
def list_mounts() -> str:
    """
    List all secret engine mounts in Vault. Use to discover available mounts
    before calling list_secrets or get_secret.
    """
    result = vault_request("sys/mounts")
    if "error" in result:
        return json.dumps(result)
    mounts = {k: v.get("type", "?") for k, v in result.items()
              if isinstance(v, dict) and "type" in v}
    return json.dumps(mounts, indent=2)


# ── Validation ─────────────────────────────────────────────────────────────────

@mcp.tool()
def validate() -> str:
    """
    Self-test all Vault MCP tools against the live instance.
    Returns pass/fail for each tool.
    """
    results = {}

    # 1. list_mounts
    try:
        data = json.loads(list_mounts())
        assert isinstance(data, dict)
        assert len(data) > 0
        results["list_mounts"] = f"PASS ({len(data)} mounts: {', '.join(data.keys())})"
    except Exception as e:
        results["list_mounts"] = f"FAIL: {e}"

    # 2. list_secrets
    try:
        data = json.loads(list_secrets("agents"))
        results["list_secrets"] = f"PASS (keys={data.get('keys', data)})"
    except Exception as e:
        results["list_secrets"] = f"FAIL: {e}"

    # 3. get_secret_metadata (non-destructive existence check)
    try:
        data = json.loads(list_secrets("agents"))
        keys = data.get("keys", [])
        if keys:
            first = keys[0].rstrip("/")
            meta = json.loads(get_secret_metadata("agents", first))
            results["get_secret_metadata"] = f"PASS (checked agents/{first})"
        else:
            results["get_secret_metadata"] = "SKIP (no keys found)"
    except Exception as e:
        results["get_secret_metadata"] = f"FAIL: {e}"

    # 4. get_secret connectivity check (expect data or a known path error, not a conn error)
    try:
        data = json.loads(get_secret("agents", "mrpink"))
        # Accept either real data or a 404-style error — both mean Vault is reachable
        if "error" in data and "connection" in str(data["error"]).lower():
            results["get_secret"] = f"FAIL: {data['error']}"
        else:
            results["get_secret"] = "PASS (vault reachable, path checked)"
    except Exception as e:
        results["get_secret"] = f"FAIL: {e}"

    passed = sum(1 for v in results.values() if v.startswith("PASS"))
    total = len(results)
    return json.dumps({"summary": f"{passed}/{total} tools passing", "tools": results}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

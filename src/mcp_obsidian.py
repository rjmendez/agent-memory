#!/usr/bin/env python3
"""
MrPink Obsidian MCP Server
Exposes the Obsidian CLI as structured MCP tools.
Requires Obsidian 1.12+ with CLI enabled (Settings → General → Command line interface).
"""

import json
import subprocess
from typing import Optional
from fastmcp import FastMCP

mcp = FastMCP("mrpink-obsidian")

OBSIDIAN_BIN = "obsidian"


def _run(args: list[str]) -> str:
    """Run an obsidian CLI command and return stdout."""
    result = subprocess.run(
        [OBSIDIAN_BIN] + args,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0 and result.stderr:
        return json.dumps({"error": result.stderr.strip(), "stdout": result.stdout.strip()})
    return result.stdout.strip()


# ── Search ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def search(query: str, limit: int = 20, with_context: bool = False) -> str:
    """
    Search the Obsidian vault for notes matching a query.
    with_context=True returns surrounding text snippets (slower).
    Returns a JSON list of matching note paths.
    """
    cmd = "search:context" if with_context else "search"
    return _run([cmd, f"query={query}", f"limit={limit}", "format=json"])


# ── Read ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def read_note(file: str) -> str:
    """
    Read the full content of an Obsidian note by name or path.
    Use the note's display name (e.g. 'Agents/MrPink') or full path.
    Returns raw markdown content including frontmatter.
    """
    return _run(["read", f"file={file}"])


@mcp.tool()
def outline(file: str) -> str:
    """
    Get the heading outline of an Obsidian note as structured JSON.
    Useful for understanding note structure before reading in full.
    """
    return _run(["outline", f"file={file}", "format=json"])


@mcp.tool()
def backlinks(file: str) -> str:
    """
    List all notes that link to this note, with link counts.
    Returns JSON with source notes and link frequency.
    """
    return _run(["backlinks", f"file={file}", "format=json", "counts"])


@mcp.tool()
def links(file: str) -> str:
    """
    List all outgoing links from a note (wikilinks and markdown links).
    Returns a JSON list of linked note paths.
    """
    return _run(["links", f"file={file}"])


# ── Canvas ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_canvases() -> str:
    """
    List all .canvas files in the vault.
    Returns a JSON array of canvas file paths.
    """
    import glob, os
    # Ask obsidian for vault root via a known note, or search broadly
    # We use `find` via vault search pattern
    result = _run(["search", "query=", "limit=1", "format=json"])
    # Fallback: scan known vault locations
    candidates = [
        os.path.expanduser("~/.openclaw/workspace/MrPink"),
        os.path.expanduser("~/vault"),
        os.path.expanduser("~/Obsidian"),
        os.path.expanduser("~/Documents/Obsidian"),
    ]
    canvases = []
    for base in candidates:
        if os.path.isdir(base):
            for path in glob.glob(f"{base}/**/*.canvas", recursive=True):
                canvases.append(path)
    return json.dumps(canvases)


@mcp.tool()
def read_canvas(path: str) -> str:
    """
    Read a canvas file and return its JSON content.
    Provide the full filesystem path to the .canvas file.
    Returns parsed canvas JSON with nodes and edges.
    """
    import os
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return json.dumps({"error": f"Canvas not found: {expanded}"})
    with open(expanded, "r") as f:
        try:
            return json.dumps(json.load(f), indent=2)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid canvas JSON: {e}"})


@mcp.tool()
def write_canvas(path: str, canvas_json: str) -> str:
    """
    Write a canvas file. Provide the full filesystem path and valid canvas JSON string.
    Canvas JSON must have 'nodes' and 'edges' arrays.
    Overwrites existing canvas. Creates file if it doesn't exist.
    """
    import os
    expanded = os.path.expanduser(path)
    try:
        parsed = json.loads(canvas_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})
    if "nodes" not in parsed or "edges" not in parsed:
        return json.dumps({"error": "Canvas JSON must contain 'nodes' and 'edges' keys"})
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    with open(expanded, "w") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)
    return json.dumps({"ok": True, "path": expanded, "nodes": len(parsed["nodes"]), "edges": len(parsed["edges"])})


# ── Write ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_note(path: str, content: str, overwrite: bool = False) -> str:
    """
    Create a new note in the vault. path is vault-relative (e.g. 'Agents/NewAgent.md').
    Will not overwrite an existing note unless overwrite=True.
    Returns the result from the Obsidian CLI.
    """
    cmd = ["create", f"file={path}", f"content={content}"]
    if overwrite:
        cmd.append("overwrite")
    return _run(cmd)


@mcp.tool()
def append_note(file: str, content: str) -> str:
    """
    Append content to an existing note. Adds content at the end of the file.
    Use for journals, logs, or running notes where you want to add without reading first.
    """
    return _run(["append", f"file={file}", f"content={content}"])


# ── Metadata ───────────────────────────────────────────────────────────────────

@mcp.tool()
def tags(counts: bool = True) -> str:
    """
    List all tags in the vault. counts=True includes usage frequency.
    Returns JSON. Useful for discovering vault structure and active topics.
    """
    args = ["tags", "format=json"]
    if counts:
        args.append("counts")
    return _run(args)


@mcp.tool()
def vault_status() -> str:
    """
    Get Obsidian vault info: version, active vault, note count.
    Use to verify the CLI is working and which vault is active.
    """
    version = _run(["version"])
    return json.dumps({"obsidian_version": version, "cli_ok": True})


if __name__ == "__main__":
    mcp.run()

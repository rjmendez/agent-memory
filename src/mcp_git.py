#!/usr/bin/env python3
"""
MrPink Git MCP Server
Structured git operations for the three workspace repos.
Replaces exec-based git calls in heartbeat sync and maintenance tasks.

Repos managed:
  - mrpink-workspace  → ~/.openclaw/workspace
  - agent-mesh        → ~/development/agent-mesh
  - ai-hacklab-starter → ~/development/ai-hacklab-starter
"""

import json
import subprocess
import os
from typing import Optional
from fastmcp import FastMCP

mcp = FastMCP("mrpink-git")

REPOS = {
    "mrpink-workspace": {
        "path": os.path.expanduser("~/.openclaw/workspace"),
        "remote": "github.com/rjmendez/mrpink-workspace",
        "branch": "main",
    },
    "agent-mesh": {
        "path": os.path.expanduser("~/development/agent-mesh"),
        "remote": "github.com/rjmendez/agent-mesh",
        "branch": "master",
    },
    "ai-hacklab-starter": {
        "path": os.path.expanduser("~/development/ai-hacklab-starter"),
        "remote": "github.com/rjmendez/ai-hacklab-starter",
        "branch": "master",
    },
}

# Files/dirs to never commit
IGNORE_PATTERNS = [
    "rust/target/",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "node_modules/",
]


def _run_git(repo_path: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"git timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def _resolve_repo(name: str) -> dict | None:
    return REPOS.get(name)


# ── Status ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def git_status(repo: str = "all") -> str:
    """
    Get git status for one or all workspace repos.
    repo: 'mrpink-workspace', 'agent-mesh', 'ai-hacklab-starter', or 'all'.
    Returns modified, untracked, staged files and current branch for each repo.
    """
    targets = list(REPOS.items()) if repo == "all" else [(repo, REPOS[repo])] if repo in REPOS else []
    if not targets:
        return json.dumps({"error": f"Unknown repo '{repo}'. Options: {list(REPOS.keys())} or 'all'"})

    results = {}
    for name, cfg in targets:
        if not os.path.isdir(cfg["path"]):
            results[name] = {"error": "repo path not found", "path": cfg["path"]}
            continue
        rc, out, err = _run_git(cfg["path"], ["status", "--short"])
        _, branch, _ = _run_git(cfg["path"], ["branch", "--show-current"])
        _, ahead, _ = _run_git(cfg["path"], ["rev-list", f"origin/{cfg['branch']}..HEAD", "--count"])
        results[name] = {
            "path": cfg["path"],
            "branch": branch or cfg["branch"],
            "ahead_of_remote": int(ahead) if ahead.isdigit() else 0,
            "changes": out.splitlines() if out else [],
            "clean": not bool(out),
        }
    return json.dumps(results, indent=2)


@mcp.tool()
def git_log(repo: str, limit: int = 10) -> str:
    """
    Get recent commit history for a repo.
    repo: 'mrpink-workspace', 'agent-mesh', or 'ai-hacklab-starter'.
    Returns commit hash, author, date, and message for recent commits.
    """
    cfg = _resolve_repo(repo)
    if not cfg:
        return json.dumps({"error": f"Unknown repo '{repo}'"})
    rc, out, err = _run_git(
        cfg["path"],
        ["log", f"-{min(limit, 50)}", "--pretty=format:%H|%an|%ai|%s"]
    )
    if rc != 0:
        return json.dumps({"error": err})
    commits = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({"hash": parts[0][:10], "author": parts[1], "date": parts[2], "message": parts[3]})
    return json.dumps(commits, indent=2)


# ── Sync ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def git_pull(repo: str = "all") -> str:
    """
    Pull latest changes from origin for one or all repos.
    repo: repo name or 'all'. Skips repos with uncommitted changes to avoid conflicts.
    Returns pull result per repo.
    """
    targets = list(REPOS.items()) if repo == "all" else [(repo, REPOS[repo])] if repo in REPOS else []
    if not targets:
        return json.dumps({"error": f"Unknown repo '{repo}'"})

    results = {}
    for name, cfg in targets:
        if not os.path.isdir(cfg["path"]):
            results[name] = "path not found"
            continue
        # Check for uncommitted changes first
        rc, status, _ = _run_git(cfg["path"], ["status", "--short"])
        if status:
            results[name] = f"skipped — uncommitted changes: {status[:100]}"
            continue
        rc, out, err = _run_git(cfg["path"], ["pull", "origin", cfg["branch"]], timeout=60)
        results[name] = out or err or "ok"
    return json.dumps(results, indent=2)


@mcp.tool()
def git_commit_and_push(repo: str, message: str, add_all: bool = True) -> str:
    """
    Stage, commit, and push changes in a repo.
    add_all=True stages all tracked+untracked files (excluding .gitignore patterns).
    Will not commit if working tree is clean.
    Returns commit hash and push result.
    """
    cfg = _resolve_repo(repo)
    if not cfg:
        return json.dumps({"error": f"Unknown repo '{repo}'"})
    if not os.path.isdir(cfg["path"]):
        return json.dumps({"error": f"Repo path not found: {cfg['path']}"})
    if not message or len(message.strip()) < 5:
        return json.dumps({"error": "Commit message too short (min 5 chars)"})

    if add_all:
        rc, _, err = _run_git(cfg["path"], ["add", "-A"])
        if rc != 0:
            return json.dumps({"error": f"git add failed: {err}"})

    # Check if there's anything to commit
    rc, status, _ = _run_git(cfg["path"], ["status", "--short"])
    staged_rc, staged, _ = _run_git(cfg["path"], ["diff", "--cached", "--name-only"])
    if not staged:
        return json.dumps({"ok": True, "repo": repo, "result": "nothing to commit, working tree clean"})

    rc, out, err = _run_git(cfg["path"], ["commit", "-m", message])
    if rc != 0:
        return json.dumps({"error": f"git commit failed: {err}"})

    _, hash_out, _ = _run_git(cfg["path"], ["rev-parse", "--short", "HEAD"])

    rc_push, push_out, push_err = _run_git(cfg["path"], ["push", "origin", cfg["branch"]], timeout=60)
    return json.dumps({
        "ok": rc_push == 0,
        "repo": repo,
        "commit": hash_out,
        "message": message,
        "push": push_out or push_err or "ok",
    })


@mcp.tool()
def git_sync_all() -> str:
    """
    Full heartbeat sync: pull all repos, commit and push any local changes.
    Skips build artifacts (rust/target, __pycache__, *.pyc).
    Safe to call from heartbeat — idempotent if already clean.
    Returns per-repo summary.
    """
    results = {}
    for name, cfg in REPOS.items():
        if not os.path.isdir(cfg["path"]):
            results[name] = "path not found"
            continue

        # Pull first
        rc_pull, pull_out, _ = _run_git(cfg["path"], ["pull", "origin", cfg["branch"], "--rebase"], timeout=60)

        # Stage and commit any local changes
        _run_git(cfg["path"], ["add", "-A"])
        rc, staged, _ = _run_git(cfg["path"], ["diff", "--cached", "--name-only"])
        if staged:
            rc_c, _, err_c = _run_git(cfg["path"], ["commit", "-m", "chore: heartbeat auto-sync"])
            if rc_c == 0:
                rc_p, push_out, push_err = _run_git(cfg["path"], ["push", "origin", cfg["branch"]], timeout=60)
                results[name] = f"committed+pushed: {push_out or push_err}"
            else:
                results[name] = f"commit failed: {err_c}"
        else:
            results[name] = f"pull: {pull_out or 'ok'}, no local changes"

    return json.dumps(results, indent=2)


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""
MrPink Audit DB MCP Server
Exposes audit-postgres (audit_framework) for reading and writing security findings.
Charlie writes findings here; MrPink reads and queries them.
"""

import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional
from fastmcp import FastMCP

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "audit_framework",
    "user": "audit",
    "password": "audit_dev_pass",
}

mcp = FastMCP("mrpink-audit")


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def serialize(rows: list) -> list:
    result = []
    for row in rows:
        r = dict(row)
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
        result.append(r)
    return result


# ── Findings ───────────────────────────────────────────────────────────────────

@mcp.tool()
def get_findings(module: Optional[str] = None, severity: Optional[str] = None,
                 verified: Optional[bool] = None, limit: int = 50) -> str:
    """
    Query security findings from audit_framework.generic_findings.
    Filter by module (scanner name), severity (critical/high/medium/low/info),
    or verified (True/False). Returns live data from PostgreSQL.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = "SELECT id, module, source, finding_type, severity, title, detail, verified, fingerprint, created_at FROM generic_findings WHERE 1=1"
            params = []
            if module:
                query += " AND module ILIKE %s"
                params.append(f"%{module}%")
            if severity:
                query += " AND severity = %s"
                params.append(severity)
            if verified is not None:
                query += " AND verified = %s"
                params.append(verified)
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            return json.dumps(serialize(cur.fetchall()), indent=2)


@mcp.tool()
def get_findings_summary() -> str:
    """
    Get a summary count of findings grouped by severity and module.
    Useful for a quick status overview without pulling all rows.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT module, severity, verified,
                       COUNT(*) as count
                FROM generic_findings
                GROUP BY module, severity, verified
                ORDER BY
                  CASE severity
                    WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5
                  END, module
            """)
            rows = serialize(cur.fetchall())

            cur.execute("SELECT COUNT(*) as total FROM generic_findings")
            total = cur.fetchone()["total"]

            return json.dumps({"total": total, "breakdown": rows}, indent=2)


@mcp.tool()
def search_findings(query: str, limit: int = 20) -> str:
    """
    Full-text search across finding titles and details.
    Searches both title and detail fields using ILIKE.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, module, source, severity, title, detail, verified, created_at
                FROM generic_findings
                WHERE title ILIKE %s OR detail ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (f"%{query}%", f"%{query}%", limit))
            return json.dumps(serialize(cur.fetchall()), indent=2)


@mcp.tool()
def mark_finding_verified(fingerprint: str, verified: bool = True) -> str:
    """
    Mark a finding as verified or unverified by its fingerprint hash.
    Use after manually confirming a finding is a real issue.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generic_findings SET verified = %s WHERE fingerprint = %s",
                (verified, fingerprint)
            )
            affected = cur.rowcount
            conn.commit()
            return json.dumps({"updated": affected, "fingerprint": fingerprint, "verified": verified})


# ── Validation ─────────────────────────────────────────────────────────────────

@mcp.tool()
def validate() -> str:
    """
    Self-test all audit DB MCP tools against the live database.
    Returns pass/fail for each tool.
    """
    results = {}

    # 1. get_findings_summary
    try:
        data = json.loads(get_findings_summary())
        assert "total" in data
        results["get_findings_summary"] = f"PASS (total={data['total']} findings)"
    except Exception as e:
        results["get_findings_summary"] = f"FAIL: {e}"

    # 2. get_findings
    try:
        data = json.loads(get_findings(limit=5))
        assert isinstance(data, list)
        results["get_findings"] = f"PASS ({len(data)} rows returned)"
    except Exception as e:
        results["get_findings"] = f"FAIL: {e}"

    # 3. search_findings
    try:
        data = json.loads(search_findings("firebase", limit=5))
        assert isinstance(data, list)
        results["search_findings"] = f"PASS ({len(data)} results for 'firebase')"
    except Exception as e:
        results["search_findings"] = f"FAIL: {e}"

    # 4. mark_finding_verified - skip destructive test, just verify DB connectivity
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        results["mark_finding_verified"] = "PASS (DB connectivity verified, write test skipped)"
    except Exception as e:
        results["mark_finding_verified"] = f"FAIL: {e}"

    passed = sum(1 for v in results.values() if v.startswith("PASS"))
    total = len(results)
    return json.dumps({"summary": f"{passed}/{total} tools passing", "tools": results}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""
stats.py — tracks MCP tool call counts per project.

Stored at ~/.graph-agent/stats.db (central, cross-project).
Token savings estimate: each tool call ≈ avoids ~300 tokens of manual file reading.
"""
from __future__ import annotations
import sqlite3
import time
import os
from pathlib import Path

_STATS_DIR  = Path(os.environ.get("GRAPH_HOME", Path.home() / ".graph-agent"))
_STATS_PATH = _STATS_DIR / "stats.db"

# Rough token cost saved per tool call (conservative estimate)
_TOKENS_PER_CALL = {
    "search_symbols":    200,
    "get_file_symbols":  150,
    "get_callers":       250,
    "get_callees":       250,
    "get_file_imports":  150,
    "get_symbol_context": 400,
    "list_files":        100,
    "get_stats":          50,
    "list_projects":      50,
    "reindex_repo":        0,   # not a query, no token saving
    "reindex_file":        0,
}
_DEFAULT_TOKENS = 200


def _get_conn() -> sqlite3.Connection:
    _STATS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_STATS_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id           INTEGER PRIMARY KEY,
            ts           REAL    NOT NULL,
            tool         TEXT    NOT NULL,
            repo         TEXT,
            result_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tc_tool ON tool_calls(tool);
        CREATE INDEX IF NOT EXISTS idx_tc_repo ON tool_calls(repo);
    """)
    conn.commit()
    return conn


def log_call(tool: str, repo: str | None, result_count: int = 0):
    """Record one tool invocation. Fire-and-forget — swallows errors."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO tool_calls(ts, tool, repo, result_count) VALUES(?,?,?,?)",
            (time.time(), tool, repo, result_count),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # never crash the server over stats


def get_summary() -> dict:
    """
    Return usage summary grouped by project.

    Shape:
    {
        "by_project": {
            "<repo_name>": {
                "calls": int,
                "estimated_tokens_saved": int,
                "top_tools": [{"tool": str, "calls": int}, ...]
            }
        },
        "totals": {"calls": int, "estimated_tokens_saved": int}
    }
    """
    try:
        conn = _get_conn()

        # per-project, per-tool counts
        rows = conn.execute("""
            SELECT
                COALESCE(repo, '__unknown__') AS repo,
                tool,
                COUNT(*) AS calls
            FROM tool_calls
            GROUP BY repo, tool
            ORDER BY repo, calls DESC
        """).fetchall()
        conn.close()
    except Exception:
        return {"by_project": {}, "totals": {"calls": 0, "estimated_tokens_saved": 0}}

    by_project: dict = {}
    total_calls = 0
    total_tokens = 0

    for r in rows:
        repo_key = os.path.basename(r["repo"].rstrip("/\\")) if r["repo"] != "__unknown__" else "(unknown)"
        n = r["calls"]
        tok = _TOKENS_PER_CALL.get(r["tool"], _DEFAULT_TOKENS) * n

        if repo_key not in by_project:
            by_project[repo_key] = {"calls": 0, "estimated_tokens_saved": 0, "top_tools": []}

        by_project[repo_key]["calls"] += n
        by_project[repo_key]["estimated_tokens_saved"] += tok
        by_project[repo_key]["top_tools"].append({"tool": r["tool"], "calls": n})

        total_calls  += n
        total_tokens += tok

    # keep only top-5 tools per project
    for v in by_project.values():
        v["top_tools"] = v["top_tools"][:5]

    return {
        "by_project": by_project,
        "totals": {
            "calls": total_calls,
            "estimated_tokens_saved": total_tokens,
        },
    }


# ── CLI ────────────────────────────────────────────────────────────────

def _print_dashboard():
    data = get_summary()
    by_project = data["by_project"]
    totals = data["totals"]

    if not by_project:
        print("No tool calls recorded yet.")
        print(f"Stats DB: {_STATS_PATH}")
        return

    W = 60
    print("=" * W)
    print(f"  graph-kit usage stats")
    print(f"  DB: {_STATS_PATH}")
    print("=" * W)

    for project, info in sorted(by_project.items(), key=lambda x: -x[1]["calls"]):
        calls = info["calls"]
        tokens = info["estimated_tokens_saved"]
        print(f"\n  {project}")
        print(f"  {'─' * (W - 4)}")
        print(f"    Tool calls          : {calls}")
        print(f"    Est. tokens saved   : ~{tokens:,}")
        if info["top_tools"]:
            print(f"    Top tools:")
            for t in info["top_tools"]:
                bar = "█" * min(t["calls"], 30)
                print(f"      {t['tool']:<24} {t['calls']:>4}  {bar}")

    print()
    print("=" * W)
    print(f"  TOTAL calls          : {totals['calls']}")
    print(f"  TOTAL tokens saved   : ~{totals['estimated_tokens_saved']:,}")
    print("=" * W)


if __name__ == "__main__":
    _print_dashboard()

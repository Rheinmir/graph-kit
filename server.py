"""
MCP Server — exposes the code knowledge graph to AI assistants (Claude, Cursor, etc.)

Usage:
    # Step 1: index your repo
    python indexer.py /path/to/repo --db ./graph.db

    # Step 2: start MCP server
    python server.py --db ./graph.db

    # Or do both at once (index then serve):
    python server.py --repo /path/to/repo --db ./graph.db

    # Index + serve + auto re-index on file save:
    python server.py --repo /path/to/repo --db ./graph.db --watch

Add to Claude Desktop config (claude_desktop_config.json):
    {
      "mcpServers": {
        "code-graph": {
          "command": "python",
          "args": ["/absolute/path/to/server.py", "--repo", "/path/to/repo", "--watch"]
        }
      }
    }
"""
from __future__ import annotations
import argparse
import os
import sys

from mcp.server.fastmcp import FastMCP

import db as graph_db

# ── Init ──────────────────────────────────────────────────────────────

ap = argparse.ArgumentParser(add_help=False)
ap.add_argument("--db",    default=None,   help="SQLite DB path (default: auto from --repo)")
ap.add_argument("--repo",  default=None,   help="Index this repo before serving")
ap.add_argument("--force", action="store_true", help="Force re-index even if unchanged")
ap.add_argument("--watch", action="store_true", help="Auto re-index on file changes")
args, _ = ap.parse_known_args()

# resolve DB path: explicit --db > auto from --repo > default
if args.db:
    DB_PATH = args.db
elif args.repo:
    DB_PATH = graph_db.db_path_for_repo(args.repo)
else:
    DB_PATH = graph_db.DB_PATH

# optional: index on startup
if args.repo:
    from indexer import index_repo
    print(f"[graph-agent] Indexing {args.repo} ...", file=sys.stderr)
    stats = index_repo(args.repo, db_path=DB_PATH, force=args.force)
    print(f"[graph-agent] Indexed {stats['indexed']} files, "
          f"{stats['symbols']} symbols, {stats['edges']} edges", file=sys.stderr)

# optional: start file watcher in background
if args.watch and args.repo:
    from watcher import start_watcher
    _observer = start_watcher(args.repo, db_path=DB_PATH)
elif args.watch and not args.repo:
    print("[graph-agent] --watch requires --repo", file=sys.stderr)

graph_db.init_db(DB_PATH)

mcp = FastMCP(
    "code-graph",
    instructions=(
        "Use these tools to understand the codebase structure. "
        "Start with `get_stats` to see what is indexed, then search for symbols or files."
    ),
)


def _conn():
    return graph_db.get_conn(DB_PATH)


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_stats() -> dict:
    """Return total counts of indexed files, symbols, and edges in the graph."""
    with _conn() as conn:
        return graph_db.get_stats(conn)


@mcp.tool()
def search_symbols(query: str, kind: str = None) -> list[dict]:
    """
    Search for symbols (functions, classes, methods) by name.

    Args:
        query: Partial or full symbol name (case-insensitive substring match).
        kind:  Optional filter — one of: function, class, method.

    Returns list of {name, kind, file, line_start, line_end, parent}.
    """
    with _conn() as conn:
        return graph_db.search_symbols(conn, query, kind)


@mcp.tool()
def get_file_symbols(file_path: str) -> list[dict]:
    """
    List all symbols defined in a specific file.

    Args:
        file_path: Relative path from repo root (e.g. "src/api/auth.py").

    Returns list of {name, kind, line_start, line_end, parent}.
    """
    with _conn() as conn:
        return graph_db.get_file_symbols(conn, file_path)


@mcp.tool()
def get_callers(function_name: str) -> list[dict]:
    """
    Find all places in the codebase that call a given function or method.

    Args:
        function_name: Exact function/method name to look up.

    Returns list of {src_symbol (caller name), file, line}.
    """
    with _conn() as conn:
        return graph_db.get_callers(conn, function_name)


@mcp.tool()
def get_callees(function_name: str) -> list[dict]:
    """
    List all functions/methods that a given function calls.

    Args:
        function_name: Exact function/method name.

    Returns list of {dst_symbol (callee name), file, line}.
    """
    with _conn() as conn:
        return graph_db.get_callees(conn, function_name)


@mcp.tool()
def get_file_imports(file_path: str) -> list[dict]:
    """
    List all modules/files imported by a specific file.

    Args:
        file_path: Relative path from repo root (e.g. "src/utils/helpers.ts").

    Returns list of {dst_file_path (imported module), line}.
    """
    with _conn() as conn:
        return graph_db.get_file_imports(conn, file_path)


@mcp.tool()
def get_symbol_context(symbol_name: str) -> dict:
    """
    Get full context for a symbol: its definition, what it calls, and who calls it.
    Useful as a starting point when asked to explain or modify a specific function/class.

    Args:
        symbol_name: Exact name of the function, class, or method.

    Returns {definition: [...], callers: [...], callees: [...]}.
    """
    with _conn() as conn:
        definition = graph_db.search_symbols(conn, symbol_name)
        # exact match only
        definition = [s for s in definition if s["name"] == symbol_name]
        callers = graph_db.get_callers(conn, symbol_name)
        callees = graph_db.get_callees(conn, symbol_name)
        return {
            "definition": definition,
            "callers": callers,
            "callees": callees,
        }


@mcp.tool()
def reindex_repo(force: bool = False) -> dict:
    """
    Re-index the repository to update the graph after code changes.
    Call this after writing or modifying files to ensure the graph reflects the latest code.

    Args:
        force: If True, re-index all files even if unchanged. Default False (skip unchanged).

    Returns {indexed, skipped_unchanged, errors, files, symbols, edges}.
    """
    if not args.repo:
        return {"error": "No repo configured. Start server with --repo /path/to/repo."}
    from indexer import index_repo
    return index_repo(args.repo, db_path=DB_PATH, force=force)


@mcp.tool()
def reindex_file(file_path: str) -> dict:
    """
    Re-index a single file immediately. Faster than reindex_repo for small edits.

    Args:
        file_path: Relative path from repo root (e.g. "src/api/auth.py").

    Returns {status, symbols, edges} or {error}.
    """
    if not args.repo:
        return {"error": "No repo configured. Start server with --repo /path/to/repo."}
    abs_path = os.path.join(os.path.abspath(args.repo), file_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return {"error": f"File not found: {abs_path}"}
    from indexer import index_file
    conn = graph_db.get_conn(DB_PATH)
    try:
        index_file(conn, abs_path, file_path, force=True)
        conn.commit()
        syms = len(graph_db.get_file_symbols(conn, file_path))
        return {"status": "ok", "file": file_path, "symbols": syms}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def list_files(directory: str = "", language: str = None) -> list[str]:
    """
    List all indexed files, optionally filtered by directory prefix or language.

    Args:
        directory: Path prefix to filter (e.g. "src/api"). Empty = all files.
        language:  Optional language filter (e.g. "python", "typescript").

    Returns list of file paths.
    """
    conn = _conn()
    sql = "SELECT path FROM files WHERE 1=1"
    params = []
    if directory:
        sql += " AND path LIKE ?"
        params.append(f"{directory}%")
    if language:
        sql += " AND language = ?"
        params.append(language)
    sql += " ORDER BY path"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [r["path"] for r in rows]


# ── Entrypoint ────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()

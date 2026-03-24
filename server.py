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

# ── File watchers (one per indexed repo) ─────────────────────────────
_watchers: dict[str, object] = {}  # repo_root → Observer

def _ensure_watching(repo_root: str, db_path: str):
    """Start a watcher for repo_root if not already watching."""
    key = os.path.abspath(repo_root)
    if key not in _watchers:
        from watcher import start_watcher
        _watchers[key] = start_watcher(repo_root, db_path=db_path)

if args.watch:
    from watcher import start_watcher
    # watch explicit --repo if given
    if args.repo:
        _ensure_watching(args.repo, DB_PATH)
    # watch ALL previously indexed repos
    for _db in graph_db.get_all_db_paths():
        _root = graph_db.get_repo_root(_db)
        if _root and os.path.isdir(_root):
            _ensure_watching(_root, _db)

graph_db.init_db(DB_PATH)

mcp = FastMCP(
    "code-graph",
    instructions=(
        "You have access to a local code knowledge graph.\n\n"
        "RULES — follow strictly:\n"
        "1. After editing, creating, or deleting any source file, ALWAYS call "
        "`reindex_file` for that file (or `reindex_repo` if multiple files changed). "
        "Never skip this step — the graph will be stale otherwise.\n"
        "2. Before answering questions about code structure (who calls X, what does Y import, etc.), "
        "call the relevant query tool instead of guessing from memory.\n"
        "3. If a query returns empty and the project may not be indexed yet, "
        "call `reindex_repo` with any open file path — it will auto-detect the repo root.\n"
    ),
)


# ── Multi-repo query helpers ──────────────────────────────────────────

def _each_db(fn):
    """Run fn(conn) on every project DB, merge list results."""
    results = []
    for path in graph_db.get_all_db_paths():
        conn = graph_db.get_conn(path)
        try:
            r = fn(conn)
            if isinstance(r, list):
                results.extend(r)
        finally:
            conn.close()
    return results


def _repo_conn(repo_path: str):
    """Get connection for a specific repo's DB."""
    return graph_db.get_conn(graph_db.db_path_for_repo(repo_path))


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_stats() -> dict:
    """Return total counts across all indexed projects."""
    totals = {"projects": 0, "files": 0, "symbols": 0, "edges": 0}
    for path in graph_db.get_all_db_paths():
        conn = graph_db.get_conn(path)
        try:
            s = graph_db.get_stats(conn)
            totals["projects"] += 1
            totals["files"]   += s["files"]
            totals["symbols"] += s["symbols"]
            totals["edges"]   += s["edges"]
        finally:
            conn.close()
    return totals


@mcp.tool()
def list_projects() -> list[str]:
    """List all indexed projects (repo names)."""
    return [
        os.path.basename(p).rsplit("-", 1)[0]
        for p in graph_db.get_all_db_paths()
    ]


@mcp.tool()
def search_symbols(query: str, kind: str = None) -> list[dict]:
    """
    Search for symbols (functions, classes, methods) by name across all indexed projects.

    Args:
        query: Partial or full symbol name (case-insensitive substring match).
        kind:  Optional filter — one of: function, class, method.
    """
    return _each_db(lambda c: graph_db.search_symbols(c, query, kind))


@mcp.tool()
def get_file_symbols(file_path: str) -> list[dict]:
    """
    List all symbols defined in a specific file.

    Args:
        file_path: Relative path from repo root (e.g. "src/api/auth.py").
    """
    return _each_db(lambda c: graph_db.get_file_symbols(c, file_path))


@mcp.tool()
def get_callers(function_name: str) -> list[dict]:
    """
    Find all places that call a given function or method, across all indexed projects.

    Args:
        function_name: Exact function/method name.
    """
    return _each_db(lambda c: graph_db.get_callers(c, function_name))


@mcp.tool()
def get_callees(function_name: str) -> list[dict]:
    """
    List all functions a given function calls, across all indexed projects.

    Args:
        function_name: Exact function/method name.
    """
    return _each_db(lambda c: graph_db.get_callees(c, function_name))


@mcp.tool()
def get_file_imports(file_path: str) -> list[dict]:
    """
    List all modules/files imported by a specific file.

    Args:
        file_path: Relative path from repo root (e.g. "src/utils/helpers.ts").
    """
    return _each_db(lambda c: graph_db.get_file_imports(c, file_path))


@mcp.tool()
def get_symbol_context(symbol_name: str) -> dict:
    """
    Get full context for a symbol: definition, callers, callees. Across all projects.
    Use this as the first call when asked to explain or modify a specific function/class.

    Args:
        symbol_name: Exact name of the function, class, or method.
    """
    definition = _each_db(lambda c: [
        s for s in graph_db.search_symbols(c, symbol_name)
        if s["name"] == symbol_name
    ])
    return {
        "definition": definition,
        "callers":    _each_db(lambda c: graph_db.get_callers(c, symbol_name)),
        "callees":    _each_db(lambda c: graph_db.get_callees(c, symbol_name)),
    }


def _find_repo_root(file_path: str) -> str | None:
    """Walk up from file_path to find repo root (git root or first dir with known config files)."""
    markers = {".git", "package.json", "go.mod", "Cargo.toml", "pyproject.toml", "composer.json"}
    current = Path(file_path).resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if any((parent / m).exists() for m in markers):
            return str(parent)
    return str(current)  # fallback: use the file's directory


@mcp.tool()
def reindex_repo(repo_path: str = "", force: bool = False) -> dict:
    """
    Re-index a repository after code changes. Call this after writing or modifying files.

    Args:
        repo_path: Absolute path to repo root OR any file inside the repo —
                   the tool will auto-detect the root from .git / go.mod / package.json etc.
                   Pass any currently open file path if unsure of the exact root.
        force:     Re-index all files even if unchanged (default False).
    """
    target = repo_path or args.repo
    if not target:
        return {"error": "Provide repo_path (or any file path inside the repo)."}
    if os.path.isfile(target):
        target = _find_repo_root(target)
    from indexer import index_repo as _index
    db = graph_db.db_path_for_repo(target)
    result = _index(target, db_path=db, force=force)
    # auto-watch the repo if --watch flag is on
    if args.watch:
        _ensure_watching(target, db)
    return result


@mcp.tool()
def reindex_file(file_path: str, repo_path: str = "") -> dict:
    """
    Re-index a single file immediately. Faster than reindex_repo for small edits.

    Args:
        file_path: Relative path from repo root (e.g. "src/api/auth.py").
        repo_path: Absolute path to repo root. Omit to use the server's --repo if set.
    """
    target = repo_path or args.repo
    if not target:
        target = _find_repo_root(file_path)
    abs_path = os.path.join(os.path.abspath(target), file_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return {"error": f"File not found: {abs_path}"}
    from indexer import index_file
    db = graph_db.db_path_for_repo(target)
    conn = graph_db.get_conn(db)
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
    List all indexed files across all projects, optionally filtered.

    Args:
        directory: Path prefix to filter (e.g. "src/api"). Empty = all files.
        language:  Language filter (e.g. "python", "typescript").
    """
    def _query(conn):
        sql = "SELECT path FROM files WHERE 1=1"
        params = []
        if directory:
            sql += " AND path LIKE ?"
            params.append(f"{directory}%")
        if language:
            sql += " AND language = ?"
            params.append(language)
        return [r["path"] for r in conn.execute(sql, params).fetchall()]
    return _each_db(_query)


# ── Entrypoint ────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()

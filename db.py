"""
SQLite storage for the code knowledge graph.
Schema: files → symbols → edges (CALLS, IMPORTS)
"""
import hashlib
import sqlite3
import os
from pathlib import Path

# Central directory for all project DBs
_GRAPH_DIR = Path(os.environ.get("GRAPH_HOME", Path.home() / ".graph-agent"))

# Default DB path — overridden when --repo is passed to server/indexer
DB_PATH = os.environ.get("GRAPH_DB", str(_GRAPH_DIR / "default.db"))


def db_path_for_repo(repo_path: str) -> str:
    """Return a stable DB path for a given repo, stored in ~/.graph-agent/."""
    repo = Path(repo_path).resolve()
    name = repo.name
    h = hashlib.sha1(str(repo).encode()).hexdigest()[:8]
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    return str(_GRAPH_DIR / f"{name}-{h}.db")


def get_all_db_paths() -> list[str]:
    """Return paths of all project DBs in ~/.graph-agent/."""
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    return [str(p) for p in sorted(_GRAPH_DIR.glob("*.db"))]


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str = DB_PATH, repo_root: str = None):
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS files (
            id       INTEGER PRIMARY KEY,
            path     TEXT UNIQUE NOT NULL,
            language TEXT,
            checksum TEXT
        );

        CREATE TABLE IF NOT EXISTS symbols (
            id         INTEGER PRIMARY KEY,
            file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            kind       TEXT NOT NULL,   -- function | class | method
            line_start INTEGER,
            line_end   INTEGER,
            parent     TEXT             -- parent class name for methods
        );

        CREATE TABLE IF NOT EXISTS edges (
            id            INTEGER PRIMARY KEY,
            kind          TEXT NOT NULL,       -- CALLS | IMPORTS
            src_file_id   INTEGER REFERENCES files(id) ON DELETE CASCADE,
            src_symbol    TEXT,                -- caller name
            dst_file_path TEXT,                -- for IMPORTS
            dst_symbol    TEXT,                -- callee name
            line          INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_symbols_name    ON symbols(name);
        CREATE INDEX IF NOT EXISTS idx_symbols_file    ON symbols(file_id);
        CREATE INDEX IF NOT EXISTS idx_edges_src       ON edges(src_symbol);
        CREATE INDEX IF NOT EXISTS idx_edges_dst       ON edges(dst_symbol);
        CREATE INDEX IF NOT EXISTS idx_edges_kind      ON edges(kind);
    """)
    conn.commit()
    if repo_root:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('repo_root', ?)",
            (str(Path(repo_root).resolve()),)
        )
        conn.commit()
    conn.close()


def get_repo_root(db_path: str) -> str | None:
    """Return the repo root stored in this DB, or None."""
    try:
        conn = get_conn(db_path)
        row = conn.execute("SELECT value FROM meta WHERE key='repo_root'").fetchone()
        conn.close()
        return row["value"] if row else None
    except Exception:
        return None


# ── File helpers ─────────────────────────────────────────────────────

def upsert_file(conn: sqlite3.Connection, path: str, language: str, checksum: str) -> int:
    conn.execute(
        "INSERT INTO files(path, language, checksum) VALUES(?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET language=excluded.language, checksum=excluded.checksum",
        (path, language, checksum),
    )
    row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    return row["id"]


def get_file_checksum(conn: sqlite3.Connection, path: str) -> str | None:
    row = conn.execute("SELECT checksum FROM files WHERE path=?", (path,)).fetchone()
    return row["checksum"] if row else None


def delete_file_data(conn: sqlite3.Connection, path: str):
    """Remove all symbols and edges for a file (before re-indexing)."""
    row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    if row:
        conn.execute("DELETE FROM symbols WHERE file_id=?", (row["id"],))
        conn.execute("DELETE FROM edges WHERE src_file_id=?", (row["id"],))


# ── Symbol helpers ────────────────────────────────────────────────────

def insert_symbol(conn: sqlite3.Connection, file_id: int, name: str,
                  kind: str, line_start: int, line_end: int, parent: str = None):
    conn.execute(
        "INSERT INTO symbols(file_id, name, kind, line_start, line_end, parent) VALUES(?,?,?,?,?,?)",
        (file_id, name, kind, line_start, line_end, parent),
    )


def insert_edge(conn: sqlite3.Connection, kind: str, src_file_id: int,
                src_symbol: str = None, dst_file_path: str = None,
                dst_symbol: str = None, line: int = None):
    conn.execute(
        "INSERT INTO edges(kind, src_file_id, src_symbol, dst_file_path, dst_symbol, line) "
        "VALUES(?,?,?,?,?,?)",
        (kind, src_file_id, src_symbol, dst_file_path, dst_symbol, line),
    )


# ── Query helpers ─────────────────────────────────────────────────────

def search_symbols(conn: sqlite3.Connection, query: str, kind: str = None) -> list[dict]:
    sql = """
        SELECT s.name, s.kind, s.line_start, s.line_end, s.parent, f.path
        FROM symbols s JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE ?
    """
    params = [f"%{query}%"]
    if kind:
        sql += " AND s.kind = ?"
        params.append(kind)
    sql += " ORDER BY s.name LIMIT 50"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_callers(conn: sqlite3.Connection, symbol_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT e.src_symbol, e.line, f.path
        FROM edges e JOIN files f ON e.src_file_id = f.id
        WHERE e.kind = 'CALLS' AND e.dst_symbol = ?
        LIMIT 50
    """, (symbol_name,)).fetchall()
    return [dict(r) for r in rows]


def get_callees(conn: sqlite3.Connection, symbol_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT e.dst_symbol, e.line, f.path
        FROM edges e JOIN files f ON e.src_file_id = f.id
        WHERE e.kind = 'CALLS' AND e.src_symbol = ?
        LIMIT 50
    """, (symbol_name,)).fetchall()
    return [dict(r) for r in rows]


def get_file_imports(conn: sqlite3.Connection, file_path: str) -> list[dict]:
    rows = conn.execute("""
        SELECT e.dst_file_path, e.line
        FROM edges e JOIN files f ON e.src_file_id = f.id
        WHERE e.kind = 'IMPORTS' AND f.path = ?
    """, (file_path,)).fetchall()
    return [dict(r) for r in rows]


def get_file_symbols(conn: sqlite3.Connection, file_path: str) -> list[dict]:
    rows = conn.execute("""
        SELECT s.name, s.kind, s.line_start, s.line_end, s.parent
        FROM symbols s JOIN files f ON s.file_id = f.id
        WHERE f.path = ?
        ORDER BY s.line_start
    """, (file_path,)).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    return {"files": files, "symbols": symbols, "edges": edges}

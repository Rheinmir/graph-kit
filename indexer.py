"""
Indexer — walks a directory, parses each source file, writes results to SQLite.

Usage:
    python indexer.py /path/to/repo [--db ./graph.db] [--force]

Options:
    --db PATH    Path to SQLite DB (default: ./graph.db)
    --force      Re-index all files even if unchanged
"""
from __future__ import annotations
import argparse
import hashlib
import os
import sys
import sqlite3
from pathlib import Path

import db as graph_db
from parser import parse_file, detect_language

# ── Directories / files to skip ───────────────────────────────────────

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "out", "target", "vendor",
    ".idea", ".vscode", "coverage", ".pytest_cache", ".mypy_cache",
}

SKIP_EXTENSIONS = {".min.js", ".min.css", ".map", ".lock", ".log", ".svg",
                   ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}

MAX_FILE_SIZE = 512 * 1024  # 512 KB — skip huge generated files


# ── Main indexer ──────────────────────────────────────────────────────

def _file_checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def should_skip(path: str) -> bool:
    p = Path(path)
    # skip by extension
    suffix = "".join(p.suffixes[-1:]).lower()
    if suffix in SKIP_EXTENSIONS:
        return True
    # skip by size
    try:
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return True
    except OSError:
        return True
    return False


def iter_source_files(repo_path: str):
    """Yield (abs_path, rel_path) for every parseable file under repo_path."""
    base = os.path.abspath(repo_path)
    for dirpath, dirnames, filenames in os.walk(base):
        # prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, base).replace("\\", "/")
            if detect_language(fname) and not should_skip(abs_path):
                yield abs_path, rel_path


def index_file(conn: sqlite3.Connection, abs_path: str, rel_path: str, force: bool = False):
    """Parse one file and write symbols/edges to DB. Skips if checksum unchanged."""
    checksum = _file_checksum(abs_path)

    # skip if unchanged
    if not force:
        existing = graph_db.get_file_checksum(conn, rel_path)
        if existing == checksum:
            return False  # unchanged

    # clear old data for this file
    graph_db.delete_file_data(conn, rel_path)

    lang = detect_language(abs_path)
    file_id = graph_db.upsert_file(conn, rel_path, lang, checksum)

    result = parse_file(abs_path)
    if not result:
        return True

    for sym in result.symbols:
        graph_db.insert_symbol(conn, file_id, sym.name, sym.kind,
                               sym.line_start, sym.line_end, sym.parent)

    for imp in result.imports:
        graph_db.insert_edge(conn, "IMPORTS", file_id,
                             dst_file_path=imp.target, line=imp.line)

    for call in result.calls:
        graph_db.insert_edge(conn, "CALLS", file_id,
                             src_symbol=call.caller, dst_symbol=call.callee,
                             line=call.line)
    return True


def index_repo(repo_path: str, db_path: str = graph_db.DB_PATH, force: bool = False) -> dict:
    """Index an entire repo. Returns stats dict."""
    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    graph_db.init_db(db_path, repo_root=repo_path)
    conn = graph_db.get_conn(db_path)

    total = indexed = skipped = errors = 0
    try:
        for abs_path, rel_path in iter_source_files(repo_path):
            total += 1
            try:
                changed = index_file(conn, abs_path, rel_path, force=force)
                if changed:
                    indexed += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(f"  [error] {rel_path}: {e}", file=sys.stderr)

            if total % 100 == 0:
                conn.commit()
                print(f"  ... {total} files scanned", flush=True)

        conn.commit()
    finally:
        conn.close()

    stats = graph_db.get_stats(graph_db.get_conn(db_path))
    return {
        "scanned": total,
        "indexed": indexed,
        "skipped_unchanged": skipped,
        "errors": errors,
        **stats,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Index a codebase into a local knowledge graph.")
    ap.add_argument("repo", nargs="?", default=".", help="Path to repo root (default: current directory)")
    ap.add_argument("--db", default=None, help="SQLite DB path (default: auto ~/.graph-agent/<name>.db)")
    ap.add_argument("--force", action="store_true", help="Re-index all files even if unchanged")
    args = ap.parse_args()

    db_path = args.db or graph_db.db_path_for_repo(args.repo)
    print(f"Indexing: {os.path.abspath(args.repo)}")
    print(f"Database: {db_path}")

    result = index_repo(args.repo, db_path=db_path, force=args.force)

    print(f"\nDone!")
    print(f"  Files scanned    : {result['scanned']}")
    print(f"  Files indexed    : {result['indexed']}")
    print(f"  Files unchanged  : {result['skipped_unchanged']}")
    print(f"  Errors           : {result['errors']}")
    print(f"  Total files in DB: {result['files']}")
    print(f"  Total symbols    : {result['symbols']}")
    print(f"  Total edges      : {result['edges']}")


if __name__ == "__main__":
    main()

"""
File watcher — monitors a repo directory and re-indexes changed files automatically.

Uses watchdog for OS-level file events + a per-file debounce timer (default 2s)
so rapid saves don't trigger multiple re-indexes of the same file.

Can be used standalone or embedded in server.py via start_watcher().
"""
from __future__ import annotations
import os
import sys
import sqlite3
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

import db as graph_db
from indexer import index_file, should_skip
from parser import detect_language

DEBOUNCE_SECONDS = 2.0


class _ReindexHandler(FileSystemEventHandler):
    def __init__(self, repo_path: str, db_path: str, debounce: float = DEBOUNCE_SECONDS):
        super().__init__()
        self.repo_path = os.path.abspath(repo_path)
        self.db_path = db_path
        self.debounce = debounce
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # watchdog calls these on FS events
    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, action="upsert")

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, action="upsert")

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, action="delete")

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            # old path → delete, new path → index
            self._schedule(event.src_path, action="delete")
            self._schedule(event.dest_path, action="upsert")

    # ── Internal ──────────────────────────────────────────────────────

    def _schedule(self, abs_path: str, action: str):
        """Cancel any pending timer for this path and restart debounce."""
        abs_path = os.path.abspath(abs_path)
        key = abs_path

        with self._lock:
            existing = self._timers.pop(key, None)
            if existing:
                existing.cancel()
            t = threading.Timer(self.debounce, self._process, args=(abs_path, action))
            self._timers[key] = t
            t.start()

    def _process(self, abs_path: str, action: str):
        with self._lock:
            self._timers.pop(abs_path, None)

        rel_path = os.path.relpath(abs_path, self.repo_path).replace("\\", "/")

        if action == "delete":
            conn = graph_db.get_conn(self.db_path)
            graph_db.delete_file_data(conn, rel_path)
            conn.execute("DELETE FROM files WHERE path=?", (rel_path,))
            conn.commit()
            conn.close()
            print(f"[watcher] removed  {rel_path}", flush=True)
            return

        # upsert: only process parseable files
        if not detect_language(abs_path) or should_skip(abs_path):
            return
        if not os.path.isfile(abs_path):
            return

        try:
            conn = graph_db.get_conn(self.db_path)
            changed = index_file(conn, abs_path, rel_path, force=True)
            conn.commit()
            conn.close()
            if changed:
                print(f"[watcher] indexed  {rel_path}", flush=True)
        except Exception as e:
            print(f"[watcher] error    {rel_path}: {e}", file=sys.stderr, flush=True)


def start_watcher(repo_path: str, db_path: str,
                  debounce: float = DEBOUNCE_SECONDS) -> Observer:
    """
    Start background file watcher. Returns the Observer (call .stop() to stop).
    Non-blocking — runs in daemon threads.
    """
    handler = _ReindexHandler(repo_path, db_path, debounce)
    observer = Observer()
    observer.schedule(handler, path=repo_path, recursive=True)
    observer.start()
    print(f"[watcher] watching {os.path.abspath(repo_path)}", flush=True)
    return observer


# ── Standalone CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, time
    ap = argparse.ArgumentParser(description="Watch a repo and auto re-index on file changes.")
    ap.add_argument("repo", help="Path to the repository root")
    ap.add_argument("--db", default=graph_db.DB_PATH, help="SQLite DB path")
    ap.add_argument("--debounce", type=float, default=DEBOUNCE_SECONDS,
                    help="Seconds to wait after last change before re-indexing (default: 2)")
    args = ap.parse_args()

    graph_db.init_db(args.db)
    observer = start_watcher(args.repo, args.db, debounce=args.debounce)
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

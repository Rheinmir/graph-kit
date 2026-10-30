"""watcher._process must close its sqlite connection even when indexing fails.

Before the fix an exception (e.g. "database is locked" with several MCP servers writing one
index.db) left the connection open: 2 fds each (index.db + index.db-wal). Run: python3 tests/test_watcher_fd.py
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# watcher imports watchdog, parser (tree-sitter) and indexer; stub them so the test needs only the stdlib.
wd = types.ModuleType("watchdog"); wo = types.ModuleType("watchdog.observers"); we = types.ModuleType("watchdog.events")
wo.Observer = object
we.FileSystemEventHandler = object
we.FileSystemEvent = object
sys.modules.update({"watchdog": wd, "watchdog.observers": wo, "watchdog.events": we})
idx = types.ModuleType("indexer")
idx.should_skip = lambda p: False
idx.index_file = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked"))
sys.modules["indexer"] = idx
par = types.ModuleType("parser"); par.detect_language = lambda p: "python"
sys.modules["parser"] = par

import db as graph_db  # noqa: E402
import watcher  # noqa: E402


def open_fds():
    return len(os.listdir("/dev/fd"))


class WatcherFd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.db = os.path.join(self.repo, "index.db")
        graph_db.init_db(self.db)
        self.src = os.path.join(self.repo, "a.py")
        Path(self.src).write_text("def f():\n    return 1\n")
        self.h = watcher._ReindexHandler.__new__(watcher._ReindexHandler)
        self.h.repo_path, self.h.db_path = self.repo, self.db
        self.h._timers, self.h._lock = {}, __import__("threading").Lock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_error_closes_connection(self):
        base = open_fds()
        for _ in range(200):
            self.h._process(self.src, "upsert")
        self.assertLessEqual(open_fds() - base, 2)

    def test_delete_error_closes_connection(self):
        orig = graph_db.delete_file_data
        graph_db.delete_file_data = lambda *a: (_ for _ in ()).throw(RuntimeError("database is locked"))
        try:
            base = open_fds()
            for _ in range(200):
                self.h._process(self.src, "delete")
            self.assertLessEqual(open_fds() - base, 2)
        finally:
            graph_db.delete_file_data = orig


if __name__ == "__main__":
    unittest.main()

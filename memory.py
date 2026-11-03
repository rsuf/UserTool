"""
Taken-name memory.

Every name the app finds taken is saved here (per platform). Later scans skip those names,
so the app never spends a request on them again until the user clears the memory.

Stored in a small SQLite file (og_checker_memory.db) in the app's data folder.
"""

import os
import sqlite3
import threading
import time


class Memory:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS taken(
            platform TEXT NOT NULL,
            name     TEXT NOT NULL,
            seen     INTEGER NOT NULL,
            PRIMARY KEY (platform, name)) WITHOUT ROWID""")
        self.db.commit()
        self.pending = {}            # (platform, name) -> time, not yet written
        self.last_flush = time.time()

    # ---- reads ----------------------------------------------------------
    def is_taken(self, platform, name):
        key = (platform, name.lower())
        with self.lock:
            if key in self.pending:
                return True
            return self.db.execute("SELECT 1 FROM taken WHERE platform=? AND name=?", key).fetchone() is not None

    def counts(self):
        self.flush()
        with self.lock:
            rows = self.db.execute("SELECT platform, COUNT(*) FROM taken GROUP BY platform").fetchall()
        return {p: n for p, n in rows}

    # ---- writes ---------------------------------------------------------
    def add(self, platform, name):
        with self.lock:
            self.pending[(platform, name.lower())] = int(time.time())
            big = len(self.pending) >= 500
        if big:
            self.flush()

    def forget(self, name_platform_pairs):
        """Remove specific names (e.g. one that turned out to be free again)."""
        with self.lock:
            for key in name_platform_pairs:
                self.pending.pop(key, None)
            self.db.executemany("DELETE FROM taken WHERE platform=? AND name=?", list(name_platform_pairs))
            self.db.commit()

    def flush(self, force=True):
        with self.lock:
            if not self.pending or (not force and time.time() - self.last_flush < 10):
                return
            rows = [(p, n, t) for (p, n), t in self.pending.items()]
            self.db.executemany("INSERT OR IGNORE INTO taken(platform, name, seen) VALUES (?,?,?)", rows)
            self.db.commit()
            self.pending.clear()
            self.last_flush = time.time()

    def reset(self, platform=None):
        """Forget every remembered name for one platform, or for all of them. Returns how many."""
        self.flush()
        with self.lock:
            if platform:
                n = self.db.execute("SELECT COUNT(*) FROM taken WHERE platform=?", (platform,)).fetchone()[0]
                self.db.execute("DELETE FROM taken WHERE platform=?", (platform,))
            else:
                n = self.db.execute("SELECT COUNT(*) FROM taken").fetchone()[0]
                self.db.execute("DELETE FROM taken")
            self.db.commit()
        return n

    def close(self):
        self.flush()
        with self.lock:
            self.db.close()

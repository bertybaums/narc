"""Cross-process, thread-safe token bucket backed by SQLite.

Why SQLite (and not a Python lock): gunicorn runs the server in multiple
worker processes (currently 2). An in-process bucket would let each worker
independently grant the full per-minute allowance, effectively multiplying
the rate by the worker count. Persisting state in SQLite with an IMMEDIATE
transaction makes the bucket truly global across processes.

Configured via env var MINDROUTER_RATE_LIMIT_PER_MIN (default 95 — under
MindRouter's 100/min ceiling).
"""

import os
import sqlite3
import time

from db import DB_PATH


class SqliteTokenBucket:
    """Token bucket whose state lives in SQLite — one row in rate_limit_state.

    All workers across all processes reading/writing that row coordinate via
    BEGIN IMMEDIATE, so the refill+consume step is atomic.
    """

    def __init__(self, db_path, name, rate_per_min, capacity=None):
        if rate_per_min <= 0:
            raise ValueError("rate_per_min must be positive")
        self.db_path = db_path
        self.name = name
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = float(capacity) if capacity is not None else float(rate_per_min)
        self._ensure_table()

    def _conn(self):
        c = sqlite3.connect(self.db_path, isolation_level=None, timeout=30.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _ensure_table(self):
        c = self._conn()
        try:
            c.execute(
                """CREATE TABLE IF NOT EXISTS rate_limit_state (
                       bucket_name  TEXT PRIMARY KEY,
                       tokens       REAL NOT NULL,
                       last_refill  REAL NOT NULL
                   )"""
            )
        finally:
            c.close()

    def acquire(self, tokens=1):
        """Block until `tokens` are available, then consume them."""
        while True:
            wait_for = self._try_consume(tokens)
            if wait_for is None:
                return
            time.sleep(min(wait_for, 5.0))

    def _try_consume(self, tokens):
        """Atomic refill+consume. Returns None on success, else seconds to wait."""
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT tokens, last_refill FROM rate_limit_state WHERE bucket_name=?",
                (self.name,),
            ).fetchone()
            now = time.time()
            if row is None:
                current = self.capacity
            else:
                stored, last = row
                elapsed = max(0.0, now - last)
                current = min(self.capacity, stored + elapsed * self.rate_per_sec)
            if current >= tokens:
                c.execute(
                    """INSERT INTO rate_limit_state (bucket_name, tokens, last_refill)
                       VALUES (?, ?, ?)
                       ON CONFLICT(bucket_name) DO UPDATE
                       SET tokens=excluded.tokens, last_refill=excluded.last_refill""",
                    (self.name, current - tokens, now),
                )
                c.execute("COMMIT")
                return None
            # Not enough; persist the refilled state and tell caller how long to wait.
            c.execute(
                """INSERT INTO rate_limit_state (bucket_name, tokens, last_refill)
                   VALUES (?, ?, ?)
                   ON CONFLICT(bucket_name) DO UPDATE
                   SET tokens=excluded.tokens, last_refill=excluded.last_refill""",
                (self.name, current, now),
            )
            c.execute("COMMIT")
            return (tokens - current) / self.rate_per_sec
        finally:
            c.close()

    def stats(self):
        c = self._conn()
        try:
            row = c.execute(
                "SELECT tokens, last_refill FROM rate_limit_state WHERE bucket_name=?",
                (self.name,),
            ).fetchone()
        finally:
            c.close()
        now = time.time()
        if row is None:
            return {"tokens_available": self.capacity, "capacity": self.capacity,
                    "rate_per_min": self.rate_per_sec * 60.0}
        stored, last = row
        elapsed = max(0.0, now - last)
        current = min(self.capacity, stored + elapsed * self.rate_per_sec)
        return {"tokens_available": current, "capacity": self.capacity,
                "rate_per_min": self.rate_per_sec * 60.0}


_DEFAULT_RATE = int(os.environ.get("MINDROUTER_RATE_LIMIT_PER_MIN", "95"))
mindrouter_bucket = SqliteTokenBucket(
    db_path=DB_PATH,
    name="mindrouter",
    rate_per_min=_DEFAULT_RATE,
)

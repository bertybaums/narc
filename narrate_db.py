"""SQLite helpers for the Narrate experiment (humans write narratives for a model).

Kept in its own database file, separate from narc.db:
  - it holds human-subject data (participants' own writing, reveal choices),
    which should not ride along with narc.db syncs, merges, and dataset exports;
  - it can be backed up, shared, or deleted on its own schedule;
  - long-running model calls write here, not into the 1 GB benchmark DB.

Puzzles are still read from narc.db; rows here refer to them by puzzle_id and
snapshot what matters (the prompt sent, the reference narrative revealed) so the
record stays interpretable if a puzzle is later edited.

Path: env NARRATE_DB_PATH (default narrate_data/narrate.db). In docker-compose the
whole narrate_data/ directory is bind-mounted so the WAL files live on the host.
"""

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = os.environ.get("NARRATE_DB_PATH", "narrate_data/narrate.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invite_codes (
    code              TEXT PRIMARY KEY,          -- normalized: uppercase, no dashes/spaces
    display_code      TEXT NOT NULL,             -- as handed out, e.g. KX7P-M2QD-9RWT
    label             TEXT,                      -- cohort, e.g. 'lab pilot Sep 2026'
    max_participants  INTEGER,                   -- NULL = unlimited
    active            INTEGER NOT NULL DEFAULT 1,
    expires_at        TIMESTAMP,                 -- NULL = never (UTC, 'YYYY-MM-DD HH:MM:SS')
    created_by        TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS participants (
    participant_id   TEXT PRIMARY KEY,           -- random public id (safe to export)
    token_hash       TEXT NOT NULL UNIQUE,       -- sha256 of the browser cookie token
    invite_code      TEXT REFERENCES invite_codes(code),
    staff_username   TEXT,                       -- set when a logged-in owner/reviewer joined
    acknowledged_at  TIMESTAMP,                  -- agreed that every submission is recorded
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Every interaction with the "show me the existing narrative" control.
-- action: 'opened' (dialog shown) | 'cancelled' | 'confirmed' (narrative shown)
CREATE TABLE IF NOT EXISTS reveal_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id  TEXT NOT NULL REFERENCES participants(participant_id),
    puzzle_id       TEXT NOT NULL,
    action          TEXT NOT NULL CHECK(action IN ('opened', 'cancelled', 'confirmed')),
    reference_text  TEXT,                        -- snapshot of what was shown (confirmed only)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reveal_participant ON reveal_events(participant_id, puzzle_id);

-- One row per submitted narrative. saw_reference is derived server-side from
-- reveal_events at submit time, never taken from the client.
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id    TEXT NOT NULL REFERENCES participants(participant_id),
    puzzle_id         TEXT NOT NULL,
    seq_num           INTEGER NOT NULL,          -- 1, 2, 3... per (participant, puzzle)
    narrative         TEXT NOT NULL,
    saw_reference     INTEGER NOT NULL,          -- 1 if reference was revealed before submit
    client_elapsed_ms INTEGER,                   -- client-reported drafting time
    model_name        TEXT NOT NULL,
    masked_positions  TEXT NOT NULL,             -- JSON, as graded
    prompt_json       TEXT NOT NULL,             -- exact messages sent (pass 1)
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK(status IN ('queued', 'running', 'done', 'failed')),
    runner_pid        INTEGER,                   -- web worker process that owns the run
    raw_response      TEXT,
    reasoning         TEXT,
    extraction_text   TEXT,
    predicted_grids   TEXT,                      -- JSON dict keyed by position
    correct           INTEGER,
    cell_accuracy     REAL,
    parse_error       TEXT,                      -- model answered but no grid could be read
    error             TEXT,                      -- the run itself failed (transport, restart)
    latency_ms        INTEGER,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    UNIQUE(participant_id, puzzle_id, seq_num)
);

CREATE INDEX IF NOT EXISTS idx_attempts_participant ON attempts(participant_id, puzzle_id);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);

-- Staff runs of the reference narrative through the exact participant pipeline.
-- A puzzle is offered to participants only once its current reference text has
-- at least one exact solve here, so "a narrative that works exists" stays true.
CREATE TABLE IF NOT EXISTS reference_checks (
    check_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id        TEXT NOT NULL,
    reference_text   TEXT NOT NULL,
    model_name       TEXT NOT NULL,
    correct          INTEGER,
    cell_accuracy    REAL,
    parse_error      TEXT,
    error            TEXT,
    latency_ms       INTEGER,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reference_checks_puzzle ON reference_checks(puzzle_id);

CREATE TABLE IF NOT EXISTS join_failures (
    failure_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Unambiguous alphabet for generated invite codes (no 0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def connect(path=None):
    path = path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_code(code):
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


# --- invite codes ---

def create_invite(conn, label=None, max_participants=None, expires_at=None,
                  code=None, created_by=None):
    """Create an invite code. Returns the display code. A custom `code` is allowed
    (memorable cohort codes); otherwise a random 12-character code is generated."""
    if code:
        display = code.strip().upper()
    else:
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
        display = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"
    norm = normalize_code(display)
    if not norm:
        raise ValueError("Invite code must contain letters or digits")
    conn.execute(
        """INSERT INTO invite_codes (code, display_code, label, max_participants,
                                     expires_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (norm, display, label, max_participants, expires_at, created_by),
    )
    conn.commit()
    return display


def get_invites(conn):
    return conn.execute(
        """SELECT i.*,
                  (SELECT COUNT(*) FROM participants p WHERE p.invite_code = i.code) AS participants,
                  (SELECT COUNT(*) FROM attempts a JOIN participants p USING (participant_id)
                    WHERE p.invite_code = i.code) AS attempts
           FROM invite_codes i ORDER BY i.created_at DESC"""
    ).fetchall()


def set_invite_active(conn, code, active):
    cur = conn.execute("UPDATE invite_codes SET active=? WHERE code=?",
                       (1 if active else 0, normalize_code(code)))
    conn.commit()
    return cur.rowcount


def check_invite(conn, code):
    """Return (invite_row, None) if the code can admit a new participant,
    else (None, reason)."""
    row = conn.execute(
        "SELECT * FROM invite_codes WHERE code=?", (normalize_code(code),)
    ).fetchone()
    if not row or not row["active"]:
        return None, "That invite code isn't valid."
    if row["expires_at"]:
        expired = conn.execute(
            "SELECT datetime('now') > datetime(?)", (row["expires_at"],)
        ).fetchone()[0]
        if expired:
            return None, "That invite code has expired."
    if row["max_participants"] is not None:
        n = conn.execute("SELECT COUNT(*) FROM participants WHERE invite_code=?",
                         (row["code"],)).fetchone()[0]
        if n >= row["max_participants"]:
            return None, "That invite code has already been used by its full group."
    return row, None


def record_join_failure(conn):
    conn.execute("INSERT INTO join_failures DEFAULT VALUES")
    conn.execute("DELETE FROM join_failures WHERE created_at < datetime('now', '-1 day')")
    conn.commit()


def recent_join_failures(conn, minutes=10):
    return conn.execute(
        "SELECT COUNT(*) FROM join_failures WHERE created_at >= datetime('now', ?)",
        (f"-{int(minutes)} minutes",),
    ).fetchone()[0]


# --- participants ---

def create_participant(conn, invite_code=None, staff_username=None):
    """Create a participant. Returns (participant_id, cookie_token)."""
    participant_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO participants (participant_id, token_hash, invite_code, staff_username)
           VALUES (?, ?, ?, ?)""",
        (participant_id, hash_token(token), invite_code, staff_username),
    )
    conn.commit()
    return participant_id, token


def get_participant_by_token(conn, token):
    if not token:
        return None
    return conn.execute("SELECT * FROM participants WHERE token_hash=?",
                        (hash_token(token),)).fetchone()


def acknowledge(conn, participant_id):
    conn.execute(
        """UPDATE participants SET acknowledged_at=CURRENT_TIMESTAMP
           WHERE participant_id=? AND acknowledged_at IS NULL""",
        (participant_id,),
    )
    conn.commit()


# --- reveals ---

def record_reveal(conn, participant_id, puzzle_id, action, reference_text=None):
    conn.execute(
        """INSERT INTO reveal_events (participant_id, puzzle_id, action, reference_text)
           VALUES (?, ?, ?, ?)""",
        (participant_id, puzzle_id, action, reference_text),
    )
    conn.commit()


def has_revealed(conn, participant_id, puzzle_id):
    return conn.execute(
        """SELECT 1 FROM reveal_events
           WHERE participant_id=? AND puzzle_id=? AND action='confirmed' LIMIT 1""",
        (participant_id, puzzle_id),
    ).fetchone() is not None


# --- attempts ---

def create_attempt(conn, participant_id, puzzle_id, narrative, model_name,
                   masked_positions_json, prompt_json, client_elapsed_ms=None,
                   runner_pid=None):
    """Insert a queued attempt; seq_num and saw_reference are computed in the same
    transaction so they can't race a concurrent submit or reveal."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq_num), 0) + 1 FROM attempts WHERE participant_id=? AND puzzle_id=?",
            (participant_id, puzzle_id),
        ).fetchone()[0]
        saw = 1 if has_revealed(conn, participant_id, puzzle_id) else 0
        cur = conn.execute(
            """INSERT INTO attempts (participant_id, puzzle_id, seq_num, narrative,
                   saw_reference, client_elapsed_ms, model_name, masked_positions, prompt_json,
                   runner_pid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (participant_id, puzzle_id, seq, narrative, saw, client_elapsed_ms,
             model_name, masked_positions_json, prompt_json, runner_pid),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cur.lastrowid


def get_attempt(conn, attempt_id):
    return conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def get_attempts(conn, participant_id, puzzle_id=None):
    if puzzle_id:
        return conn.execute(
            "SELECT * FROM attempts WHERE participant_id=? AND puzzle_id=? ORDER BY seq_num",
            (participant_id, puzzle_id),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM attempts WHERE participant_id=? ORDER BY created_at",
        (participant_id,),
    ).fetchall()


def mark_attempt_running(conn, attempt_id, pid):
    conn.execute(
        """UPDATE attempts SET status='running', started_at=CURRENT_TIMESTAMP, runner_pid=?
           WHERE attempt_id=?""",
        (pid, attempt_id),
    )
    conn.commit()


def finish_attempt(conn, attempt_id, *, raw_response=None, reasoning=None,
                   extraction_text=None, predicted_grids=None, correct=None,
                   cell_accuracy=None, parse_error=None, error=None, latency_ms=None):
    status = "failed" if error else "done"
    conn.execute(
        """UPDATE attempts SET status=?, raw_response=?, reasoning=?, extraction_text=?,
               predicted_grids=?, correct=?, cell_accuracy=?, parse_error=?, error=?,
               latency_ms=?, finished_at=CURRENT_TIMESTAMP
           WHERE attempt_id=?""",
        (status, raw_response, reasoning, extraction_text, predicted_grids, correct,
         cell_accuracy, parse_error, error, latency_ms, attempt_id),
    )
    conn.commit()


def fail_attempt(conn, attempt_id, error):
    conn.execute(
        """UPDATE attempts SET status='failed', error=?, finished_at=CURRENT_TIMESTAMP
           WHERE attempt_id=? AND status IN ('queued', 'running')""",
        (error, attempt_id),
    )
    conn.commit()


def count_active_attempts(conn, participant_id=None):
    if participant_id:
        return conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE participant_id=? AND status IN ('queued', 'running')",
            (participant_id,),
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE status IN ('queued', 'running')"
    ).fetchone()[0]


def count_recent_attempts(conn, participant_id, hours):
    """Attempts that used (or are using) the model in the window. Failed runs
    don't count: an outage shouldn't burn a participant's allowance."""
    return conn.execute(
        """SELECT COUNT(*) FROM attempts
           WHERE participant_id=? AND status != 'failed'
             AND created_at >= datetime('now', ?)""",
        (participant_id, f"-{int(hours)} hours"),
    ).fetchone()[0]


# --- reference checks ---

def insert_reference_check(conn, puzzle_id, reference_text, model_name, correct,
                           cell_accuracy, parse_error=None, error=None, latency_ms=None):
    conn.execute(
        """INSERT INTO reference_checks (puzzle_id, reference_text, model_name, correct,
               cell_accuracy, parse_error, error, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, reference_text, model_name, correct, cell_accuracy,
         parse_error, error, latency_ms),
    )
    conn.commit()


def reference_status(conn, puzzle_id, reference_text, model_name):
    """(solved, total) over completed checks of this exact reference text."""
    row = conn.execute(
        """SELECT COALESCE(SUM(correct), 0), COUNT(*) FROM reference_checks
           WHERE puzzle_id=? AND reference_text=? AND model_name=? AND error IS NULL""",
        (puzzle_id, reference_text, model_name),
    ).fetchone()
    return row[0], row[1]

"""SQLite helpers for the NARC pipeline."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DB_PATH = "narc.db"


def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_PATH.read_text())
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn):
    """Idempotent migrations for schema changes that CREATE TABLE IF NOT EXISTS
    can't apply to existing tables (e.g. CHECK constraint changes)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if row and "'collaborator'" not in row[0]:
        conn.executescript("""
            BEGIN;
            CREATE TABLE users_new (
                user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK(role IN ('owner', 'reviewer', 'collaborator')),
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO users_new (user_id, username, password_hash, role, created_at)
                SELECT user_id, username, password_hash, role, created_at FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            COMMIT;
        """)

    _migrate_mask_variant_id(conn)
    _migrate_narc_strength(conn)


def _has_column(conn, table, column):
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == column for c in cols)


def _migrate_mask_variant_id(conn):
    """Add mask_variant_id to trials and classifications and fold it into their
    UNIQUE / PRIMARY KEY. SQLite can't ALTER a UNIQUE/PK, so rebuild each table.
    Guarded by column presence — runs once, then no-ops. New rows get NULL until
    migrate_mask_variants.py backfills them to each puzzle's 'original' mask."""
    if not _has_column(conn, "trials", "mask_variant_id"):
        conn.executescript("""
            BEGIN;
            CREATE TABLE trials_new (
                trial_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
                variant_id     INTEGER REFERENCES narrative_variants(variant_id),
                mask_variant_id INTEGER REFERENCES mask_variants(mask_variant_id),
                model_name     TEXT NOT NULL,
                condition      TEXT NOT NULL,
                repeat_num     INTEGER DEFAULT 1,
                prompt_text    TEXT NOT NULL,
                raw_response   TEXT,
                response_text  TEXT,
                response_at    TEXT,
                latency_ms     INTEGER,
                error          TEXT,
                predicted_grids TEXT,
                reasoning      TEXT,
                correct        INTEGER,
                cell_accuracy  REAL,
                UNIQUE(puzzle_id, variant_id, mask_variant_id, model_name, condition, repeat_num)
            );
            INSERT INTO trials_new
                (trial_id, puzzle_id, variant_id, model_name, condition, repeat_num,
                 prompt_text, raw_response, response_text, response_at, latency_ms,
                 error, predicted_grids, reasoning, correct, cell_accuracy)
                SELECT trial_id, puzzle_id, variant_id, model_name, condition, repeat_num,
                       prompt_text, raw_response, response_text, response_at, latency_ms,
                       error, predicted_grids, reasoning, correct, cell_accuracy
                FROM trials;
            DROP TABLE trials;
            ALTER TABLE trials_new RENAME TO trials;
            COMMIT;
        """)

    if not _has_column(conn, "classifications", "mask_variant_id"):
        conn.executescript("""
            BEGIN;
            CREATE TABLE classifications_new (
                puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
                variant_id     INTEGER REFERENCES narrative_variants(variant_id),
                mask_variant_id INTEGER REFERENCES mask_variants(mask_variant_id),
                model_name     TEXT NOT NULL,
                grids_only     INTEGER,
                narrative_only INTEGER,
                both           INTEGER,
                has_narc       INTEGER,
                PRIMARY KEY (puzzle_id, variant_id, mask_variant_id, model_name)
            );
            INSERT INTO classifications_new
                (puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc)
                SELECT puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc
                FROM classifications;
            DROP TABLE classifications;
            ALTER TABLE classifications_new RENAME TO classifications;
            COMMIT;
        """)


def _migrate_narc_strength(conn):
    """Add order-sensitivity columns to classifications. These are not part of the
    PRIMARY KEY, so a plain ADD COLUMN suffices (no table rebuild). Guarded by
    column presence — runs once, then no-ops. Populated by classify.py from the
    'both_shuffled' trials produced by collect.run_sensitivity_job."""
    if not _has_column(conn, "classifications", "narc_strength"):
        conn.executescript("""
            ALTER TABLE classifications ADD COLUMN narc_strength  TEXT;
            ALTER TABLE classifications ADD COLUMN shuffle_solved INTEGER;
            ALTER TABLE classifications ADD COLUMN shuffle_total  INTEGER;
        """)


# --- puzzles ---

def upsert_puzzle(conn, puzzle_id, title, narrative, sequence_json,
                  masked_positions, answer_grids,
                  creator='human', difficulty=None, tags=None,
                  human_difficulty=None, ai_difficulty=None,
                  parent_puzzle_id=None, stance_group=None, stance=None):
    conn.execute(
        """INSERT OR REPLACE INTO puzzles
           (puzzle_id, title, narrative, sequence_json, masked_positions,
            answer_grids, creator, difficulty, human_difficulty, ai_difficulty, tags,
            parent_puzzle_id, stance_group, stance)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, title, narrative, sequence_json, masked_positions,
         answer_grids, creator, difficulty, human_difficulty, ai_difficulty, tags,
         parent_puzzle_id, stance_group, stance),
    )
    conn.commit()


def get_puzzle(conn, puzzle_id):
    return conn.execute(
        "SELECT * FROM puzzles WHERE puzzle_id=?", (puzzle_id,)
    ).fetchone()


def puzzle_exists(conn, puzzle_id):
    """True if the ID is taken in the puzzles table OR is reserved by a pending submission."""
    row = conn.execute("SELECT 1 FROM puzzles WHERE puzzle_id=? LIMIT 1", (puzzle_id,)).fetchone()
    if row:
        return True
    row = conn.execute(
        """SELECT 1 FROM submissions
           WHERE status='pending'
             AND submission_type IN ('new_puzzle', 'revision')
             AND json_extract(payload_json, '$.puzzle_id') = ?
           LIMIT 1""",
        (puzzle_id,),
    ).fetchone()
    return row is not None


def next_available_puzzle_id(conn, prefix="sub"):
    """Return prefix_NNN with N = (max existing N for that prefix) + 1.
    Considers both the puzzles table and pending submissions.
    """
    rows = conn.execute(
        f"""SELECT puzzle_id FROM puzzles WHERE puzzle_id LIKE ?
            UNION ALL
            SELECT json_extract(payload_json, '$.puzzle_id') FROM submissions
             WHERE status='pending'
               AND submission_type IN ('new_puzzle', 'revision')
               AND json_extract(payload_json, '$.puzzle_id') LIKE ?""",
        (f"{prefix}_%", f"{prefix}_%"),
    ).fetchall()
    max_n = 0
    plen = len(prefix) + 1  # +1 for the underscore
    for r in rows:
        pid = r[0] or ""
        suffix = pid[plen:]
        if suffix.isdigit():
            n = int(suffix)
            if n > max_n:
                max_n = n
    return f"{prefix}_{max_n + 1:03d}"


def get_all_puzzles(conn):
    return conn.execute(
        "SELECT * FROM puzzles ORDER BY created_at"
    ).fetchall()


def delete_puzzle(conn, puzzle_id):
    conn.execute("DELETE FROM puzzles WHERE puzzle_id=?", (puzzle_id,))
    conn.commit()


def set_puzzle_status(conn, puzzle_id, status):
    """status: 'draft' | 'active' | 'featured'"""
    if status not in ("draft", "active", "featured"):
        raise ValueError(f"Invalid status: {status}")
    conn.execute("UPDATE puzzles SET status=? WHERE puzzle_id=?", (status, puzzle_id))
    conn.commit()


def set_puzzle_tags(conn, puzzle_id, tags):
    """tags: comma-separated string or None. Lifecycle tags (draft:, featured:) are
    silently stripped — use set_puzzle_status for those."""
    if tags is None:
        clean = None
    else:
        parts = [t.strip() for t in tags.split(",") if t.strip()]
        parts = [t for t in parts
                 if not t.startswith("draft:") and not t.startswith("featured:")]
        clean = ",".join(parts) if parts else None
    conn.execute("UPDATE puzzles SET tags=? WHERE puzzle_id=?", (clean, puzzle_id))
    conn.commit()
    return clean


def puzzle_to_json(row):
    """Convert a puzzle DB row to a JSON-serializable dict."""
    masked_positions = json.loads(row["masked_positions"])
    answer_grids = json.loads(row["answer_grids"])
    return {
        "puzzle_id": row["puzzle_id"],
        "title": row["title"],
        "narrative": row["narrative"],
        "sequence": json.loads(row["sequence_json"]),
        "masked_positions": masked_positions,
        "answer_grids": answer_grids,
        "creator": row["creator"],
        "difficulty": row["difficulty"],
        "human_difficulty": row["human_difficulty"],
        "ai_difficulty": row["ai_difficulty"],
        "tags": row["tags"],
        "parent_puzzle_id": row["parent_puzzle_id"],
        "stance_group": row["stance_group"],
        "stance": row["stance"],
        "status": row["status"] if "status" in row.keys() else "draft",
        "created_at": row["created_at"],
    }


# --- narrative variants ---

def upsert_variant(conn, puzzle_id, variant, narrative, source_domain=None,
                   generator='human'):
    conn.execute(
        """INSERT OR REPLACE INTO narrative_variants
           (puzzle_id, variant, source_domain, narrative, generator)
           VALUES (?, ?, ?, ?, ?)""",
        (puzzle_id, variant, source_domain, narrative, generator),
    )
    conn.commit()
    row = conn.execute(
        "SELECT variant_id FROM narrative_variants WHERE puzzle_id=? AND variant=?",
        (puzzle_id, variant),
    ).fetchone()
    return row["variant_id"] if row else None


def get_variants(conn, puzzle_id):
    return conn.execute(
        """SELECT * FROM narrative_variants WHERE puzzle_id=?
           ORDER BY CASE WHEN variant='original' THEN 0 ELSE 1 END, variant""",
        (puzzle_id,),
    ).fetchall()


# --- mask variants ---

def upsert_mask_variant(conn, puzzle_id, label, masked_positions):
    """masked_positions: list of ints (stored as JSON). Returns mask_variant_id."""
    if not isinstance(masked_positions, str):
        masked_positions = json.dumps(masked_positions)
    conn.execute(
        """INSERT INTO mask_variants (puzzle_id, label, masked_positions)
           VALUES (?, ?, ?)
           ON CONFLICT(puzzle_id, label)
           DO UPDATE SET masked_positions=excluded.masked_positions""",
        (puzzle_id, label, masked_positions),
    )
    conn.commit()
    row = conn.execute(
        "SELECT mask_variant_id FROM mask_variants WHERE puzzle_id=? AND label=?",
        (puzzle_id, label),
    ).fetchone()
    return row["mask_variant_id"] if row else None


def get_mask_variants(conn, puzzle_id):
    return conn.execute(
        """SELECT * FROM mask_variants WHERE puzzle_id=?
           ORDER BY CASE WHEN label='original' THEN 0 ELSE 1 END, mask_variant_id""",
        (puzzle_id,),
    ).fetchall()


def get_mask_variant(conn, mask_variant_id):
    return conn.execute(
        "SELECT * FROM mask_variants WHERE mask_variant_id=?", (mask_variant_id,)
    ).fetchone()


def get_original_mask_variant_id(conn, puzzle_id):
    row = conn.execute(
        "SELECT mask_variant_id FROM mask_variants WHERE puzzle_id=? AND label='original'",
        (puzzle_id,),
    ).fetchone()
    return row["mask_variant_id"] if row else None


def delete_mask_variant(conn, mask_variant_id):
    """Delete a mask variant and any test-matrix pairs referencing it."""
    conn.execute("DELETE FROM variant_pairs WHERE mask_variant_id=?", (mask_variant_id,))
    conn.execute("DELETE FROM mask_variants WHERE mask_variant_id=?", (mask_variant_id,))
    conn.commit()


# --- variant pairs (test matrix) ---

def set_variant_pair(conn, puzzle_id, variant_id, mask_variant_id, enabled=1):
    conn.execute(
        """INSERT INTO variant_pairs (puzzle_id, variant_id, mask_variant_id, enabled)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(variant_id, mask_variant_id)
           DO UPDATE SET enabled=excluded.enabled""",
        (puzzle_id, variant_id, mask_variant_id, 1 if enabled else 0),
    )
    conn.commit()


def get_variant_pairs(conn, puzzle_id):
    return conn.execute(
        "SELECT * FROM variant_pairs WHERE puzzle_id=?", (puzzle_id,)
    ).fetchall()


def get_enabled_pairs(conn, puzzle_id):
    """Enabled (narrative variant x mask variant) cells joined with their labels
    and payloads. Used by collect to decide what to run."""
    return conn.execute(
        """SELECT vp.pair_id, vp.variant_id, vp.mask_variant_id,
                  nv.variant AS narrative_label, nv.narrative,
                  mv.label AS mask_label, mv.masked_positions
           FROM variant_pairs vp
           JOIN narrative_variants nv ON nv.variant_id = vp.variant_id
           JOIN mask_variants mv ON mv.mask_variant_id = vp.mask_variant_id
           WHERE vp.puzzle_id=? AND vp.enabled=1
           ORDER BY vp.mask_variant_id, vp.variant_id""",
        (puzzle_id,),
    ).fetchall()


# --- trials ---

def insert_trial(conn, puzzle_id, model_name, condition, prompt_text,
                 variant_id=None, repeat_num=1, mask_variant_id=None):
    conn.execute(
        """INSERT OR IGNORE INTO trials
           (puzzle_id, variant_id, mask_variant_id, model_name, condition,
            repeat_num, prompt_text)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, variant_id, mask_variant_id, model_name, condition,
         repeat_num, prompt_text),
    )
    conn.commit()
    # NULL-safe lookup: variant_id and mask_variant_id may each be NULL.
    conds = ["puzzle_id=?", "model_name=?", "condition=?", "repeat_num=?"]
    params = [puzzle_id, model_name, condition, repeat_num]
    if variant_id is None:
        conds.append("variant_id IS NULL")
    else:
        conds.append("variant_id=?"); params.append(variant_id)
    if mask_variant_id is None:
        conds.append("mask_variant_id IS NULL")
    else:
        conds.append("mask_variant_id=?"); params.append(mask_variant_id)
    row = conn.execute(
        "SELECT trial_id FROM trials WHERE " + " AND ".join(conds), tuple(params)
    ).fetchone()
    return row["trial_id"]


def update_trial_response(conn, trial_id, raw_response, response_text,
                          latency_ms, error=None):
    conn.execute(
        """UPDATE trials
           SET raw_response=?, response_text=?, latency_ms=?, error=?,
               response_at=datetime('now')
           WHERE trial_id=?""",
        (raw_response, response_text, latency_ms, error, trial_id),
    )
    conn.commit()


def update_trial_evaluation(conn, trial_id, predicted_grids, reasoning,
                            correct, cell_accuracy):
    conn.execute(
        """UPDATE trials
           SET predicted_grids=?, reasoning=?, correct=?, cell_accuracy=?
           WHERE trial_id=?""",
        (predicted_grids, reasoning, correct, cell_accuracy, trial_id),
    )
    conn.commit()


def get_pending_trials(conn, model_name=None, condition=None):
    sql = "SELECT * FROM trials WHERE response_text IS NULL AND error IS NULL"
    params = []
    if model_name:
        sql += " AND model_name=?"
        params.append(model_name)
    if condition:
        sql += " AND condition=?"
        params.append(condition)
    return conn.execute(sql, tuple(params)).fetchall()


def get_trials(conn, puzzle_id, model_name=None, variant_id=None):
    sql = "SELECT * FROM trials WHERE puzzle_id=?"
    params = [puzzle_id]
    if model_name:
        sql += " AND model_name=?"
        params.append(model_name)
    if variant_id is not None:
        sql += " AND variant_id=?"
        params.append(variant_id)
    return conn.execute(sql, tuple(params)).fetchall()


# --- classifications ---

def upsert_classification(conn, puzzle_id, model_name, grids_only,
                          narrative_only, both, has_narc, variant_id=None,
                          mask_variant_id=None, narc_strength=None,
                          shuffle_solved=None, shuffle_total=None):
    # SQLite treats NULL key values as distinct in UNIQUE/PK constraints, so
    # INSERT OR REPLACE doesn't dedupe. Delete first to guarantee uniqueness.
    conds = ["puzzle_id=?", "model_name=?"]
    params = [puzzle_id, model_name]
    if variant_id is None:
        conds.append("variant_id IS NULL")
    else:
        conds.append("variant_id=?"); params.append(variant_id)
    if mask_variant_id is None:
        conds.append("mask_variant_id IS NULL")
    else:
        conds.append("mask_variant_id=?"); params.append(mask_variant_id)
    conn.execute(
        "DELETE FROM classifications WHERE " + " AND ".join(conds), tuple(params)
    )
    conn.execute(
        """INSERT INTO classifications
           (puzzle_id, variant_id, mask_variant_id, model_name,
            grids_only, narrative_only, both, has_narc,
            narc_strength, shuffle_solved, shuffle_total)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, variant_id, mask_variant_id, model_name,
         grids_only, narrative_only, both, has_narc,
         narc_strength, shuffle_solved, shuffle_total),
    )
    conn.commit()


# --- solve attempts ---

def insert_solve_attempt(conn, puzzle_id, session_id, phase, saw_narrative,
                         submitted_grids, correct, cell_accuracy,
                         time_spent_ms=None, solver_name=None, skipped_phase1=0,
                         active_variant=None):
    conn.execute(
        """INSERT INTO solve_attempts
           (puzzle_id, session_id, solver_name, phase, saw_narrative,
            submitted_grids, correct, cell_accuracy, time_spent_ms, skipped_phase1,
            active_variant)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, session_id, solver_name, phase, saw_narrative,
         submitted_grids, correct, cell_accuracy, time_spent_ms, skipped_phase1,
         active_variant),
    )
    conn.commit()


def insert_variant_view(conn, session_id, puzzle_id, variant):
    conn.execute(
        """INSERT INTO variant_views (session_id, puzzle_id, variant)
           VALUES (?, ?, ?)""",
        (session_id, puzzle_id, variant),
    )
    conn.commit()


def insert_solve_events(conn, session_id, puzzle_id, events):
    """Batch-insert solve events. Each event is {type, payload, client_ms}."""
    rows = []
    for ev in events:
        payload = ev.get("payload")
        payload_json = json.dumps(payload) if payload is not None else None
        rows.append((
            session_id,
            puzzle_id,
            ev.get("type", "unknown"),
            payload_json,
            ev.get("client_ms"),
        ))
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO solve_events
           (session_id, puzzle_id, event_type, payload, client_ms)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


# --- votes ---

def upsert_vote(conn, puzzle_id, voter_id, value, ip_address=None):
    conn.execute(
        """INSERT INTO votes (puzzle_id, voter_id, value, ip_address)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(puzzle_id, voter_id)
           DO UPDATE SET value=excluded.value, ip_address=excluded.ip_address,
                         created_at=CURRENT_TIMESTAMP""",
        (puzzle_id, voter_id, value, ip_address),
    )
    conn.commit()


def delete_vote(conn, puzzle_id, voter_id):
    conn.execute(
        "DELETE FROM votes WHERE puzzle_id=? AND voter_id=?",
        (puzzle_id, voter_id),
    )
    conn.commit()


def get_vote_counts(conn):
    rows = conn.execute(
        """SELECT puzzle_id,
                  SUM(CASE WHEN value=1 THEN 1 ELSE 0 END) as up,
                  SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END) as down,
                  SUM(value) as net
           FROM votes GROUP BY puzzle_id"""
    ).fetchall()
    return {r["puzzle_id"]: {"up": r["up"], "down": r["down"], "net": r["net"]}
            for r in rows}


def get_voter_votes(conn, voter_id):
    rows = conn.execute(
        "SELECT puzzle_id, value FROM votes WHERE voter_id=?",
        (voter_id,),
    ).fetchall()
    return {r["puzzle_id"]: r["value"] for r in rows}


def get_puzzle_solve_stats(conn):
    rows = conn.execute(
        """SELECT puzzle_id,
                  COUNT(*) as total_attempts,
                  SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) as correct_count,
                  CASE WHEN COUNT(*)>0
                       THEN CAST(SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) AS REAL)/COUNT(*)
                       ELSE NULL END as solve_rate,
                  SUM(CASE WHEN phase=1 AND correct=1 THEN 1 ELSE 0 END) as phase1_correct,
                  SUM(CASE WHEN phase=1 THEN 1 ELSE 0 END) as phase1_total,
                  SUM(CASE WHEN phase=2 AND correct=1 THEN 1 ELSE 0 END) as phase2_correct,
                  SUM(CASE WHEN phase=2 THEN 1 ELSE 0 END) as phase2_total
           FROM solve_attempts
           WHERE skipped_phase1=0
           GROUP BY puzzle_id"""
    ).fetchall()
    stats = {}
    for r in rows:
        p1_rate = r["phase1_correct"] / r["phase1_total"] if r["phase1_total"] else None
        p2_rate = r["phase2_correct"] / r["phase2_total"] if r["phase2_total"] else None
        narrative_lift = None
        if p1_rate is not None and p2_rate is not None:
            narrative_lift = p2_rate - p1_rate
        stats[r["puzzle_id"]] = {
            "attempts": r["total_attempts"],
            "correct": r["correct_count"],
            "solve_rate": r["solve_rate"],
            "narrative_lift": narrative_lift,
        }
    return stats


def count_recent_votes_by_ip(conn, ip_address):
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM votes
           WHERE ip_address=? AND created_at > datetime('now', '-1 hour')""",
        (ip_address,),
    ).fetchone()
    return row["cnt"]


def get_puzzle_vote_counts(conn, puzzle_id):
    row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN value=1 THEN 1 ELSE 0 END), 0) as up,
                  COALESCE(SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END), 0) as down
           FROM votes WHERE puzzle_id=?""",
        (puzzle_id,),
    ).fetchone()
    return {"up": row["up"], "down": row["down"]}


# --- users ---

def create_user(conn, username, password_hash, role):
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, password_hash, role),
    )
    conn.commit()


def get_user_by_username(conn, username):
    return conn.execute(
        "SELECT * FROM users WHERE username=?", (username,)
    ).fetchone()


def get_user_by_id(conn, user_id):
    return conn.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()


def get_all_users(conn):
    return conn.execute(
        "SELECT user_id, username, role, created_at FROM users ORDER BY created_at"
    ).fetchall()


def delete_user(conn, user_id):
    conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()


def update_user_password(conn, user_id, password_hash):
    conn.execute(
        "UPDATE users SET password_hash=? WHERE user_id=?",
        (password_hash, user_id),
    )
    conn.commit()


def update_user_role(conn, user_id, role):
    if role not in ("owner", "reviewer", "collaborator"):
        raise ValueError(f"Invalid role: {role}")
    conn.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))
    conn.commit()


def count_owners(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='owner'"
    ).fetchone()[0]


# --- submissions ---

def create_submission(conn, submission_type, payload_json, target_puzzle_id=None,
                      submitter_name=None, submitter_email=None):
    conn.execute(
        """INSERT INTO submissions
           (submission_type, payload_json, target_puzzle_id, submitter_name, submitter_email)
           VALUES (?, ?, ?, ?, ?)""",
        (submission_type, payload_json, target_puzzle_id, submitter_name, submitter_email),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_submission(conn, submission_id):
    return conn.execute(
        "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
    ).fetchone()


def get_submissions(conn, status=None):
    if status:
        return conn.execute(
            "SELECT * FROM submissions WHERE status=? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM submissions ORDER BY created_at DESC"
    ).fetchall()


def update_submission_payload(conn, submission_id, payload_json):
    conn.execute(
        "UPDATE submissions SET payload_json=? WHERE submission_id=?",
        (payload_json, submission_id),
    )
    conn.commit()


def review_submission(conn, submission_id, status, reviewer_id, review_note=None):
    conn.execute(
        """UPDATE submissions
           SET status=?, reviewer_id=?, review_note=?, reviewed_at=datetime('now')
           WHERE submission_id=?""",
        (status, reviewer_id, review_note, submission_id),
    )
    conn.commit()


# --- activity log ---

def log_activity(conn, user_id, action, target_type=None, target_id=None,
                 detail=None, snapshot_json=None):
    conn.execute(
        """INSERT INTO activity_log
           (user_id, action, target_type, target_id, detail, snapshot_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, action, target_type, target_id, detail, snapshot_json),
    )
    conn.commit()


def get_recent_activity(conn, limit=50):
    return conn.execute(
        """SELECT a.*, u.username
           FROM activity_log a
           LEFT JOIN users u ON a.user_id = u.user_id
           ORDER BY a.created_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()


def get_activity_entry(conn, log_id):
    return conn.execute(
        "SELECT * FROM activity_log WHERE log_id=?", (log_id,)
    ).fetchone()


def update_puzzle_creator(conn, puzzle_id, creator):
    conn.execute(
        "UPDATE puzzles SET creator=? WHERE puzzle_id=?",
        (creator, puzzle_id),
    )
    conn.commit()


def delete_variant(conn, puzzle_id, variant):
    conn.execute(
        "DELETE FROM narrative_variants WHERE puzzle_id=? AND variant=?",
        (puzzle_id, variant),
    )
    conn.commit()


# --- review jobs ---

def create_review_job(conn, puzzle_id, model_name, created_by, log_path):
    cur = conn.execute(
        """INSERT INTO review_jobs (puzzle_id, model_name, created_by, log_path)
           VALUES (?, ?, ?, ?)""",
        (puzzle_id, model_name, created_by, log_path),
    )
    conn.commit()
    return cur.lastrowid


def set_review_job_status(conn, job_id, status, error=None):
    now = datetime.utcnow().isoformat()
    if status == "running":
        conn.execute(
            "UPDATE review_jobs SET status=?, started_at=? WHERE job_id=?",
            (status, now, job_id),
        )
    elif status in ("done", "failed"):
        conn.execute(
            "UPDATE review_jobs SET status=?, finished_at=?, error=? WHERE job_id=?",
            (status, now, error, job_id),
        )
    else:
        conn.execute(
            "UPDATE review_jobs SET status=? WHERE job_id=?",
            (status, job_id),
        )
    conn.commit()


def get_review_job(conn, job_id):
    return conn.execute(
        "SELECT * FROM review_jobs WHERE job_id=?", (job_id,)
    ).fetchone()


def get_review_jobs(conn, limit=50):
    return conn.execute(
        "SELECT * FROM review_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def get_active_review_job_for_puzzle(conn, puzzle_id):
    return conn.execute(
        """SELECT * FROM review_jobs
           WHERE puzzle_id=? AND status IN ('queued', 'running')
           ORDER BY created_at DESC LIMIT 1""",
        (puzzle_id,),
    ).fetchone()


def get_untested_puzzle_ids(conn, model_name):
    """Puzzles with zero trial rows for the given model."""
    rows = conn.execute(
        """SELECT p.puzzle_id FROM puzzles p
           LEFT JOIN trials t ON t.puzzle_id=p.puzzle_id AND t.model_name=?
           WHERE t.trial_id IS NULL
           ORDER BY p.created_at DESC""",
        (model_name,),
    ).fetchall()
    return [r["puzzle_id"] for r in rows]


def delete_trials_for_puzzle_model(conn, puzzle_id, model_name):
    """Delete all trials for a (puzzle, model) — used for reruns."""
    conn.execute(
        "DELETE FROM trials WHERE puzzle_id=? AND model_name=?",
        (puzzle_id, model_name),
    )
    conn.execute(
        "DELETE FROM classifications WHERE puzzle_id=? AND model_name=?",
        (puzzle_id, model_name),
    )
    conn.commit()

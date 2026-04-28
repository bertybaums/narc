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
    return conn


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


# --- trials ---

def insert_trial(conn, puzzle_id, model_name, condition, prompt_text,
                 variant_id=None, repeat_num=1):
    conn.execute(
        """INSERT OR IGNORE INTO trials
           (puzzle_id, variant_id, model_name, condition, repeat_num, prompt_text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (puzzle_id, variant_id, model_name, condition, repeat_num, prompt_text),
    )
    conn.commit()
    if variant_id is not None:
        row = conn.execute(
            """SELECT trial_id FROM trials
               WHERE puzzle_id=? AND variant_id=?
                     AND model_name=? AND condition=? AND repeat_num=?""",
            (puzzle_id, variant_id, model_name, condition, repeat_num),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT trial_id FROM trials
               WHERE puzzle_id=? AND variant_id IS NULL
                     AND model_name=? AND condition=? AND repeat_num=?""",
            (puzzle_id, model_name, condition, repeat_num),
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
                          narrative_only, both, has_narc, variant_id=None):
    # SQLite treats NULL variant_id values as distinct in UNIQUE constraints,
    # so INSERT OR REPLACE doesn't dedupe. Delete first to guarantee uniqueness.
    if variant_id is None:
        conn.execute(
            """DELETE FROM classifications
               WHERE puzzle_id=? AND model_name=? AND variant_id IS NULL""",
            (puzzle_id, model_name),
        )
    else:
        conn.execute(
            """DELETE FROM classifications
               WHERE puzzle_id=? AND model_name=? AND variant_id=?""",
            (puzzle_id, model_name, variant_id),
        )
    conn.execute(
        """INSERT INTO classifications
           (puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc),
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

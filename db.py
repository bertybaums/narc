"""SQLite helpers for the NARC pipeline."""

import json
import sqlite3
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
                  human_difficulty=None, ai_difficulty=None):
    conn.execute(
        """INSERT OR REPLACE INTO puzzles
           (puzzle_id, title, narrative, sequence_json, masked_positions,
            answer_grids, creator, difficulty, human_difficulty, ai_difficulty, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, title, narrative, sequence_json, masked_positions,
         answer_grids, creator, difficulty, human_difficulty, ai_difficulty, tags),
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
    conn.execute(
        """INSERT OR REPLACE INTO classifications
           (puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc),
    )
    conn.commit()


# --- solve attempts ---

def insert_solve_attempt(conn, puzzle_id, session_id, phase, saw_narrative,
                         submitted_grids, correct, cell_accuracy,
                         time_spent_ms=None, solver_name=None, skipped_phase1=0):
    conn.execute(
        """INSERT INTO solve_attempts
           (puzzle_id, session_id, solver_name, phase, saw_narrative,
            submitted_grids, correct, cell_accuracy, time_spent_ms, skipped_phase1)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (puzzle_id, session_id, solver_name, phase, saw_narrative,
         submitted_grids, correct, cell_accuracy, time_spent_ms, skipped_phase1),
    )
    conn.commit()

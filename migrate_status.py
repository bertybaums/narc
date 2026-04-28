#!/usr/bin/env python3
"""Add lifecycle `status` column to puzzles and backfill from existing draft/featured
tags + model trial results.

Status values:
  - draft:    not shown on /browse (in-progress or all models fail)
  - active:   shown on /browse (default for puzzles at least one model can solve)
  - featured: shown on /browse with extra emphasis (admin override)

Backfill rules (first match wins):
  1. If puzzle has any `featured:` tag -> status='featured'
  2. Else if puzzle has any `draft:` tag -> status='draft'
  3. Else if no model can solve grids_only or both -> status='draft'
  4. Else -> status='active'

Also strips `draft:` and `featured:` prefix tags from puzzles.tags (now stored as
status). Other tags (audience:, domain:, arc:, etc.) are preserved.

Idempotent. Backs up the DB before running.
"""

import shutil
import sqlite3
import sys
from datetime import datetime


DB_PATH = "narc.db"
MODELS = ["gpt-oss-120b", "gpt-oss-20b", "qwen3.5-122b", "nemotron-3-super",
          "gemma-4-26b", "gemma-4-31b"]
LIFECYCLE_PREFIXES = ("draft:", "featured:")


def backup_db(path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.pre_status_migration_{ts}"
    shutil.copy2(path, backup)
    print(f"Backed up to {backup}")
    return backup


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def add_status_column(conn):
    if column_exists(conn, "puzzles", "status"):
        print("Column puzzles.status already exists -- skipping ADD")
        return
    conn.execute(
        "ALTER TABLE puzzles ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'"
    )
    conn.commit()
    print("Added column puzzles.status")


def build_solvable_set(conn):
    """Return set of puzzle_ids where at least one model solves grids_only or both."""
    rows = conn.execute(
        """SELECT puzzle_id, model_name, condition, MAX(correct) as correct
           FROM trials
           WHERE correct = 1 AND condition IN ('grids_only', 'both')
             AND model_name IN ({})
           GROUP BY puzzle_id, model_name, condition""".format(
            ",".join("?" * len(MODELS))
        ),
        MODELS,
    ).fetchall()
    return {r["puzzle_id"] for r in rows}


def split_tags(tag_str):
    return [t.strip() for t in (tag_str or "").split(",") if t.strip()]


def has_prefix(tags, prefix):
    return any(t.startswith(prefix) for t in tags)


def strip_lifecycle_tags(tags):
    return [t for t in tags if not any(t.startswith(p) for p in LIFECYCLE_PREFIXES)]


def backfill(conn):
    solvable = build_solvable_set(conn)
    rows = conn.execute("SELECT puzzle_id, tags, status FROM puzzles").fetchall()

    counts = {"featured": 0, "draft_tag": 0, "draft_unsolved": 0, "active": 0,
              "tags_stripped": 0}
    for r in rows:
        pid = r["puzzle_id"]
        tags = split_tags(r["tags"])

        if has_prefix(tags, "featured:"):
            new_status = "featured"
            counts["featured"] += 1
        elif has_prefix(tags, "draft:"):
            new_status = "draft"
            counts["draft_tag"] += 1
        elif pid in solvable:
            new_status = "active"
            counts["active"] += 1
        else:
            new_status = "draft"
            counts["draft_unsolved"] += 1

        clean_tags = strip_lifecycle_tags(tags)
        new_tag_str = ",".join(clean_tags) if clean_tags else None
        if new_tag_str != r["tags"]:
            counts["tags_stripped"] += 1

        conn.execute(
            "UPDATE puzzles SET status=?, tags=? WHERE puzzle_id=?",
            (new_status, new_tag_str, pid),
        )
    conn.commit()
    print("Backfill complete:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    print(f"Migrating {db_path}")
    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        add_status_column(conn)
        backfill(conn)
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()

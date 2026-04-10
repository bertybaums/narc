"""Import puzzles and trial data from auto-narc databases into the main NARC DB."""

import json
import sqlite3
from pathlib import Path

MAIN_DB = "narc.db"
AUTO_DB = Path("/Users/bbaum/Documents/_RCDS/auto-narc/auto_narc.db")
S4_DB = Path("/Users/bbaum/Documents/_RCDS/auto-narc/auto_narc_s4.db")


def import_puzzles(main, source, creator="colab", stance_group_map=None):
    """Import puzzles from source DB, skipping duplicates."""
    source.row_factory = sqlite3.Row
    puzzles = source.execute("SELECT * FROM puzzles ORDER BY puzzle_id").fetchall()
    imported = 0
    skipped = 0
    for p in puzzles:
        pid = p["puzzle_id"]
        existing = main.execute(
            "SELECT puzzle_id FROM puzzles WHERE puzzle_id=?", (pid,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        stance_group = None
        stance = None
        if stance_group_map and pid in stance_group_map:
            stance_group = stance_group_map[pid]["group"]
            stance = stance_group_map[pid]["stance"]

        main.execute(
            """INSERT INTO puzzles
               (puzzle_id, title, narrative, sequence_json, masked_positions,
                answer_grids, creator, difficulty, human_difficulty, ai_difficulty,
                tags, parent_puzzle_id, stance_group, stance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, p["title"], p["narrative"], p["sequence_json"],
             p["masked_positions"], p["answer_grids"], creator,
             p["difficulty"], p["human_difficulty"], p["ai_difficulty"],
             p["tags"], None, stance_group, stance),
        )
        # Add original variant
        main.execute(
            """INSERT OR IGNORE INTO narrative_variants
               (puzzle_id, variant, narrative, generator)
               VALUES (?, 'original', ?, ?)""",
            (pid, p["narrative"], creator),
        )
        imported += 1
    main.commit()
    return imported, skipped


def import_trials(main, source):
    """Import trials from source DB, skipping duplicates."""
    source.row_factory = sqlite3.Row
    trials = source.execute("SELECT * FROM trials").fetchall()
    imported = 0
    skipped = 0
    for t in trials:
        # Check if puzzle exists in main DB
        exists = main.execute(
            "SELECT 1 FROM puzzles WHERE puzzle_id=?", (t["puzzle_id"],)
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        try:
            main.execute(
                """INSERT OR IGNORE INTO trials
                   (puzzle_id, variant_id, model_name, condition, repeat_num,
                    prompt_text, raw_response, response_text, response_at,
                    latency_ms, error, predicted_grids, reasoning, correct, cell_accuracy)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t["puzzle_id"], None, t["model_name"], t["condition"],
                 t["repeat_num"], t["prompt_text"], t["raw_response"],
                 t["response_text"], t["response_at"], t["latency_ms"],
                 t["error"], t["predicted_grids"], t["reasoning"],
                 t["correct"], t["cell_accuracy"]),
            )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1
    main.commit()
    return imported, skipped


def import_classifications(main, source):
    """Import classifications from source DB."""
    source.row_factory = sqlite3.Row
    rows = source.execute("SELECT * FROM classifications").fetchall()
    imported = 0
    skipped = 0
    for c in rows:
        exists = main.execute(
            "SELECT 1 FROM puzzles WHERE puzzle_id=?", (c["puzzle_id"],)
        ).fetchone()
        if not exists:
            skipped += 1
            continue
        try:
            main.execute(
                """INSERT OR IGNORE INTO classifications
                   (puzzle_id, variant_id, model_name, grids_only,
                    narrative_only, both, has_narc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (c["puzzle_id"], None, c["model_name"],
                 c["grids_only"], c["narrative_only"], c["both"], c["has_narc"]),
            )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1
    main.commit()
    return imported, skipped


def build_stance_group_map(source):
    """Build a mapping from puzzle_id to stance group and stance type."""
    source.row_factory = sqlite3.Row
    puzzles = source.execute("SELECT puzzle_id, title FROM puzzles").fetchall()
    sgmap = {}
    for p in puzzles:
        pid = p["puzzle_id"]
        title = p["title"]
        # Determine stance from puzzle_id suffix
        stance = None
        for s in ("intentional", "design", "physical"):
            if pid.endswith("_" + s):
                stance = s
                break
        if not stance:
            continue
        # Group name from title: "The Chase (intentional)" -> "The Chase"
        group = title.rsplit(" (", 1)[0] if " (" in title else title
        sgmap[pid] = {"group": group, "stance": stance}
    return sgmap


def main():
    main_conn = sqlite3.connect(MAIN_DB)
    main_conn.row_factory = sqlite3.Row
    main_conn.execute("PRAGMA journal_mode=WAL")

    # --- auto_narc.db ---
    print(f"=== Importing from {AUTO_DB.name} ===")
    auto = sqlite3.connect(str(AUTO_DB))
    p_imp, p_skip = import_puzzles(main_conn, auto, creator="colab")
    print(f"  Puzzles: {p_imp} imported, {p_skip} skipped (already exist)")
    t_imp, t_skip = import_trials(main_conn, auto)
    print(f"  Trials: {t_imp} imported, {t_skip} skipped")
    c_imp, c_skip = import_classifications(main_conn, auto)
    print(f"  Classifications: {c_imp} imported, {c_skip} skipped")
    auto.close()

    # --- auto_narc_s4.db ---
    print(f"\n=== Importing from {S4_DB.name} ===")
    s4 = sqlite3.connect(str(S4_DB))
    sgmap = build_stance_group_map(s4)
    p_imp, p_skip = import_puzzles(main_conn, s4, creator="colab",
                                   stance_group_map=sgmap)
    print(f"  Puzzles: {p_imp} imported, {p_skip} skipped")
    t_imp, t_skip = import_trials(main_conn, s4)
    print(f"  Trials: {t_imp} imported, {t_skip} skipped")
    c_imp, c_skip = import_classifications(main_conn, s4)
    print(f"  Classifications: {c_imp} imported, {c_skip} skipped")
    s4.close()

    # Summary
    total = main_conn.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0]
    print(f"\n=== Done. Total puzzles in main DB: {total} ===")
    main_conn.close()


if __name__ == "__main__":
    main()

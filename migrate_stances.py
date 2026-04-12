#!/usr/bin/env python3
"""Migrate stance puzzles into the narrative variant system.

Merges 133 stance puzzles (55 groups) into 55 merged puzzles, each with
narrative variants for intentional/design/physical stances. Migrates all
referencing data: trials, classifications, oddoneout_trials, solve_attempts, votes.

Safe to run multiple times (idempotent). Backs up DB before changes.
"""

import json
import shutil
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime


DB_PATH = "narc.db"
STANCE_SUFFIXES = ["_intentional", "_design", "_physical"]
# Preference order for picking the "primary" stance
STANCE_PRIORITY = ["intentional", "design", "physical"]


def backup_db():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{DB_PATH}.pre_stance_migration_{ts}"
    shutil.copy2(DB_PATH, backup)
    print(f"Backed up to {backup}")
    return backup


def derive_base_id(puzzle_ids):
    """Derive a base_id from a set of stance puzzle_ids.

    e.g., ['agent_001_intentional', 'agent_001_design'] -> 'agent_001'
    """
    bases = set()
    for pid in puzzle_ids:
        base = pid
        for suffix in STANCE_SUFFIXES:
            if pid.endswith(suffix):
                base = pid[:-len(suffix)]
                break
        bases.add(base)

    if len(bases) != 1:
        # Fallback: some IDs may not follow the pattern (e.g., leaky variants)
        # Use the most common base
        from collections import Counter
        all_bases = []
        for pid in puzzle_ids:
            base = pid
            for suffix in STANCE_SUFFIXES:
                if pid.endswith(suffix):
                    base = pid[:-len(suffix)]
                    break
            all_bases.append(base)
        base = Counter(all_bases).most_common(1)[0][0]
        print(f"  WARNING: Multiple base IDs {bases}, using '{base}'")
        return base

    return bases.pop()


def get_variant_name(puzzle_id, stance, base_id):
    """Determine the variant name for a stance puzzle.

    Returns (variant_name, source_domain).
    The intentional stance becomes 'original' so it shows first.
    Leaky/duplicate variants get a unique name.
    """
    if stance == "intentional":
        # Check if this is a non-standard intentional (unlikely but safe)
        if not puzzle_id.endswith("_intentional"):
            return stance, f"stance:{stance}"
        return "original", "stance:intentional"
    elif stance == "design":
        # Check for leaky variants
        if not puzzle_id.startswith(base_id + "_"):
            return "design-leaky", "stance:design-leaky"
        return "design", "stance:design"
    elif stance == "physical":
        return "physical", "stance:physical"
    else:
        return stance, f"stance:{stance}"


def apply_schema_changes(conn):
    """Apply schema changes needed for the migration."""
    # 1. Add active_variant to solve_attempts
    try:
        conn.execute("ALTER TABLE solve_attempts ADD COLUMN active_variant TEXT")
        print("Added active_variant column to solve_attempts")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("active_variant column already exists")
        else:
            raise

    # 2. Create variant_views table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS variant_views (
            view_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            puzzle_id   TEXT NOT NULL REFERENCES puzzles(puzzle_id),
            variant     TEXT NOT NULL,
            viewed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variant_views_puzzle ON variant_views(puzzle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variant_views_session ON variant_views(session_id)")
    print("Created variant_views table")

    # 3. Recreate classifications with updated PK
    # Check if we need to do this (if variant_id is already in PK, skip)
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='classifications'").fetchone()
    if row and "PRIMARY KEY (puzzle_id, variant_id, model_name)" in row[0]:
        print("Classifications PK already updated")
    else:
        print("Recreating classifications table with updated PK...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS classifications_new (
                puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
                variant_id     INTEGER REFERENCES narrative_variants(variant_id),
                model_name     TEXT NOT NULL,
                grids_only     INTEGER,
                narrative_only INTEGER,
                both           INTEGER,
                has_narc       INTEGER,
                PRIMARY KEY (puzzle_id, variant_id, model_name)
            )
        """)
        conn.execute("""
            INSERT INTO classifications_new
            SELECT puzzle_id, variant_id, model_name, grids_only, narrative_only, both, has_narc
            FROM classifications
        """)
        old_count = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM classifications_new").fetchone()[0]
        assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"
        conn.execute("DROP TABLE classifications")
        conn.execute("ALTER TABLE classifications_new RENAME TO classifications")
        print(f"  Migrated {new_count} classification rows")

    # 4. Create votes table if not exists (in case migration runs before schema update)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            vote_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle_id   TEXT NOT NULL REFERENCES puzzles(puzzle_id),
            voter_id    TEXT NOT NULL,
            value       INTEGER NOT NULL,
            ip_address  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(puzzle_id, voter_id)
        )
    """)


def migrate(dry_run=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Pre-migration counts
    print("\n=== PRE-MIGRATION COUNTS ===")
    total_puzzles = conn.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0]
    stance_puzzles = conn.execute("SELECT COUNT(*) FROM puzzles WHERE stance IS NOT NULL").fetchone()[0]
    total_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    stance_trials = conn.execute("SELECT COUNT(*) FROM trials WHERE puzzle_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)").fetchone()[0]
    total_class = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    stance_class = conn.execute("SELECT COUNT(*) FROM classifications WHERE puzzle_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)").fetchone()[0]
    total_ooo = conn.execute("SELECT COUNT(*) FROM oddoneout_trials").fetchone()[0]
    stance_ooo = conn.execute("""SELECT COUNT(*) FROM oddoneout_trials
        WHERE puzzle_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)
           OR distractor_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)""").fetchone()[0]
    total_sa = conn.execute("SELECT COUNT(*) FROM solve_attempts").fetchone()[0]
    stance_sa = conn.execute("SELECT COUNT(*) FROM solve_attempts WHERE puzzle_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)").fetchone()[0]
    total_votes = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    stance_votes = conn.execute("SELECT COUNT(*) FROM votes WHERE puzzle_id IN (SELECT puzzle_id FROM puzzles WHERE stance IS NOT NULL)").fetchone()[0]

    print(f"Puzzles: {total_puzzles} total, {stance_puzzles} stance")
    print(f"Trials: {total_trials} total, {stance_trials} stance")
    print(f"Classifications: {total_class} total, {stance_class} stance")
    print(f"Oddoneout: {total_ooo} total, {stance_ooo} stance")
    print(f"Solve attempts: {total_sa} total, {stance_sa} stance")
    print(f"Votes: {total_votes} total, {stance_votes} stance")

    if stance_puzzles == 0:
        print("\nNo stance puzzles found — migration already complete or nothing to do.")
        conn.close()
        return

    if dry_run:
        print("\n*** DRY RUN — no changes will be made ***")

    # Apply schema changes
    print("\n=== SCHEMA CHANGES ===")
    apply_schema_changes(conn)
    conn.commit()

    # Discover stance groups
    groups_raw = conn.execute("""
        SELECT * FROM puzzles WHERE stance IS NOT NULL
        ORDER BY stance_group, stance, puzzle_id
    """).fetchall()

    groups = OrderedDict()
    for r in groups_raw:
        g = r["stance_group"]
        if g not in groups:
            groups[g] = []
        groups[g].append(dict(r))

    print(f"\n=== MIGRATING {len(groups)} STANCE GROUPS ===")

    stats = {
        "puzzles_created": 0,
        "variants_created": 0,
        "trials_updated": 0,
        "classifications_updated": 0,
        "oddoneout_updated": 0,
        "solve_attempts_updated": 0,
        "votes_updated": 0,
        "votes_conflicted": 0,
        "old_puzzles_deleted": 0,
        "old_variants_deleted": 0,
    }

    for group_name, members in groups.items():
        puzzle_ids = [m["puzzle_id"] for m in members]
        base_id = derive_base_id(puzzle_ids)

        # Idempotency: skip if merged puzzle already exists
        existing = conn.execute(
            "SELECT puzzle_id, stance FROM puzzles WHERE puzzle_id=?", (base_id,)
        ).fetchone()
        if existing and existing["stance"] is None:
            print(f"  SKIP {group_name} ({base_id}) — already migrated")
            continue

        print(f"\n  {group_name} -> {base_id}")
        print(f"    Members: {puzzle_ids}")

        # Pick primary (intentional preferred)
        primary = None
        for pref in STANCE_PRIORITY:
            for m in members:
                if m["stance"] == pref:
                    primary = m
                    break
            if primary:
                break
        if not primary:
            primary = members[0]

        print(f"    Primary: {primary['puzzle_id']} ({primary['stance']})")

        if dry_run:
            continue

        # Create merged puzzle
        conn.execute("""
            INSERT OR REPLACE INTO puzzles
            (puzzle_id, title, narrative, sequence_json, masked_positions, answer_grids,
             creator, difficulty, human_difficulty, ai_difficulty, tags,
             parent_puzzle_id, stance_group, stance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            base_id, group_name, primary["narrative"],
            primary["sequence_json"], primary["masked_positions"], primary["answer_grids"],
            primary["creator"], primary["difficulty"],
            primary["human_difficulty"], primary["ai_difficulty"],
            primary["tags"], None, group_name,
        ))
        stats["puzzles_created"] += 1

        # Create narrative variants and build ID mapping
        id_map = {}  # old_puzzle_id -> (base_id, variant_id)
        seen_variants = set()

        for m in members:
            variant_name, source_domain = get_variant_name(
                m["puzzle_id"], m["stance"], base_id
            )

            # Handle duplicate variant names within a group
            if variant_name in seen_variants:
                # Append a suffix to make unique
                orig = variant_name
                i = 2
                while variant_name in seen_variants:
                    variant_name = f"{orig}-v{i}"
                    source_domain = f"stance:{variant_name}"
                    i += 1
                print(f"    Dedup: {m['puzzle_id']} -> variant '{variant_name}' (was '{orig}')")

            seen_variants.add(variant_name)

            conn.execute("""
                INSERT OR IGNORE INTO narrative_variants
                (puzzle_id, variant, source_domain, narrative, generator)
                VALUES (?, ?, ?, ?, 'colab')
            """, (base_id, variant_name, source_domain, m["narrative"]))

            # Get the variant_id
            vid_row = conn.execute(
                "SELECT variant_id FROM narrative_variants WHERE puzzle_id=? AND variant=?",
                (base_id, variant_name)
            ).fetchone()
            variant_id = vid_row["variant_id"]

            id_map[m["puzzle_id"]] = (base_id, variant_id, variant_name)
            stats["variants_created"] += 1
            print(f"    Variant: {m['puzzle_id']} -> ({base_id}, {variant_name}, vid={variant_id})")

        # Migrate trials
        for old_pid, (new_pid, new_vid, _) in id_map.items():
            cur = conn.execute(
                "UPDATE trials SET puzzle_id=?, variant_id=? WHERE puzzle_id=?",
                (new_pid, new_vid, old_pid)
            )
            stats["trials_updated"] += cur.rowcount

        # Migrate classifications
        for old_pid, (new_pid, new_vid, _) in id_map.items():
            cur = conn.execute(
                "UPDATE classifications SET puzzle_id=?, variant_id=? WHERE puzzle_id=?",
                (new_pid, new_vid, old_pid)
            )
            stats["classifications_updated"] += cur.rowcount

        # Migrate oddoneout_trials (both puzzle_id and distractor_id)
        for old_pid, (new_pid, new_vid, _) in id_map.items():
            cur = conn.execute(
                "UPDATE oddoneout_trials SET puzzle_id=? WHERE puzzle_id=?",
                (new_pid, old_pid)
            )
            stats["oddoneout_updated"] += cur.rowcount
            cur = conn.execute(
                "UPDATE oddoneout_trials SET distractor_id=? WHERE distractor_id=?",
                (new_pid, old_pid)
            )
            stats["oddoneout_updated"] += cur.rowcount

        # Migrate solve_attempts
        for old_pid, (new_pid, new_vid, vname) in id_map.items():
            cur = conn.execute(
                "UPDATE solve_attempts SET puzzle_id=?, active_variant=? WHERE puzzle_id=?",
                (new_pid, vname, old_pid)
            )
            stats["solve_attempts_updated"] += cur.rowcount

        # Migrate votes (handle conflicts)
        for old_pid, (new_pid, new_vid, _) in id_map.items():
            cur = conn.execute(
                "UPDATE OR IGNORE votes SET puzzle_id=? WHERE puzzle_id=?",
                (new_pid, old_pid)
            )
            stats["votes_updated"] += cur.rowcount
            # Delete any remaining (conflicting)
            cur = conn.execute("DELETE FROM votes WHERE puzzle_id=?", (old_pid,))
            stats["votes_conflicted"] += cur.rowcount

        # Delete old narrative_variants
        for old_pid in id_map:
            cur = conn.execute(
                "DELETE FROM narrative_variants WHERE puzzle_id=?", (old_pid,)
            )
            stats["old_variants_deleted"] += cur.rowcount

        # Delete old puzzle rows
        for old_pid in id_map:
            conn.execute("DELETE FROM puzzles WHERE puzzle_id=?", (old_pid,))
            stats["old_puzzles_deleted"] += 1

    if not dry_run:
        conn.commit()

    # Post-migration counts
    print("\n=== POST-MIGRATION COUNTS ===")
    print(f"Puzzles: {conn.execute('SELECT COUNT(*) FROM puzzles').fetchone()[0]}")
    print(f"  (with stance_group): {conn.execute('SELECT COUNT(*) FROM puzzles WHERE stance_group IS NOT NULL').fetchone()[0]}")
    print(f"  (with stance=NULL): {conn.execute('SELECT COUNT(*) FROM puzzles WHERE stance IS NULL').fetchone()[0]}")
    print(f"Narrative variants: {conn.execute('SELECT COUNT(*) FROM narrative_variants').fetchone()[0]}")
    print(f"Trials: {conn.execute('SELECT COUNT(*) FROM trials').fetchone()[0]}")
    print(f"Classifications: {conn.execute('SELECT COUNT(*) FROM classifications').fetchone()[0]}")
    print(f"Oddoneout: {conn.execute('SELECT COUNT(*) FROM oddoneout_trials').fetchone()[0]}")
    print(f"Solve attempts: {conn.execute('SELECT COUNT(*) FROM solve_attempts').fetchone()[0]}")
    print(f"Votes: {conn.execute('SELECT COUNT(*) FROM votes').fetchone()[0]}")

    # Verify no orphans
    print("\n=== ORPHAN CHECK ===")
    orphan_trials = conn.execute("""
        SELECT COUNT(*) FROM trials
        WHERE puzzle_id NOT IN (SELECT puzzle_id FROM puzzles)
    """).fetchone()[0]
    orphan_class = conn.execute("""
        SELECT COUNT(*) FROM classifications
        WHERE puzzle_id NOT IN (SELECT puzzle_id FROM puzzles)
    """).fetchone()[0]
    orphan_ooo = conn.execute("""
        SELECT COUNT(*) FROM oddoneout_trials
        WHERE puzzle_id NOT IN (SELECT puzzle_id FROM puzzles)
           OR distractor_id NOT IN (SELECT puzzle_id FROM puzzles)
    """).fetchone()[0]
    print(f"Orphan trials: {orphan_trials}")
    print(f"Orphan classifications: {orphan_class}")
    print(f"Orphan oddoneout: {orphan_ooo}")
    if orphan_trials or orphan_class or orphan_ooo:
        print("WARNING: Orphaned records found!")

    print(f"\n=== MIGRATION STATS ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if not dry_run:
        backup_db()
    migrate(dry_run=dry_run)

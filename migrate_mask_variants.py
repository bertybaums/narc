"""Idempotent data migration for the mask-variants feature.

Schema changes (new tables, mask_variant_id columns, rebuilt UNIQUE/PK) are
applied automatically by db.init_db() -> _apply_migrations. This script does the
DATA parts, and is safe to re-run:

  1. Backfill sequence_json so every position carries its true grid (fill holes
     from answer_grids); set each item's `masked` flag.
  2. Seed one 'original' mask variant per puzzle from its masked_positions.
  3. Backfill mask_variant_id on existing trials/classifications -> 'original'.
  4. Seed the test matrix: each narrative variant x the 'original' mask (enabled).

Run:  python migrate_mask_variants.py [--db narc.db]
"""

import json

import click

import db
import grids


def backfill_complete_sequences(conn):
    rows = conn.execute(
        "SELECT puzzle_id, sequence_json, masked_positions, answer_grids FROM puzzles"
    ).fetchall()
    changed = 0
    for r in rows:
        seq = json.loads(r["sequence_json"])
        mp = set(json.loads(r["masked_positions"]))
        ag = json.loads(r["answer_grids"])
        newseq = grids.complete_sequence(seq, ag)
        for it in newseq:
            it["masked"] = it["position"] in mp
        if newseq != seq:
            conn.execute(
                "UPDATE puzzles SET sequence_json=? WHERE puzzle_id=?",
                (json.dumps(newseq), r["puzzle_id"]),
            )
            changed += 1
    conn.commit()
    return changed


def seed_original_mask_variants(conn):
    rows = conn.execute("SELECT puzzle_id, masked_positions FROM puzzles").fetchall()
    for r in rows:
        # masked_positions is already a JSON string; upsert stores it verbatim.
        db.upsert_mask_variant(conn, r["puzzle_id"], "original", r["masked_positions"])
    return len(rows)


def backfill_trial_mask_ids(conn):
    cur = conn.execute(
        """UPDATE trials SET mask_variant_id = (
               SELECT mv.mask_variant_id FROM mask_variants mv
               WHERE mv.puzzle_id = trials.puzzle_id AND mv.label='original')
           WHERE mask_variant_id IS NULL"""
    )
    trials_updated = cur.rowcount
    cur = conn.execute(
        """UPDATE classifications SET mask_variant_id = (
               SELECT mv.mask_variant_id FROM mask_variants mv
               WHERE mv.puzzle_id = classifications.puzzle_id AND mv.label='original')
           WHERE mask_variant_id IS NULL"""
    )
    cls_updated = cur.rowcount
    conn.commit()
    return trials_updated, cls_updated


def seed_variant_pairs(conn):
    """Enable every existing narrative variant against the 'original' mask, so
    current behavior (variants tested at the original mask) is represented in the
    matrix. New mask variants start disabled until the creator enables cells."""
    rows = conn.execute(
        """SELECT nv.puzzle_id, nv.variant_id, mv.mask_variant_id
           FROM narrative_variants nv
           JOIN mask_variants mv
             ON mv.puzzle_id = nv.puzzle_id AND mv.label='original'"""
    ).fetchall()
    for r in rows:
        db.set_variant_pair(conn, r["puzzle_id"], r["variant_id"],
                            r["mask_variant_id"], enabled=1)
    return len(rows)


@click.command()
@click.option("--db", "db_path", default="narc.db", help="Path to the SQLite DB")
def main(db_path):
    conn = db.init_db(db_path)  # applies schema + _apply_migrations
    try:
        click.echo("Schema migrations applied (mask_variants, variant_pairs, "
                   "mask_variant_id columns).")

        n = backfill_complete_sequences(conn)
        click.echo(f"1. Complete-sequence backfill: {n} puzzles updated.")

        n = seed_original_mask_variants(conn)
        click.echo(f"2. Seeded 'original' mask variants: {n} puzzles.")

        t, c = backfill_trial_mask_ids(conn)
        click.echo(f"3. Backfilled mask_variant_id: {t} trials, {c} classifications.")

        n = seed_variant_pairs(conn)
        click.echo(f"4. Seeded variant_pairs (narrative x original mask): {n} cells.")

        # Sanity
        holes = conn.execute(
            "SELECT COUNT(*) FROM trials WHERE mask_variant_id IS NULL"
        ).fetchone()[0]
        click.echo(f"\nRemaining trials with NULL mask_variant_id: {holes} "
                   "(nonzero only if a puzzle row is missing).")
        click.echo("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""Phase 2: Classify NARC property for each puzzle/model pair.

CLI:
    python classify.py [--model MODEL] [--puzzle PUZZLE_ID]

In-process:
    from classify import run_classify_job
    run_classify_job(model="gpt-oss-120b", puzzle="narc_001", log_fn=log.write)
"""

import click
import db


def run_classify_job(model, puzzle=None, log_fn=print):
    """Compute NARC classifications for one model across one or all puzzles.

    Returns dict with keys: total, narc.
    """
    conn = db.init_db()
    try:
        if puzzle:
            row = db.get_puzzle(conn, puzzle)
            if not row:
                log_fn(f"Puzzle {puzzle} not found")
                return {"total": 0, "narc": 0}
            puzzles = [row]
        else:
            puzzles = db.get_all_puzzles(conn)

        narc_count = 0
        total = 0

        for puzzle_row in puzzles:
            pid = puzzle_row["puzzle_id"]
            trials = db.get_trials(conn, pid, model_name=model)

            if not trials:
                continue

            # grids_only is narrative-independent — it varies only with the mask,
            # and is stored under variant_id NULL. Narrative conditions vary with
            # both the narrative variant and the mask. To classify a (variant,
            # mask) cell we pair that cell's narrative results with its mask's
            # grids_only result.
            grids_by_mask = {}                 # mask_variant_id -> correct
            narrative_by_cell = {}             # (variant_id, mask_variant_id) -> {cond: correct}
            for t in trials:
                if t["correct"] is None:
                    continue
                mvid = t["mask_variant_id"]
                if t["condition"] == "grids_only":
                    grids_by_mask[mvid] = t["correct"]
                else:
                    cell = (t["variant_id"], mvid)
                    narrative_by_cell.setdefault(cell, {})[t["condition"]] = t["correct"]

            # Classify every cell that has narrative data, plus any mask that has
            # only a grids_only result (so grids_sufficient is still recorded).
            cells = set(narrative_by_cell)
            for mvid in grids_by_mask:
                cells.add((None, mvid))

            for (vid, mvid) in cells:
                res = narrative_by_cell.get((vid, mvid), {})
                grids_only = grids_by_mask.get(mvid, 0)
                narrative_only = res.get("narrative_only", 0)
                both = res.get("both", 0)
                has_narc = int(grids_only == 0 and narrative_only == 0 and both == 1)

                db.upsert_classification(conn, pid, model, grids_only, narrative_only,
                                         both, has_narc, variant_id=vid,
                                         mask_variant_id=mvid)
                total += 1
                if has_narc:
                    narc_count += 1

                status = "NARC" if has_narc else "no"
                if grids_only:
                    status = "grids_sufficient"
                elif narrative_only:
                    status = "narrative_sufficient"
                elif not both:
                    status = "unsolvable"
                log_fn(f"  {pid} [v={vid} m={mvid}]: {status} "
                       f"(g={grids_only} n={narrative_only} b={both})")

        log_fn(f"\nResults for {model}:")
        log_fn(f"  Cells classified: {total}")
        log_fn(f"  NARC-verified: {narc_count}")

        return {"total": total, "narc": narc_count}
    finally:
        conn.close()


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Model name")
@click.option("--puzzle", default=None, help="Single puzzle ID (default: all)")
def main(model, puzzle):
    run_classify_job(model=model, puzzle=puzzle, log_fn=click.echo)


if __name__ == "__main__":
    main()

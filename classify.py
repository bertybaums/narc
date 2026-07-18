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

            # Authoritative recompute: drop this (puzzle, model)'s existing rows so
            # stale cells with no backing trials (orphans left by the old collapsed
            # classifier / variant-id remaps) don't linger with a false has_narc.
            conn.execute(
                "DELETE FROM classifications WHERE puzzle_id=? AND model_name=?",
                (pid, model),
            )

            # grids_only is narrative-independent — it varies only with the mask,
            # and is stored under variant_id NULL. Narrative conditions vary with
            # both the narrative variant and the mask. To classify a (variant,
            # mask) cell we pair that cell's narrative results with its mask's
            # grids_only result.
            grids_by_mask = {}                 # mask_variant_id -> correct
            narrative_by_cell = {}             # (variant_id, mask_variant_id) -> {cond: correct}
            shuffled_by_cell = {}              # (variant_id, mask_variant_id) -> [correct, ...]
            keywords_by_cell = {}              # (variant_id, mask_variant_id) -> [correct, ...]
            for t in trials:
                if t["correct"] is None:
                    continue
                mvid = t["mask_variant_id"]
                if t["condition"] == "grids_only":
                    grids_by_mask[mvid] = t["correct"]
                elif t["condition"] == "both_shuffled":
                    shuffled_by_cell.setdefault((t["variant_id"], mvid), []).append(
                        t["correct"])
                elif t["condition"] == "both_keywords":
                    keywords_by_cell.setdefault((t["variant_id"], mvid), []).append(
                        t["correct"])
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

                # Order-sensitivity (weak/strong NARC): only meaningful for NARC
                # cells that have shuffled-order 'both' trials to judge from.
                narc_strength = shuffle_solved = shuffle_total = None
                if has_narc:
                    sh = shuffled_by_cell.get((vid, mvid), [])
                    if sh:
                        shuffle_total = len(sh)
                        shuffle_solved = sum(sh)
                        if shuffle_solved == 0:
                            narc_strength = "strong"
                        elif shuffle_solved == shuffle_total:
                            narc_strength = "weak"
                        else:
                            narc_strength = "partial"

                # Narrative-sensitivity (keyword ablation): does the clue help
                # as a narrative or merely as a bag of key terms? Only
                # meaningful for NARC cells with 'both_keywords' trials.
                narrative_dependence = keyword_solved = keyword_total = None
                if has_narc:
                    kw = keywords_by_cell.get((vid, mvid), [])
                    if kw:
                        keyword_total = len(kw)
                        keyword_solved = sum(kw)
                        if keyword_solved == 0:
                            narrative_dependence = "narrative"
                        elif keyword_solved == keyword_total:
                            narrative_dependence = "lexical"
                        else:
                            narrative_dependence = "partial"

                db.upsert_classification(conn, pid, model, grids_only, narrative_only,
                                         both, has_narc, variant_id=vid,
                                         mask_variant_id=mvid,
                                         narc_strength=narc_strength,
                                         shuffle_solved=shuffle_solved,
                                         shuffle_total=shuffle_total,
                                         narrative_dependence=narrative_dependence,
                                         keyword_solved=keyword_solved,
                                         keyword_total=keyword_total)
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
                if has_narc and narc_strength:
                    status += f"/{narc_strength}({shuffle_solved}/{shuffle_total})"
                if has_narc and narrative_dependence:
                    status += f"/{narrative_dependence}({keyword_solved}/{keyword_total})"
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

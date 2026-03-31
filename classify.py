"""Phase 2: Classify NARC property for each puzzle/model pair.

Usage:
    python classify.py [--model MODEL]
"""

import click
import db


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Model name")
def main(model):
    conn = db.init_db()
    puzzles = db.get_all_puzzles(conn)

    narc_count = 0
    total = 0

    for puzzle_row in puzzles:
        pid = puzzle_row["puzzle_id"]
        trials = db.get_trials(conn, pid, model_name=model)

        if not trials:
            continue

        total += 1
        results = {}
        for t in trials:
            if t["correct"] is not None:
                results[t["condition"]] = t["correct"]

        grids_only = results.get("grids_only", 0)
        narrative_only = results.get("narrative_only", 0)
        both = results.get("both", 0)
        has_narc = int(grids_only == 0 and narrative_only == 0 and both == 1)

        db.upsert_classification(conn, pid, model, grids_only, narrative_only,
                                 both, has_narc)

        status = "NARC" if has_narc else "no"
        if grids_only:
            status = "grids_sufficient"
        elif narrative_only:
            status = "narrative_sufficient"
        elif not both:
            status = "unsolvable"

        if has_narc:
            narc_count += 1

        click.echo(f"  {pid}: {status} "
                    f"(g={grids_only} n={narrative_only} b={both})")

    click.echo(f"\nResults for {model}:")
    click.echo(f"  Total puzzles tested: {total}")
    click.echo(f"  NARC-verified: {narc_count}")

    conn.close()


if __name__ == "__main__":
    main()

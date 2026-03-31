"""Run 3-condition testing on Story Prism actor-salience variants.

Tests each prism_actors_* variant against the same puzzle grids,
using the variant narrative in narrative_only and both conditions.

Usage:
    python collect_prism.py [--model MODEL] [--concurrency N] [--dry-run]
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import yaml

import db
import grids
import models
import prompts
from collect import run_trial


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def get_model_config(config, name):
    for m in config["models"]:
        if m["name"] == name:
            return m
    raise ValueError(f"Model {name} not found in config")


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--concurrency", default=8, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--tier", default="actors", help="Prism tier: actors, teller, feeling, or all")
def main(model, concurrency, dry_run, tier):
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conditions = config["experiment"]["conditions"]

    conn = db.init_db()

    # Get prism variants
    pattern = f"prism_{tier}_%" if tier != "all" else "prism_%"
    variants = conn.execute(
        """SELECT v.variant_id, v.puzzle_id, v.variant, v.narrative
           FROM narrative_variants v
           WHERE v.variant LIKE ?
           ORDER BY v.puzzle_id, v.variant""",
        (pattern,),
    ).fetchall()

    click.echo(f"Found {len(variants)} prism variants (tier={tier})")

    # Insert trial rows for each variant x condition
    trial_map = {}  # trial_id -> (variant_row, condition)
    for v in variants:
        puzzle_row = db.get_puzzle(conn, v["puzzle_id"])
        if not puzzle_row:
            click.echo(f"  WARNING: puzzle {v['puzzle_id']} not in DB, skipping")
            continue
        puzzle_data = db.puzzle_to_json(puzzle_row)

        for cond in conditions:
            if cond == "grids_only":
                prompt_msgs = prompts.build_grids_only(puzzle_data)
            elif cond == "narrative_only":
                prompt_msgs = prompts.build_narrative_only(puzzle_data, narrative=v["narrative"])
            else:
                prompt_msgs = prompts.build_both(puzzle_data, narrative=v["narrative"])

            prompt_text = json.dumps(prompt_msgs)
            tid = db.insert_trial(
                conn, v["puzzle_id"], model, cond, prompt_text,
                variant_id=v["variant_id"]
            )
            if tid:
                trial_map[tid] = (v, puzzle_data)

    # Get pending trials for these variants
    pending = conn.execute(
        """SELECT t.* FROM trials t
           JOIN narrative_variants v ON t.variant_id = v.variant_id
           WHERE t.model_name = ? AND t.response_text IS NULL
                 AND v.variant LIKE ?
           ORDER BY t.puzzle_id""",
        (model, pattern),
    ).fetchall()

    click.echo(f"Pending trials: {len(pending)}")

    if dry_run:
        for t in pending:
            vid = t["variant_id"]
            vrow = conn.execute(
                "SELECT variant FROM narrative_variants WHERE variant_id=?", (vid,)
            ).fetchone()
            click.echo(f"  Would run: {t['puzzle_id']}/{vrow['variant']}/{t['condition']}")
        return

    # Pre-load puzzle data and variant narratives
    puzzle_cache = {}
    variant_cache = {}
    for t in pending:
        pid = t["puzzle_id"]
        vid = t["variant_id"]
        if pid not in puzzle_cache:
            puzzle_row = db.get_puzzle(conn, pid)
            puzzle_cache[pid] = db.puzzle_to_json(puzzle_row)
        if vid not in variant_cache:
            vrow = conn.execute(
                "SELECT narrative FROM narrative_variants WHERE variant_id=?", (vid,)
            ).fetchone()
            variant_cache[vid] = vrow["narrative"] if vrow else None

    def process_trial(trial_row):
        puzzle_data = puzzle_cache[trial_row["puzzle_id"]]
        variant_narrative = variant_cache.get(trial_row["variant_id"])
        return run_trial(
            model_config, extraction_config, trial_row, puzzle_data,
            variant_narrative=variant_narrative
        )

    completed = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process_trial, t): t for t in pending}
        for future in as_completed(futures):
            trial_row = futures[future]
            try:
                result = future.result()
                trial_id = result[0]
                raw_response = result[1]
                response_text = result[2]
                reasoning = result[3]
                error = result[4]
                latency = result[5]
                predicted = result[6] if len(result) > 6 else None

                db.update_trial_response(
                    conn, trial_id, raw_response, response_text, latency, error=error
                )

                if predicted is not None:
                    puzzle_data = puzzle_cache[trial_row["puzzle_id"]]
                    expected = puzzle_data["answer_grids"]
                    masked_positions = puzzle_data["masked_positions"]

                    pred_mapped = predicted
                    if "_single" in predicted and len(masked_positions) == 1:
                        pred_mapped = {str(masked_positions[0]): predicted["_single"]}

                    all_correct = True
                    total_cells = 0
                    matching_cells = 0
                    for pos_str, exp_grid in expected.items():
                        pred_grid = pred_mapped.get(pos_str, [])
                        c, acc = grids.compare_grids(pred_grid, exp_grid)
                        if not c:
                            all_correct = False
                        r = len(exp_grid)
                        cols = len(exp_grid[0]) if r > 0 else 0
                        n = r * cols
                        total_cells += n
                        matching_cells += int(acc * n)

                    cell_accuracy = matching_cells / total_cells if total_cells else 0
                    db.update_trial_evaluation(
                        conn, trial_id, json.dumps(pred_mapped), reasoning,
                        1 if all_correct else 0, cell_accuracy
                    )
                    status = "correct" if all_correct else f"wrong ({cell_accuracy:.1%})"
                else:
                    status = f"parse_error: {error}"
                    errors += 1

                completed += 1
                vid = trial_row["variant_id"]
                vname = variant_cache.get(vid, "?")[:30]
                click.echo(
                    f"  [{completed}/{len(pending)}] "
                    f"{trial_row['puzzle_id']}/{trial_row['condition']}: {status}"
                )

            except Exception as e:
                click.echo(f"  ERROR {trial_row['puzzle_id']}/{trial_row['condition']}: {e}")
                errors += 1

    click.echo(f"\nDone: {completed} completed, {errors} errors")


if __name__ == "__main__":
    main()

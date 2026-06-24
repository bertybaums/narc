"""Phase 1: Run 3-condition testing on subject models via MindRouter.

CLI:
    python collect.py [--model MODEL] [--puzzle PUZZLE_ID] [--condition CONDITION]

In-process (e.g., from server.py):
    from collect import run_collect_job
    run_collect_job(model="gpt-oss-120b", puzzle="narc_001", log_fn=log.write)
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import yaml

import db
import grids
import models
import prompts


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def get_model_config(config, name):
    for m in config["models"]:
        if m["name"] == name:
            return m
    raise ValueError(f"Model {name} not found in config")


def run_trial(model_config, extraction_config, trial_row, puzzle_data, variant_narrative=None):
    """Run a single trial: call LLM, parse response, evaluate."""
    trial_id = trial_row["trial_id"]
    condition = trial_row["condition"]

    if condition == "grids_only":
        messages = prompts.build_grids_only(puzzle_data)
    elif condition == "narrative_only":
        messages = prompts.build_narrative_only(puzzle_data, narrative=variant_narrative)
    elif condition == "both":
        messages = prompts.build_both(puzzle_data, narrative=variant_narrative)
    else:
        raise ValueError(f"Unknown condition: {condition}")

    try:
        raw1, reasoning, raw2, text2, total_latency = models.call_llm_two_pass(
            model_config, messages, prompts.build_extraction, extraction_config
        )
    except Exception as e:
        return trial_id, None, None, None, str(e), 0

    predicted, parsed_reasoning, parse_error = grids.parse_response_grids(text2)

    if predicted is None:
        predicted, _, _ = grids.parse_response_grids(reasoning)

    if predicted is None:
        try:
            masked_positions = puzzle_data["masked_positions"]
            dimensions = [
                (puzzle_data["sequence"][p]["rows"], puzzle_data["sequence"][p]["cols"])
                for p in masked_positions
            ]
            strict_msgs = prompts.build_extraction_strict(
                reasoning, masked_positions, dimensions
            )
            _, text3, latency3 = models.call_llm(extraction_config, strict_msgs)
            total_latency += latency3
            predicted, _, parse_error = grids.parse_response_grids(text3)
            if predicted is not None:
                text2 = text3
        except Exception:
            pass

    return trial_id, raw1, text2, reasoning, parse_error if predicted is None else None, total_latency, predicted


def run_collect_job(model, puzzle=None, condition=None, concurrency=8,
                    dry_run=False, log_fn=print):
    """Collect responses for one model across one or all puzzles.

    Returns dict with keys: pending, completed, errors.
    log_fn is called with progress messages (defaults to print for CLI use).
    """
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conditions = [condition] if condition else config["experiment"]["conditions"]

    conn = db.init_db()
    try:
        if puzzle:
            row = db.get_puzzle(conn, puzzle)
            if not row:
                log_fn(f"Puzzle {puzzle} not found")
                return {"pending": 0, "completed": 0, "errors": 0}
            rows = [row]
        else:
            rows = db.get_all_puzzles(conn)

        log_fn(f"Collecting: {len(rows)} puzzles x {len(conditions)} conditions on {model}")

        for row in rows:
            puzzle_data = db.puzzle_to_json(row)
            # Pre-flight: a masked position outside the sequence (e.g. left over
            # from shrinking the sequence after masking a later grid) would crash
            # prompt building with a cryptic "list index out of range". Surface a
            # clear, actionable error instead.
            seq = puzzle_data.get("sequence") or []
            n = len(seq)
            bad = [p for p in puzzle_data.get("masked_positions", [])
                   if not isinstance(p, int) or p < 0 or p >= n]
            if bad:
                raise ValueError(
                    f"Puzzle {puzzle_data['puzzle_id']}: masked position(s) {bad} "
                    f"out of range for {n}-grid sequence (valid 0-{n - 1})"
                )
            for cond in conditions:
                prompt_text = json.dumps(
                    prompts.build_grids_only(puzzle_data) if cond == "grids_only"
                    else prompts.build_narrative_only(puzzle_data) if cond == "narrative_only"
                    else prompts.build_both(puzzle_data)
                )
                db.insert_trial(conn, puzzle_data["puzzle_id"], model, cond, prompt_text)

        pending = db.get_pending_trials(conn, model_name=model)
        if puzzle:
            pending = [t for t in pending if t["puzzle_id"] == puzzle]
        log_fn(f"Pending trials: {len(pending)}")

        if dry_run:
            for t in pending:
                log_fn(f"  Would run: {t['puzzle_id']} / {t['condition']}")
            return {"pending": len(pending), "completed": 0, "errors": 0}

        completed = 0
        errors = 0

        puzzle_cache = {}
        for t in pending:
            pid = t["puzzle_id"]
            if pid not in puzzle_cache:
                puzzle_row = db.get_puzzle(conn, pid)
                puzzle_cache[pid] = db.puzzle_to_json(puzzle_row)

        def process_trial(trial_row):
            puzzle_data = puzzle_cache[trial_row["puzzle_id"]]
            return run_trial(model_config, extraction_config, trial_row, puzzle_data)

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

                    db.update_trial_response(conn, trial_id, raw_response, response_text,
                                             latency, error=error)

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
                    log_fn(f"  [{completed}/{len(pending)}] "
                           f"{trial_row['puzzle_id']}/{trial_row['condition']}: {status}")

                except Exception as e:
                    log_fn(f"  ERROR {trial_row['puzzle_id']}/{trial_row['condition']}: {e}")
                    errors += 1

        log_fn(f"\nDone: {completed} completed, {errors} errors")
        return {"pending": len(pending), "completed": completed, "errors": errors}
    finally:
        conn.close()


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--puzzle", default=None, help="Single puzzle ID (default: all)")
@click.option("--condition", default=None, help="Single condition (default: all 3)")
@click.option("--concurrency", default=8, type=int, help="Max parallel requests")
@click.option("--dry-run", is_flag=True, help="Show what would be done without calling API")
def main(model, puzzle, condition, concurrency, dry_run):
    run_collect_job(model=model, puzzle=puzzle, condition=condition,
                    concurrency=concurrency, dry_run=dry_run, log_fn=click.echo)


if __name__ == "__main__":
    main()

"""Test focalization variants: same puzzle, different actor perspectives.

For each focal puzzle, runs the 3 focal narrative variants (active, observer, absent)
under the 'both' condition to test whether perspective affects solve rate.

Usage:
    python collect_focal.py --model gpt-oss-120b [--concurrency 8]
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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


def run_variant_trial(model_config, extraction_config, puzzle_data, narrative):
    """Run one trial with a specific narrative variant."""
    messages = prompts.build_both(puzzle_data, narrative=narrative)

    try:
        raw1, reasoning, raw2, text2, total_latency = models.call_llm_two_pass(
            model_config, messages, prompts.build_extraction, extraction_config
        )
    except Exception as e:
        return None, None, str(e), 0

    predicted, _, parse_error = grids.parse_response_grids(text2)
    if predicted is None:
        predicted, _, _ = grids.parse_response_grids(reasoning)

    return predicted, reasoning, parse_error if predicted is None else None, total_latency


@click.command()
@click.option("--model", default="gpt-oss-120b")
@click.option("--concurrency", default=8, type=int)
def main(model, concurrency):
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")

    conn = db.init_db()

    # Load focal variant files
    variant_data = {}
    for vfile in sorted(Path("data").glob("focal_variants_*.json")):
        data = json.loads(vfile.read_text())
        for key, info in data.items():
            if not isinstance(info, dict) or "variants" not in info:
                continue
            variant_data[key] = info["variants"]

    # Build work items: (puzzle_id, variant_name, narrative, puzzle_data)
    work = []
    for pid, variants in variant_data.items():
        puzzle_row = db.get_puzzle(conn, pid)
        if not puzzle_row:
            continue
        puzzle_data = db.puzzle_to_json(puzzle_row)

        for v in variants:
            narr = v.get("narrative", "")
            if not narr:
                continue
            var_name = v.get("variant", f"focal_{v.get('actor', '?')}")
            ease = v.get("predicted_ease", "?")
            actor = v.get("actor", "?")
            work.append((pid, var_name, actor, ease, narr, puzzle_data))

    click.echo(f"Testing {len(work)} focal variant trials on {model} (concurrency {concurrency})")

    results = []
    completed = 0
    errors = 0

    def process(item):
        pid, var_name, actor, ease, narr, puzzle_data = item
        predicted, reasoning, error, latency = run_variant_trial(
            model_config, extraction_config, puzzle_data, narr
        )
        return pid, var_name, actor, ease, predicted, reasoning, error, latency

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process, w): w for w in work}
        for future in as_completed(futures):
            w = futures[future]
            pid, var_name, actor, ease, *_ = w
            try:
                pid, var_name, actor, ease, predicted, reasoning, error, latency = future.result()

                if predicted is not None:
                    puzzle_data = [x for x in work if x[0] == pid][0][5]
                    expected = puzzle_data["answer_grids"]
                    masked_positions = puzzle_data["masked_positions"]

                    pred_mapped = predicted
                    if "_single" in predicted and len(masked_positions) == 1:
                        pred_mapped = {str(masked_positions[0]): predicted["_single"]}

                    all_correct = True
                    total_cells = matching_cells = 0
                    for pos_str, exp_grid in expected.items():
                        pred_grid = pred_mapped.get(pos_str, [])
                        c, acc = grids.compare_grids(pred_grid, exp_grid)
                        if not c:
                            all_correct = False
                        n = len(exp_grid) * (len(exp_grid[0]) if exp_grid else 0)
                        total_cells += n
                        matching_cells += int(acc * n)

                    cell_accuracy = matching_cells / total_cells if total_cells else 0
                    status = "correct" if all_correct else f"wrong ({cell_accuracy:.0%})"

                    results.append({
                        "puzzle_id": pid, "variant": var_name, "actor": actor,
                        "predicted_ease": ease, "correct": all_correct,
                        "cell_accuracy": cell_accuracy, "latency_ms": latency
                    })
                else:
                    status = f"parse_error"
                    errors += 1
                    results.append({
                        "puzzle_id": pid, "variant": var_name, "actor": actor,
                        "predicted_ease": ease, "correct": False,
                        "cell_accuracy": 0, "error": error, "latency_ms": latency
                    })

                completed += 1
                click.echo(f"  [{completed}/{len(work)}] {pid}/{var_name} ({ease}): {status}")

            except Exception as e:
                click.echo(f"  ERROR {pid}/{var_name}: {e}")
                errors += 1

    # Save results
    out_path = f"focal_results_{model}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    # Summary by predicted ease
    click.echo(f"\n{'='*60}")
    click.echo(f"FOCALIZATION RESULTS for {model}")
    click.echo(f"{'='*60}")

    for ease_level in ["easy", "medium", "hard"]:
        subset = [r for r in results if r["predicted_ease"] == ease_level]
        if not subset:
            continue
        n = len(subset)
        correct = sum(1 for r in subset if r["correct"])
        avg_acc = sum(r["cell_accuracy"] for r in subset) / n if n else 0
        click.echo(f"\n  {ease_level.upper()} (focal_active/observer/absent):")
        click.echo(f"    Trials: {n}")
        click.echo(f"    Correct: {correct}/{n} ({correct/n*100:.1f}%)")
        click.echo(f"    Avg cell accuracy: {avg_acc:.1%}")

    click.echo(f"\n  Total: {completed} completed, {errors} errors")
    click.echo(f"  Results saved to {out_path}")

    conn.close()


if __name__ == "__main__":
    main()

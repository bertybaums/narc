"""Follow-up runs for the NARC-tiny puzzles (creator `narc-tiny`, see import_narc_tiny.py).

The imported runs cover grids_only, the story alone, story + grids, grammar text + grids and
the K shuffled orders of every NARC cell. This script runs what they left out, for one model
or all, on the NARC-tiny puzzles only:

  1. narrative_only on the grammar text (the imported runs tested the story alone, never the
     grammar text alone), stored under the grammar variant and the original mask;
  2. classify;
  3. order-sensitivity for any NARC cell without its K shuffles (a no-op after the import);
  4. narrative-sensitivity (keyword ablation), K repeats per NARC cell;
  5. classify, so the verdicts are stored.

Usage:
    python collect_narc_tiny.py [--model MODEL | --all-models] [--concurrency N] [--dry-run]

Resumable: every step skips trials that already have an answer. Run on prod from inside the
container (see run_narc_tiny.sh).
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

import db
import prompts
from classify import run_classify_job
from collect import (get_model_config, grade_prediction, load_config,
                     run_narrative_sensitivity_job, run_sensitivity_job, run_trial)

CREATOR = "narc-tiny"


def tiny_puzzle_ids(conn):
    return [r["puzzle_id"] for r in conn.execute(
        "SELECT puzzle_id FROM puzzles WHERE creator=? ORDER BY puzzle_id", (CREATOR,))]


def run_grammar_narrative_only(model, concurrency=8, dry_run=False, log_fn=print):
    """narrative_only on the grammar text for every NARC-tiny puzzle."""
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conn = db.init_db()
    try:
        planned = []  # (pid, variant_id, mask_variant_id, prompt_text, view, narrative)
        for pid in tiny_puzzle_ids(conn):
            view = db.puzzle_to_json(db.get_puzzle(conn, pid))
            v = conn.execute(
                "SELECT variant_id, narrative FROM narrative_variants "
                "WHERE puzzle_id=? AND variant='grammar'", (pid,)).fetchone()
            mvid = db.get_original_mask_variant_id(conn, pid)
            if not v or mvid is None:
                log_fn(f"  skip {pid}: grammar variant or original mask missing")
                continue
            msgs = prompts.build_narrative_only(view, narrative=v["narrative"])
            planned.append((pid, v["variant_id"], mvid, json.dumps(msgs), view, v["narrative"]))

        if dry_run:
            log_fn(f"Grammar narrative_only planned trials: {len(planned)}")
            return {"pending": len(planned), "completed": 0, "errors": 0}

        pending = []
        for pid, vid, mvid, prompt_text, view, narrative in planned:
            tid = db.insert_trial(conn, pid, model, "narrative_only", prompt_text,
                                  variant_id=vid, mask_variant_id=mvid)
            row = conn.execute("SELECT * FROM trials WHERE trial_id=?", (tid,)).fetchone()
            if row and row["response_text"] is None and row["error"] is None:
                pending.append((row, view, narrative))
        log_fn(f"Grammar narrative_only pending trials: {len(pending)} of {len(planned)}")

        completed = 0
        errors = 0

        def process(item):
            row, view, narrative = item
            return run_trial(model_config, extraction_config, row, view,
                             variant_narrative=narrative)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process, it): it for it in pending}
            for future in as_completed(futures):
                row, view, narrative = futures[future]
                try:
                    result = future.result()
                    trial_id = result[0]
                    db.update_trial_response(conn, trial_id, result[1], result[2],
                                             result[5], error=result[4])
                    predicted = result[6] if len(result) > 6 else None
                    if predicted is not None:
                        pred_mapped, correct, acc = grade_prediction(view, predicted)
                        db.update_trial_evaluation(conn, trial_id, json.dumps(pred_mapped),
                                                   result[3], correct, acc)
                        status = "correct" if correct else f"wrong ({acc:.1%})"
                    else:
                        status = f"parse_error: {result[4]}"
                        errors += 1
                    completed += 1
                    log_fn(f"  [{completed}/{len(pending)}] {row['puzzle_id']}/"
                           f"grammar/narrative_only: {status}")
                except Exception as e:
                    log_fn(f"  ERROR {row['puzzle_id']}/grammar/narrative_only: {e}")
                    errors += 1

        log_fn(f"\nDone: {completed} completed, {errors} errors")
        return {"pending": len(pending), "completed": completed, "errors": errors}
    finally:
        conn.close()


def classify_tiny(model, pids, log_fn=print):
    total = narc = 0
    for pid in pids:
        res = run_classify_job(model=model, puzzle=pid, log_fn=lambda *_a, **_k: None)
        total += res["total"]
        narc += res["narc"]
    log_fn(f"  classify {model}: {total} cells, {narc} NARC")


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--all-models", is_flag=True,
              help="Every model with trials on the NARC-tiny puzzles (overrides --model)")
@click.option("--concurrency", default=8, type=int, help="Max parallel requests")
@click.option("--skip-narrative-only", is_flag=True,
              help="Skip step 1 (grammar text alone)")
@click.option("--dry-run", is_flag=True,
              help="List planned trials without calling the API or writing rows")
def main(model, all_models, concurrency, skip_narrative_only, dry_run):
    conn = db.init_db()
    pids = tiny_puzzle_ids(conn)
    if all_models:
        configured = {m["name"] for m in load_config()["models"]}
        targets = [r[0] for r in conn.execute(
            "SELECT DISTINCT t.model_name FROM trials t JOIN puzzles p ON p.puzzle_id=t.puzzle_id "
            "WHERE p.creator=? ORDER BY 1", (CREATOR,)) if r[0] in configured]
    else:
        targets = [model]
    conn.close()
    click.echo(f"NARC-tiny: {len(pids)} puzzles, targets: {targets}")
    if not pids:
        return
    for m in targets:
        click.echo(f"\n===== {m} =====")
        if not skip_narrative_only:
            click.echo(f"----- grammar text alone {m} -----")
            res = run_grammar_narrative_only(m, concurrency=concurrency, dry_run=dry_run,
                                             log_fn=click.echo)
            click.echo(f"  grammar narrative_only: {res}")
        if not dry_run:
            classify_tiny(m, pids, log_fn=click.echo)
        click.echo(f"----- order sensitivity {m} -----")
        res = run_sensitivity_job(model=m, puzzle=pids, concurrency=concurrency,
                                  dry_run=dry_run, log_fn=click.echo)
        click.echo(f"  sensitivity: {res}")
        click.echo(f"----- keyword ablation {m} -----")
        res = run_narrative_sensitivity_job(model=m, puzzle=pids, concurrency=concurrency,
                                            dry_run=dry_run, log_fn=click.echo)
        click.echo(f"  narrative-sensitivity: {res}")
        if not dry_run:
            classify_tiny(m, pids, log_fn=click.echo)


if __name__ == "__main__":
    main()

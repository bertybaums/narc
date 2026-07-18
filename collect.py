"""Phase 1: Run 3-condition testing on subject models via MindRouter.

CLI:
    python collect.py [--model MODEL] [--puzzle PUZZLE_ID] [--condition CONDITION]

In-process (e.g., from server.py):
    from collect import run_collect_job
    run_collect_job(model="gpt-oss-120b", puzzle="narc_001", log_fn=log.write)
"""

import hashlib
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
    elif condition in ("both", "both_shuffled"):
        # both_shuffled is the same prompt over a shuffled-order view (built by
        # the caller); grading uses that view's answer_grids.
        messages = prompts.build_both(puzzle_data, narrative=variant_narrative)
    elif condition == "both_keywords":
        # Narrative-sensitivity: same grids, clue replaced by its key terms.
        messages = prompts.build_both_keywords(puzzle_data, narrative=variant_narrative)
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


def grade_prediction(puzzle_data, predicted):
    """Grade a predicted grids dict against a puzzle_data view's answer_grids.
    Returns (pred_mapped, correct01, cell_accuracy). puzzle_data may be a mask
    variant view (its answer_grids/masked_positions define what is graded)."""
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
    return pred_mapped, (1 if all_correct else 0), cell_accuracy


def run_matrix_job(model, puzzle=None, concurrency=8, dry_run=False,
                   include_original_pair=False, log_fn=print):
    """Run the test matrix: every enabled (narrative variant x mask variant) pair.

    Each pair uses the mask variant's positions (via grids.remask, which hides
    the chosen grids and derives their answers) and the narrative variant's clue.
    Trials are tagged with both variant_id and mask_variant_id. Returns dict with
    keys: pending, completed, errors.

    The (original narrative x original mask) cell is skipped by default — it is
    exactly the base 3-condition run that run_collect_job stores under
    variant_id NULL, so re-running it here would duplicate those API calls.
    Pass include_original_pair=True to force it.
    """
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conditions = config["experiment"]["conditions"]

    conn = db.init_db()
    try:
        if puzzle:
            pids = [puzzle]
        else:
            pids = [r["puzzle_id"] for r in conn.execute(
                "SELECT DISTINCT puzzle_id FROM variant_pairs WHERE enabled=1"
            ).fetchall()]

        # Enumerate planned trials first (no DB writes) so --dry-run is read-only.
        planned = []  # (pid, variant_id, mask_variant_id, cond, prompt_text, view, narrative)
        for pid in pids:
            base_row = db.get_puzzle(conn, pid)
            if not base_row:
                log_fn(f"  skip {pid}: not found")
                continue
            base = db.puzzle_to_json(base_row)
            view_cache = {}  # mask_variant_id -> remasked view
            for pr in db.get_enabled_pairs(conn, pid):
                if (not include_original_pair
                        and pr["narrative_label"] == "original"
                        and pr["mask_label"] == "original"):
                    continue  # covered by run_collect_job under variant_id NULL
                mvid = pr["mask_variant_id"]
                if mvid not in view_cache:
                    view_cache[mvid] = grids.remask(base, json.loads(pr["masked_positions"]))
                view = view_cache[mvid]
                narrative = pr["narrative"]
                for cond in conditions:
                    if cond == "grids_only":
                        msgs = prompts.build_grids_only(view)
                    elif cond == "narrative_only":
                        msgs = prompts.build_narrative_only(view, narrative=narrative)
                    else:
                        msgs = prompts.build_both(view, narrative=narrative)
                    planned.append((pid, pr["variant_id"], mvid, cond,
                                    json.dumps(msgs), view, narrative))

        if dry_run:
            for pid, vid, mvid, cond, _pt, _v, _n in planned:
                log_fn(f"  would run {pid} v={vid} m={mvid} {cond}")
            log_fn(f"Matrix planned trials: {len(planned)} across {len(pids)} puzzle(s)")
            return {"pending": len(planned), "completed": 0, "errors": 0}

        pending = []
        for pid, vid, mvid, cond, prompt_text, view, narrative in planned:
            tid = db.insert_trial(conn, pid, model, cond, prompt_text,
                                  variant_id=vid, mask_variant_id=mvid)
            row = conn.execute("SELECT * FROM trials WHERE trial_id=?", (tid,)).fetchone()
            if row and row["response_text"] is None and row["error"] is None:
                pending.append((row, view, narrative))

        log_fn(f"Matrix pending trials: {len(pending)} across {len(pids)} puzzle(s)")

        completed = 0
        errors = 0

        def process(item):
            row, view, narrative = item
            return run_trial(model_config, extraction_config, row, view,
                             variant_narrative=narrative), view

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process, it): it for it in pending}
            for future in as_completed(futures):
                row, view, narrative = futures[future]
                try:
                    result, view = future.result()
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
                           f"m{row['mask_variant_id']}/{row['condition']}: {status}")
                except Exception as e:
                    log_fn(f"  ERROR {row['puzzle_id']}/{row['condition']}: {e}")
                    errors += 1

        log_fn(f"\nDone: {completed} completed, {errors} errors")
        return {"pending": len(pending), "completed": completed, "errors": errors}
    finally:
        conn.close()


def _resolve_narrative(conn, base, variant_id):
    """Narrative text for a classification cell: the variant's clue, or the base
    puzzle narrative when variant_id is NULL."""
    if variant_id is None:
        return base["narrative"]
    row = conn.execute(
        "SELECT narrative FROM narrative_variants WHERE variant_id=?", (variant_id,)
    ).fetchone()
    return row["narrative"] if row and row["narrative"] else base["narrative"]


def _factorial(n):
    f = 1
    for i in range(2, n + 1):
        f *= i
    return f


def run_sensitivity_job(model, puzzle=None, shuffles=None, concurrency=8,
                        dry_run=False, log_fn=print):
    """Order-sensitivity (weak/strong NARC) test.

    For every NARC cell (has_narc=1) of `model`, re-run the winning `both`
    condition over K deterministically-shuffled grid orders (same grids, same
    narrative). Trials are stored with condition='both_shuffled', repeat_num=1..K,
    tagged with the cell's variant_id + mask_variant_id. classify.py then reads
    these to label the cell strong / partial / weak. Idempotent: skips trials
    that already have a response; --dry-run is read-only. Returns dict with keys:
    pending, completed, errors, cells.
    """
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    if shuffles is None:
        shuffles = config.get("experiment", {}).get("shuffles", 3)

    conn = db.init_db()
    try:
        sql = ("SELECT puzzle_id, variant_id, mask_variant_id FROM classifications "
               "WHERE has_narc=1 AND model_name=?")
        params = [model]
        if puzzle:
            sql += " AND puzzle_id=?"
            params.append(puzzle)
        cells = conn.execute(sql, tuple(params)).fetchall()

        # Enumerate planned trials first (no DB writes) so --dry-run is read-only.
        planned = []  # (pid, vid, mvid, k, prompt_text, view, narrative)
        base_cache = {}
        for c in cells:
            pid, vid, mvid = c["puzzle_id"], c["variant_id"], c["mask_variant_id"]
            if pid not in base_cache:
                row = db.get_puzzle(conn, pid)
                base_cache[pid] = db.puzzle_to_json(row) if row else None
            base = base_cache[pid]
            if base is None:
                log_fn(f"  skip {pid}: not found")
                continue
            mv = db.get_mask_variant(conn, mvid) if mvid is not None else None
            if mvid is not None and not mv:
                log_fn(f"  skip {pid} m={mvid}: mask variant missing")
                continue
            masked_positions = (json.loads(mv["masked_positions"]) if mv
                                else base["masked_positions"])
            narrative = _resolve_narrative(conn, base, vid)

            n = len(base["sequence"])
            k_target = min(shuffles, max(0, _factorial(n) - 1))
            seen_orders = set()
            k, attempt = 0, 0
            while k < k_target and attempt < k_target * 20 + 50:
                seed = int(hashlib.sha256(
                    f"{pid}|{vid}|{mvid}|{attempt}".encode()).hexdigest(), 16) % (2**32)
                view, order = grids.shuffle_view(base, masked_positions, seed)
                attempt += 1
                if order in seen_orders:
                    continue
                seen_orders.add(order)
                k += 1
                msgs = prompts.build_both(view, narrative=narrative)
                planned.append((pid, vid, mvid, k, json.dumps(msgs), view, narrative))

        if dry_run:
            for pid, vid, mvid, k, _pt, _v, _n in planned:
                log_fn(f"  would run {pid} v={vid} m={mvid} both_shuffled#{k}")
            log_fn(f"Sensitivity planned trials: {len(planned)} across "
                   f"{len(cells)} NARC cell(s)")
            return {"pending": len(planned), "completed": 0, "errors": 0,
                    "cells": len(cells)}

        pending = []
        for pid, vid, mvid, k, prompt_text, view, narrative in planned:
            tid = db.insert_trial(conn, pid, model, "both_shuffled", prompt_text,
                                  variant_id=vid, mask_variant_id=mvid, repeat_num=k)
            row = conn.execute("SELECT * FROM trials WHERE trial_id=?", (tid,)).fetchone()
            if row and row["response_text"] is None and row["error"] is None:
                pending.append((row, view, narrative))

        log_fn(f"Sensitivity pending trials: {len(pending)} across "
               f"{len(cells)} NARC cell(s)")

        completed = 0
        errors = 0

        def process(item):
            row, view, narrative = item
            return run_trial(model_config, extraction_config, row, view,
                             variant_narrative=narrative), view

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process, it): it for it in pending}
            for future in as_completed(futures):
                row, view, narrative = futures[future]
                try:
                    result, view = future.result()
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
                           f"m{row['mask_variant_id']}/shuf#{row['repeat_num']}: {status}")
                except Exception as e:
                    log_fn(f"  ERROR {row['puzzle_id']}/both_shuffled: {e}")
                    errors += 1

        log_fn(f"\nDone: {completed} completed, {errors} errors")
        return {"pending": len(pending), "completed": completed, "errors": errors,
                "cells": len(cells)}
    finally:
        conn.close()


def run_narrative_sensitivity_job(model, puzzle=None, repeats=None, concurrency=8,
                                  dry_run=False, log_fn=print):
    """Narrative-sensitivity (lexical vs narrative NARC) test.

    For every NARC cell (has_narc=1) of `model`, re-run the winning `both`
    condition with the clue replaced by its alphabetized key terms (same grids,
    same mask). The keyword prompt is deterministic, so the K repeats measure
    model sampling noise only. Trials are stored with condition='both_keywords',
    repeat_num=1..K, tagged with the cell's variant_id + mask_variant_id.
    classify.py then reads these to label the cell narrative / partial /
    lexical. Idempotent: skips trials that already have a response; --dry-run
    is read-only. Returns dict with keys: pending, completed, errors, cells.
    """
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    if repeats is None:
        repeats = config.get("experiment", {}).get("keyword_repeats", 3)

    conn = db.init_db()
    try:
        sql = ("SELECT puzzle_id, variant_id, mask_variant_id FROM classifications "
               "WHERE has_narc=1 AND model_name=?")
        params = [model]
        if puzzle:
            sql += " AND puzzle_id=?"
            params.append(puzzle)
        cells = conn.execute(sql, tuple(params)).fetchall()

        # Enumerate planned trials first (no DB writes) so --dry-run is read-only.
        planned = []  # (pid, vid, mvid, k, prompt_text, view, narrative)
        base_cache = {}
        for c in cells:
            pid, vid, mvid = c["puzzle_id"], c["variant_id"], c["mask_variant_id"]
            if pid not in base_cache:
                row = db.get_puzzle(conn, pid)
                base_cache[pid] = db.puzzle_to_json(row) if row else None
            base = base_cache[pid]
            if base is None:
                log_fn(f"  skip {pid}: not found")
                continue
            mv = db.get_mask_variant(conn, mvid) if mvid is not None else None
            if mvid is not None and not mv:
                log_fn(f"  skip {pid} m={mvid}: mask variant missing")
                continue
            masked_positions = (json.loads(mv["masked_positions"]) if mv
                                else base["masked_positions"])
            narrative = _resolve_narrative(conn, base, vid)
            if not prompts.extract_keywords(narrative):
                log_fn(f"  skip {pid} v={vid}: no keywords survive extraction")
                continue
            view = grids.remask(base, masked_positions)
            msgs = prompts.build_both_keywords(view, narrative=narrative)
            prompt_text = json.dumps(msgs)
            for k in range(1, repeats + 1):
                planned.append((pid, vid, mvid, k, prompt_text, view, narrative))

        if dry_run:
            for pid, vid, mvid, k, _pt, _v, _n in planned:
                log_fn(f"  would run {pid} v={vid} m={mvid} both_keywords#{k}")
            log_fn(f"Narrative-sensitivity planned trials: {len(planned)} across "
                   f"{len(cells)} NARC cell(s)")
            return {"pending": len(planned), "completed": 0, "errors": 0,
                    "cells": len(cells)}

        pending = []
        for pid, vid, mvid, k, prompt_text, view, narrative in planned:
            tid = db.insert_trial(conn, pid, model, "both_keywords", prompt_text,
                                  variant_id=vid, mask_variant_id=mvid, repeat_num=k)
            row = conn.execute("SELECT * FROM trials WHERE trial_id=?", (tid,)).fetchone()
            if row and row["response_text"] is None and row["error"] is None:
                pending.append((row, view, narrative))

        log_fn(f"Narrative-sensitivity pending trials: {len(pending)} across "
               f"{len(cells)} NARC cell(s)")

        completed = 0
        errors = 0

        def process(item):
            row, view, narrative = item
            return run_trial(model_config, extraction_config, row, view,
                             variant_narrative=narrative), view

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process, it): it for it in pending}
            for future in as_completed(futures):
                row, view, narrative = futures[future]
                try:
                    result, view = future.result()
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
                           f"m{row['mask_variant_id']}/kw#{row['repeat_num']}: {status}")
                except Exception as e:
                    log_fn(f"  ERROR {row['puzzle_id']}/both_keywords: {e}")
                    errors += 1

        log_fn(f"\nDone: {completed} completed, {errors} errors")
        return {"pending": len(pending), "completed": completed, "errors": errors,
                "cells": len(cells)}
    finally:
        conn.close()


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
            orig_mask_id = db.get_original_mask_variant_id(conn, puzzle_data["puzzle_id"])
            for cond in conditions:
                prompt_text = json.dumps(
                    prompts.build_grids_only(puzzle_data) if cond == "grids_only"
                    else prompts.build_narrative_only(puzzle_data) if cond == "narrative_only"
                    else prompts.build_both(puzzle_data)
                )
                db.insert_trial(conn, puzzle_data["puzzle_id"], model, cond, prompt_text,
                                mask_variant_id=orig_mask_id)

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

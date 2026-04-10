"""Collect odd-one-out experiment data.

Design: 3 grids from a puzzle + 1 distractor from a different puzzle.
Model must identify which grid doesn't belong.

Two conditions: grids_only, grids_and_narrative.

Puzzle selection: puzzles that are NARC on >= 1 model, plus a representative
sample of stance puzzles. Distractors are chosen from different puzzles with
similar grid dimensions.

Usage:
    python collect_oddoneout.py --model gpt-oss-120b
    python collect_oddoneout.py --model gpt-oss-120b --puzzle narc_042
    python collect_oddoneout.py --model gpt-oss-120b --dry-run
"""

import hashlib
import json
import random
import sqlite3
import string
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import yaml

import db
import models
import prompts_oddoneout as prompts_ooo

CONDITIONS = ["grids_only", "grids_and_narrative"]


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def get_model_config(config, name):
    for m in config["models"]:
        if m["name"] == name:
            return m
    raise ValueError(f"Model {name} not found in config")


def get_candidate_puzzles(conn):
    """Return puzzles that are NARC on >= 1 model, with representative
    stance sampling."""
    conn.row_factory = sqlite3.Row

    # All puzzles with their NARC counts
    rows = conn.execute("""
        SELECT p.puzzle_id, p.stance_group, p.stance,
               COALESCE(SUM(c.has_narc), 0) as narc_count
        FROM puzzles p
        LEFT JOIN classifications c ON p.puzzle_id = c.puzzle_id
        GROUP BY p.puzzle_id
        HAVING narc_count >= 1
        ORDER BY narc_count DESC
    """).fetchall()

    # Separate stance vs non-stance
    non_stance = []
    stance_by_group = defaultdict(list)
    for r in rows:
        if r["stance_group"]:
            stance_by_group[r["stance_group"]].append(dict(r))
        else:
            non_stance.append(r["puzzle_id"])

    # Sample non-stance: up to 8 per category prefix
    by_prefix = defaultdict(list)
    for pid in non_stance:
        if pid.startswith("narc_ai_"):
            by_prefix["narc_ai"].append(pid)
        elif pid.startswith("narc_focal_"):
            by_prefix["narc_focal"].append(pid)
        elif pid.startswith("narc_sp_"):
            by_prefix["narc_sp"].append(pid)
        elif pid.startswith("narc_gap_"):
            by_prefix["narc_gap"].append(pid)
        elif pid.startswith("narc_prism_"):
            by_prefix["narc_prism"].append(pid)
        elif pid.startswith("narc_new_"):
            by_prefix["narc_new"].append(pid)
        elif pid.startswith("narc_0"):
            by_prefix["narc_core"].append(pid)
        elif pid.startswith("iter_"):
            by_prefix["iter"].append(pid)
        elif pid.startswith("comp_"):
            by_prefix["comp"].append(pid)
        elif pid.startswith("hc_"):
            by_prefix["hc"].append(pid)
        elif pid.startswith("synth_"):
            by_prefix["synth"].append(pid)
        elif pid.startswith("Had"):
            by_prefix["human"].append(pid)
        else:
            by_prefix["other"].append(pid)

    selected = []
    for prefix in sorted(by_prefix.keys()):
        selected.extend(by_prefix[prefix][:8])

    # Sample stance: top 10 groups, all 3 stances per group
    stance_groups_sorted = sorted(
        stance_by_group.items(),
        key=lambda x: max(p["narc_count"] for p in x[1]),
        reverse=True,
    )
    for group_name, puzzles in stance_groups_sorted[:10]:
        for p in puzzles:
            selected.append(p["puzzle_id"])

    return selected


def pick_distractor(conn, puzzle_data, all_puzzle_ids, rng):
    """Pick a distractor grid from a different puzzle with similar dimensions.

    Returns (distractor_grid, distractor_puzzle_id) or (None, None).
    """
    target_pid = puzzle_data["puzzle_id"]
    # Get a non-masked grid's dimensions for matching
    seq = puzzle_data["sequence"]
    masked = set(puzzle_data["masked_positions"])
    visible_grids = [item for item in seq if item["position"] not in masked]
    if not visible_grids:
        return None, None
    ref = visible_grids[0]
    ref_rows, ref_cols = ref["rows"], ref["cols"]

    # Try to find a distractor with matching dimensions
    candidates = [pid for pid in all_puzzle_ids if pid != target_pid]
    rng.shuffle(candidates)

    for cand_pid in candidates[:50]:  # Check up to 50
        row = db.get_puzzle(conn, cand_pid)
        if not row:
            continue
        cand = db.puzzle_to_json(row)
        cand_masked = set(cand["masked_positions"])
        for item in cand["sequence"]:
            if item["position"] not in cand_masked and item.get("grid"):
                if item["rows"] == ref_rows and item["cols"] == ref_cols:
                    return item["grid"], cand_pid
    # Fallback: any grid from any other puzzle
    for cand_pid in candidates[:50]:
        row = db.get_puzzle(conn, cand_pid)
        if not row:
            continue
        cand = db.puzzle_to_json(row)
        cand_masked = set(cand["masked_positions"])
        for item in cand["sequence"]:
            if item["position"] not in cand_masked and item.get("grid"):
                return item["grid"], cand_pid
    return None, None


def select_puzzle_grids(puzzle_data, rng):
    """Select 3 visible grids from the puzzle.

    Returns list of 3 grids (2D arrays).
    """
    seq = puzzle_data["sequence"]
    masked = set(puzzle_data["masked_positions"])
    visible = [item for item in seq if item["position"] not in masked
               and item.get("grid")]

    if len(visible) < 3:
        # Include answer grids if not enough visible
        answer_grids = puzzle_data.get("answer_grids", {})
        for pos in puzzle_data["masked_positions"]:
            ag = answer_grids.get(str(pos))
            if ag:
                visible.append({"position": pos, "grid": ag,
                                "rows": len(ag), "cols": len(ag[0])})
    if len(visible) < 3:
        return None

    selected = rng.sample(visible, min(3, len(visible)))
    while len(selected) < 3:
        selected.append(rng.choice(visible))
    return [item["grid"] for item in selected]


def parse_oddoneout_response(response_text):
    """Parse model response to extract predicted odd-one-out.

    Returns (predicted_letter, reasoning, error).
    """
    if not response_text:
        return None, None, "Empty response"

    text = response_text.strip()

    # Try JSON parsing
    json_text = text
    if "```json" in json_text:
        try:
            start = json_text.index("```json") + 7
            end = json_text.index("```", start)
            json_text = json_text[start:end].strip()
        except ValueError:
            pass

    data = _try_parse_json(json_text)
    if data is None:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            data = _try_parse_json(text[brace_start:brace_end + 1])

    if data is not None:
        reasoning = data.get("reasoning", "")
        answer = data.get("odd_one_out", "")
        if isinstance(answer, str) and answer.strip().upper() in "ABCD":
            return answer.strip().upper(), reasoning, None
        return None, reasoning, f"Invalid answer: {answer}"

    # Fallback: look for a single letter A-D
    import re
    match = re.search(r'\b([A-D])\b', text[-100:])
    if match:
        return match.group(1), text, None

    return None, text, "Could not parse answer"


def _try_parse_json(text):
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def run_oddoneout_trial(model_config, extraction_config, puzzle_data,
                        distractor_grid, distractor_pos, condition):
    """Run a single odd-one-out trial."""
    puzzle_grids = select_puzzle_grids(
        puzzle_data, random.Random(hash(puzzle_data["puzzle_id"]))
    )
    if puzzle_grids is None:
        return {"error": "Not enough grids", "predicted_odd": None,
                "latency_ms": 0}

    if condition == "grids_only":
        messages, correct = prompts_ooo.build_oddoneout_grids_only(
            puzzle_grids, distractor_grid, distractor_pos)
    else:
        messages, correct = prompts_ooo.build_oddoneout_grids_and_narrative(
            puzzle_grids, distractor_grid, distractor_pos,
            puzzle_data["narrative"])

    try:
        raw1, reasoning, raw2, text2, total_latency = models.call_llm_two_pass(
            model_config, messages, prompts_ooo.build_oddoneout_extraction,
            extraction_config)
    except Exception as e:
        return {"error": str(e), "predicted_odd": None, "reasoning": None,
                "raw_response": None, "response_text": None, "latency_ms": 0,
                "prompt_text": messages[1]["content"],
                "correct_label": correct}

    predicted, parsed_reasoning, parse_error = parse_oddoneout_response(text2)
    if predicted is None:
        predicted, _, _ = parse_oddoneout_response(reasoning)

    return {
        "correct_label": correct,
        "predicted_odd": predicted,
        "raw_response": raw1,
        "response_text": text2,
        "reasoning": reasoning,
        "error": parse_error if predicted is None else None,
        "latency_ms": total_latency,
        "prompt_text": messages[1]["content"],
    }


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--puzzle", default=None, help="Single puzzle ID")
@click.option("--condition", default=None, type=click.Choice(CONDITIONS))
@click.option("--concurrency", default=8, type=int)
@click.option("--dry-run", is_flag=True)
def main(model, puzzle, condition, concurrency, dry_run):
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conditions = [condition] if condition else CONDITIONS

    conn = db.init_db()

    # Get candidate puzzles
    if puzzle:
        candidate_ids = [puzzle]
    else:
        candidate_ids = get_candidate_puzzles(conn)

    # Load puzzle data
    all_puzzle_ids = [r["puzzle_id"] for r in db.get_all_puzzles(conn)]
    puzzles_data = []
    for pid in candidate_ids:
        row = db.get_puzzle(conn, pid)
        if row:
            puzzles_data.append(db.puzzle_to_json(row))

    click.echo(f"Odd-one-out: {len(puzzles_data)} puzzles x {len(conditions)} "
               f"conditions on {model}")

    # Deterministic RNG per puzzle for distractor selection
    work = []
    skipped = 0
    for pdata in puzzles_data:
        pid = pdata["puzzle_id"]
        rng = random.Random(hashlib.md5(pid.encode()).hexdigest())

        # Pick distractor
        distractor_grid, distractor_pid = pick_distractor(
            conn, pdata, all_puzzle_ids, rng)
        if distractor_grid is None:
            skipped += 1
            continue

        # Deterministic distractor position
        distractor_pos = rng.randint(0, 3)

        for cond in conditions:
            # Skip if already done
            existing = conn.execute(
                """SELECT trial_id FROM oddoneout_trials
                   WHERE puzzle_id=? AND distractor_id=? AND model_name=?
                         AND condition=? AND predicted_odd IS NOT NULL""",
                (pid, distractor_pid, model, cond),
            ).fetchone()
            if existing:
                continue

            work.append((pdata, distractor_grid, distractor_pid,
                         distractor_pos, cond))

    click.echo(f"Pending: {len(work)} trials "
               f"({len(puzzles_data) * len(conditions) - len(work)} done, "
               f"{skipped} skipped - not enough grids)")

    if dry_run:
        for pdata, _, dist_pid, _, cond in work[:20]:
            click.echo(f"  Would run: {pdata['puzzle_id']} "
                       f"(distractor: {dist_pid}) / {cond}")
        if len(work) > 20:
            click.echo(f"  ... and {len(work) - 20} more")
        return

    if not work:
        click.echo("Nothing to do.")
        return

    completed = 0
    errors = 0

    def process(item):
        pdata, dist_grid, dist_pid, dist_pos, cond = item
        result = run_oddoneout_trial(
            model_config, extraction_config, pdata,
            dist_grid, dist_pos, cond)
        return pdata, dist_pid, dist_pos, cond, result

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process, item): item for item in work}
        for future in as_completed(futures):
            try:
                pdata, dist_pid, dist_pos, cond, result = future.result()
                pid = pdata["puzzle_id"]

                predicted = result.get("predicted_odd")
                correct_label = result.get("correct_label",
                                           string.ascii_uppercase[dist_pos])
                correct_idx = ord(correct_label) - ord("A")
                predicted_idx = (ord(predicted) - ord("A")) if predicted else None
                is_correct = 1 if predicted == correct_label else 0 if predicted else None

                conn.execute(
                    """INSERT OR REPLACE INTO oddoneout_trials
                       (puzzle_id, distractor_id, model_name, condition,
                        prompt_text, raw_response, response_text, reasoning,
                        predicted_odd, correct_odd, correct, error,
                        latency_ms, response_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               datetime('now'))""",
                    (pid, dist_pid, model, cond,
                     result.get("prompt_text"), result.get("raw_response"),
                     result.get("response_text"), result.get("reasoning"),
                     predicted_idx, correct_idx, is_correct,
                     result.get("error"), result.get("latency_ms")),
                )
                conn.commit()

                completed += 1
                if predicted:
                    status = ("CORRECT" if is_correct else
                              f"WRONG (said {predicted}, was {correct_label})")
                else:
                    status = f"parse_error: {result.get('error')}"
                    errors += 1

                click.echo(f"  [{completed}/{len(work)}] {pid} / {cond}: {status}")

            except Exception as e:
                item = futures[future]
                click.echo(f"  ERROR {item[0]['puzzle_id']}/{item[4]}: {e}")
                errors += 1

    # Summary
    click.echo(f"\nDone: {completed} completed, {errors} errors")

    rows = conn.execute(
        """SELECT condition, COUNT(*) as n,
                  SUM(correct) as correct_count
           FROM oddoneout_trials
           WHERE model_name=? AND correct IS NOT NULL
           GROUP BY condition""",
        (model,),
    ).fetchall()

    if rows:
        click.echo(f"\n{'Condition':<25} {'N':>5} {'Accuracy':>10}")
        click.echo("-" * 44)
        for r in rows:
            acc = r["correct_count"] / r["n"] * 100 if r["n"] else 0
            click.echo(f"{r['condition']:<25} {r['n']:>5} "
                       f"{acc:>9.1f}%")


if __name__ == "__main__":
    main()

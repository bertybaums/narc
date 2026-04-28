"""Collect ordering experiment data: can models recover grid sequence order?

Two conditions:
  - grids_only: shuffled grids, no narrative
  - grids_and_narrative: shuffled grids + narrative clue

Eligible puzzles: active (not draft), 4+ grids.

Usage:
    python collect_ordering.py --model gpt-oss-120b
    python collect_ordering.py --model gpt-oss-120b --puzzle narc_042
    python collect_ordering.py --model gpt-oss-120b --condition grids_only
    python collect_ordering.py --model gpt-oss-120b --dry-run
"""

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import yaml

import db
import models
import prompts_ordering as prompts_ord


ORDERING_CONDITIONS = ["grids_only", "grids_and_narrative"]


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def get_model_config(config, name):
    for m in config["models"]:
        if m["name"] == name:
            return m
    raise ValueError(f"Model {name} not found in config")


def ensure_ordering_tables(conn):
    """Create ordering-specific tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ordering_trials (
            trial_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
            model_name     TEXT NOT NULL,
            condition      TEXT NOT NULL,
            repeat_num     INTEGER DEFAULT 1,
            prompt_text    TEXT,
            correct_order  TEXT NOT NULL,
            raw_response   TEXT,
            response_text  TEXT,
            response_at    TEXT,
            latency_ms     INTEGER,
            error          TEXT,
            predicted_order TEXT,
            reasoning      TEXT,
            exact_match    INTEGER,
            kendall_tau    REAL,
            UNIQUE(puzzle_id, model_name, condition, repeat_num)
        );
    """)


def get_active_puzzle_ids(conn):
    """Return puzzle_ids that are not drafts (status != 'draft')."""
    rows = conn.execute(
        "SELECT puzzle_id FROM puzzles WHERE status != 'draft'"
    ).fetchall()
    return [r["puzzle_id"] for r in rows]


def get_eligible_puzzles(conn, single_puzzle=None):
    """Return active puzzles with 4+ grids."""
    active_ids = set(get_active_puzzle_ids(conn))

    if single_puzzle:
        row = db.get_puzzle(conn, single_puzzle)
        if not row:
            raise ValueError(f"Puzzle {single_puzzle} not found")
        pdata = db.puzzle_to_json(row)
        n = len(pdata["sequence"])
        if n < 4:
            raise ValueError(f"{single_puzzle} has {n} grids (need 4+)")
        if single_puzzle not in active_ids:
            click.echo(f"Warning: {single_puzzle} is a draft puzzle")
        return [pdata]

    all_puzzles = db.get_all_puzzles(conn)
    eligible = []
    for row in all_puzzles:
        pdata = db.puzzle_to_json(row)
        if pdata["puzzle_id"] not in active_ids:
            continue
        if len(pdata["sequence"]) >= 4:
            eligible.append(pdata)
    return eligible


def parse_ordering_response(response_text, expected_labels):
    """Parse model response to extract predicted ordering.

    Returns (predicted_order: list|None, reasoning: str|None, error: str|None).
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
        order = data.get("order")
        if isinstance(order, list):
            # Normalize to uppercase strings
            order = [str(x).strip().upper() for x in order]
            # Validate: must contain exactly the expected labels
            if set(order) == set(expected_labels) and len(order) == len(expected_labels):
                return order, reasoning, None
            else:
                return None, reasoning, f"Invalid labels: got {order}, expected {expected_labels}"
        return None, reasoning, "No 'order' array in response JSON"

    # Fallback: look for a sequence of letters in brackets
    import re
    bracket_match = re.search(r'\[([A-Z](?:\s*,\s*[A-Z])*)\]', text)
    if bracket_match:
        order = [x.strip() for x in bracket_match.group(1).split(",")]
        if set(order) == set(expected_labels) and len(order) == len(expected_labels):
            return order, text, None

    return None, text, "Could not parse ordering from response"


def _try_parse_json(text):
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def kendall_tau(predicted, correct):
    """Compute Kendall tau correlation between two orderings.

    Both are lists of labels in some order. Returns tau in [-1, 1].
    +1 = perfect agreement, -1 = perfectly reversed, 0 = no correlation.
    """
    n = len(correct)
    if n < 2:
        return 1.0

    # Build rank maps
    correct_rank = {label: i for i, label in enumerate(correct)}
    pred_rank = {label: i for i, label in enumerate(predicted)}

    # Count concordant and discordant pairs
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            li, lj = correct[i], correct[j]
            # In correct order, li comes before lj
            # Check if same in predicted
            if pred_rank[li] < pred_rank[lj]:
                concordant += 1
            else:
                discordant += 1

    total_pairs = n * (n - 1) // 2
    return (concordant - discordant) / total_pairs


def run_ordering_trial(model_config, extraction_config, puzzle_data, condition):
    """Run a single ordering trial."""
    # Build prompt
    if condition == "grids_only":
        messages, correct_order = prompts_ord.build_ordering_grids_only(puzzle_data)
    elif condition == "grids_and_narrative":
        messages, correct_order = prompts_ord.build_ordering_grids_and_narrative(puzzle_data)
    else:
        raise ValueError(f"Unknown condition: {condition}")

    # Two-pass call
    try:
        raw1, reasoning, raw2, text2, total_latency = models.call_llm_two_pass(
            model_config, messages, prompts_ord.build_ordering_extraction,
            extraction_config
        )
    except Exception as e:
        return {
            "correct_order": correct_order,
            "error": str(e),
            "raw_response": None,
            "response_text": None,
            "reasoning": None,
            "predicted_order": None,
            "latency_ms": 0,
        }

    # Parse response — try pass-2 first, then raw reasoning
    import string
    n = len(correct_order)
    expected_labels = list(string.ascii_uppercase[:n])

    predicted, parsed_reasoning, parse_error = parse_ordering_response(
        text2, expected_labels
    )
    if predicted is None:
        predicted, _, _ = parse_ordering_response(reasoning, expected_labels)

    return {
        "correct_order": correct_order,
        "raw_response": raw1,
        "response_text": text2,
        "reasoning": reasoning,
        "predicted_order": predicted,
        "error": parse_error if predicted is None else None,
        "latency_ms": total_latency,
    }


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--puzzle", default=None, help="Single puzzle ID (default: all eligible)")
@click.option("--condition", default=None,
              type=click.Choice(ORDERING_CONDITIONS),
              help="Single condition (default: both)")
@click.option("--concurrency", default=8, type=int, help="Max parallel requests")
@click.option("--dry-run", is_flag=True, help="Show what would be done")
def main(model, puzzle, condition, concurrency, dry_run):
    config = load_config()
    model_config = get_model_config(config, model)
    extraction_config = get_model_config(config, "gpt-oss-120b-extract")
    conditions = [condition] if condition else ORDERING_CONDITIONS

    conn = db.init_db()
    ensure_ordering_tables(conn)

    # Get eligible puzzles
    eligible = get_eligible_puzzles(conn, single_puzzle=puzzle)
    click.echo(f"Ordering experiment: {len(eligible)} puzzles x {len(conditions)} "
               f"conditions on {model}")

    # Build work items, skip already-completed
    work = []
    for pdata in eligible:
        pid = pdata["puzzle_id"]
        for cond in conditions:
            # Check if already done
            existing = conn.execute(
                """SELECT trial_id FROM ordering_trials
                   WHERE puzzle_id=? AND model_name=? AND condition=?
                         AND predicted_order IS NOT NULL""",
                (pid, model, cond),
            ).fetchone()
            if existing:
                continue
            work.append((pdata, cond))

    click.echo(f"Pending: {len(work)} trials ({len(eligible) * len(conditions) - len(work)} already done)")

    if dry_run:
        for pdata, cond in work[:20]:
            n = len(pdata["sequence"])
            click.echo(f"  Would run: {pdata['puzzle_id']} ({n} grids) / {cond}")
        if len(work) > 20:
            click.echo(f"  ... and {len(work) - 20} more")
        return

    if not work:
        click.echo("Nothing to do.")
        return

    # Run trials
    completed = 0
    errors = 0

    def process(item):
        pdata, cond = item
        return pdata, cond, run_ordering_trial(model_config, extraction_config, pdata, cond)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process, item): item for item in work}
        for future in as_completed(futures):
            try:
                pdata, cond, result = future.result()
                pid = pdata["puzzle_id"]

                correct_order = result["correct_order"]
                predicted = result["predicted_order"]
                error = result["error"]

                # Compute metrics
                exact = None
                tau = None
                if predicted is not None:
                    exact = 1 if predicted == correct_order else 0
                    tau = kendall_tau(predicted, correct_order)

                # Store in DB
                conn.execute(
                    """INSERT OR REPLACE INTO ordering_trials
                       (puzzle_id, model_name, condition, correct_order,
                        raw_response, response_text, reasoning,
                        predicted_order, error, latency_ms,
                        exact_match, kendall_tau, response_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, datetime('now'))""",
                    (pid, model, cond, json.dumps(correct_order),
                     result["raw_response"], result["response_text"],
                     result["reasoning"], json.dumps(predicted) if predicted else None,
                     error, result["latency_ms"],
                     exact, tau),
                )
                conn.commit()

                completed += 1
                n = len(pdata["sequence"])
                if predicted is not None:
                    status = f"tau={tau:+.3f}" + (" EXACT" if exact else "")
                else:
                    status = f"parse_error: {error}"
                    errors += 1

                click.echo(f"  [{completed}/{len(work)}] {pid} ({n}g) / {cond}: {status}")

            except Exception as e:
                item = futures[future]
                click.echo(f"  ERROR {item[0]['puzzle_id']}/{item[1]}: {e}")
                errors += 1

    # Summary
    click.echo(f"\nDone: {completed} completed, {errors} errors")

    # Print aggregate stats
    rows = conn.execute(
        """SELECT condition, COUNT(*) as n,
                  AVG(kendall_tau) as avg_tau,
                  SUM(exact_match) as exact_count
           FROM ordering_trials
           WHERE model_name=? AND kendall_tau IS NOT NULL
           GROUP BY condition""",
        (model,),
    ).fetchall()

    if rows:
        click.echo(f"\n{'Condition':<25} {'N':>5} {'Avg tau':>10} {'Exact':>8}")
        click.echo("-" * 52)
        for r in rows:
            click.echo(f"{r['condition']:<25} {r['n']:>5} {r['avg_tau']:>+10.3f} "
                       f"{r['exact_count']:>5}/{r['n']}")


if __name__ == "__main__":
    main()

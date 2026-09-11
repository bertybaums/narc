#!/usr/bin/env python3
"""Import closed-book Claude results (narc-iclr2027/experiments/fable_human/run_fable.py) into
narc.db as trials + base-cell classifications for one model, so they show on the Inspect tab.

    python import_fable_results.py --results data/fable_results_2026-09-08.jsonl --model claude-fable-5-1

Idempotent: trials are looked up NULL-safely (db.insert_trial) and classifications replaced
(db.upsert_classification). Only rows with a graded `correct` are imported. Base cell =
variant_id NULL, mask_variant_id NULL, which Inspect labels original x original.
"""
import argparse
import json
import sqlite3

import db
import prompts

CONDITIONS = ("grids_only", "narrative_only", "both")


def build_prompt(puzzle_json, condition):
    if condition == "grids_only":
        msgs = prompts.build_grids_only(puzzle_json)
    elif condition == "narrative_only":
        msgs = prompts.build_narrative_only(puzzle_json)
    else:
        msgs = prompts.build_both(puzzle_json)
    return json.dumps(msgs, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default="claude-fable-5-1")
    ap.add_argument("--db", default="narc.db")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    rows = [json.loads(l) for l in open(a.results)]
    rows = [r for r in rows if r.get("correct") is not None and r.get("condition") in CONDITIONS]
    by_puzzle = {}
    n_trials = 0
    for r in rows:
        pid = r["puzzle_id"]
        prow = conn.execute("SELECT * FROM puzzles WHERE puzzle_id=?", (pid,)).fetchone()
        if prow is None:
            print("skip (no puzzle):", pid)
            continue
        pj = db.puzzle_to_json(prow)
        try:
            prompt_text = build_prompt(pj, r["condition"])
        except Exception as e:  # noqa: BLE001
            prompt_text = json.dumps({"note": f"prompt not rebuilt: {e}"})
        tid = db.insert_trial(conn, pid, a.model, r["condition"], prompt_text)
        db.update_trial_response(conn, tid, r.get("response_text"), r.get("response_text"),
                                 int(round((r.get("latency_s") or 0) * 1000)), r.get("parse_error"))
        conn.execute("UPDATE trials SET response_at=? WHERE trial_id=?", (r.get("ts"), tid))
        db.update_trial_evaluation(conn, tid, json.dumps(r.get("predicted")), None,
                                   int(r["correct"]), float(r.get("cell_accuracy") or 0.0))
        by_puzzle.setdefault(pid, {})[r["condition"]] = int(r["correct"])
        n_trials += 1
    n_cls = n_narc = 0
    for pid, c in by_puzzle.items():
        if not all(k in c for k in CONDITIONS):
            continue
        g, l, b = c["grids_only"], c["narrative_only"], c["both"]
        has_narc = int(g == 0 and l == 0 and b == 1)
        db.upsert_classification(conn, pid, a.model, g, l, b, has_narc)
        n_cls += 1
        n_narc += has_narc
    conn.commit()
    print(f"{a.model}: {n_trials} trials over {len(by_puzzle)} puzzles; {n_cls} classifications, {n_narc} NARC")
    print("models now in trials:", [r[0] for r in conn.execute("SELECT DISTINCT model_name FROM trials ORDER BY 1")])


if __name__ == "__main__":
    main()

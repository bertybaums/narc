#!/usr/bin/env python3
"""Rescore stored trials whose predicted grid was graded under the wrong key.

Background (September 16, 2026): the extraction pass was never told the masked
position, so it keyed lone grids "0", and subject models often wrote the 1-indexed
"Grid N" label as the key. grade_prediction required the exact key, so exactly right
grids were graded wrong. Assessment and counts:
narc-iclr2027/notes/grading-miskey-assessment-2026-09-16.md.

This script re-keys every stored prediction with grids.normalize_prediction_keys and
regrades it with collect.grade_prediction against the trial's own graded positions:
the mask variant's positions, else the puzzle's, and for both_shuffled the slot marked
[MASKED] in the stored prompt (the grid content is unchanged by shuffling). Multi-mask
both_shuffled trials are left alone. No model calls. Trials whose keys already match
are untouched, so re-running after --apply changes nothing.

    python rescore_miskeyed.py                       # dry run (counts, no writes)
    python rescore_miskeyed.py --apply               # one transaction + JSONL audit under data/
    python rescore_miskeyed.py --model qwen3.8-27b --db /path/narc.db --audit-dir data

Prod: snapshot first, then run inside the container so the WAL is the container's:
    docker exec narc-narc-1 python rescore_miskeyed.py
    docker exec narc-narc-1 python rescore_miskeyed.py --apply
Afterwards: python classify.py --model M for every model, then the sensitivity
backfills for NARC cells created by the rescore.
"""
import collections
import json
import os
import re
import sqlite3
import time

import click

import grids
from collect import grade_prediction

MASK_RE = re.compile(r"Grid (\d+) \((\d+)x(\d+)\)[^\n]*?\[MASKED")


def _graded_positions(trial, puzzle_positions, mask_positions):
    """Return the positions this trial was graded at, or None if undeterminable."""
    positions = mask_positions if mask_positions is not None else puzzle_positions
    if trial["condition"] != "both_shuffled":
        return list(positions), list(positions)
    if len(positions) != 1:
        return None, None  # multi-mask shuffled: left strict
    try:
        user = json.loads(trial["prompt_text"])[1]["content"]
    except Exception:
        user = trial["prompt_text"] or ""
    slots = [int(m[0]) - 1 for m in MASK_RE.findall(user)]
    if len(slots) != 1:
        return None, None
    return slots, list(positions)  # graded slot, content position


@click.command()
@click.option("--db", "db_path", default="narc.db", show_default=True)
@click.option("--model", default=None, help="Restrict to one model_name")
@click.option("--apply", "do_apply", is_flag=True, help="Write changes (default: dry run)")
@click.option("--audit-dir", default="data", show_default=True,
              help="Directory for the JSONL audit written on --apply")
def main(db_path, model, do_apply, audit_dir):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    puzzles = {}
    for p in conn.execute("SELECT puzzle_id, sequence_json, answer_grids, masked_positions FROM puzzles"):
        comp = grids.complete_sequence(json.loads(p["sequence_json"]), json.loads(p["answer_grids"]))
        puzzles[p["puzzle_id"]] = (json.loads(p["masked_positions"]),
                                   {it["position"]: it.get("grid") for it in comp})
    mvs = {r["mask_variant_id"]: json.loads(r["masked_positions"])
           for r in conn.execute("SELECT mask_variant_id, masked_positions FROM mask_variants")}

    sql = ("SELECT trial_id, puzzle_id, mask_variant_id, model_name, condition, prompt_text, "
           "predicted_grids, correct, cell_accuracy FROM trials "
           "WHERE correct IS NOT NULL AND predicted_grids IS NOT NULL")
    params = []
    if model:
        sql += " AND model_name=?"
        params.append(model)

    counts = collections.Counter()
    skipped = collections.Counter()
    changes = []
    for t in conn.execute(sql, params):
        try:
            pred = json.loads(t["predicted_grids"])
        except Exception:
            skipped["unparseable predicted_grids"] += 1
            continue
        if not isinstance(pred, dict) or not pred:
            skipped["not a dict"] += 1
            continue
        pz = puzzles.get(t["puzzle_id"])
        if pz is None:
            skipped["puzzle missing"] += 1
            continue
        puzzle_positions, content = pz
        mask_positions = None
        if t["mask_variant_id"] is not None:
            mask_positions = mvs.get(t["mask_variant_id"])
            if mask_positions is None:
                skipped["mask variant missing"] += 1
                continue
        graded, content_pos = _graded_positions(t, puzzle_positions, mask_positions)
        if graded is None:
            skipped["multi-mask shuffled or unreadable prompt"] += 1
            continue
        mapped = grids.normalize_prediction_keys(pred, graded)
        if set(mapped) == set(pred):
            continue  # already keyed at the graded positions (or left strict)
        expected = {}
        for g, cpos in zip(graded, content_pos):
            grid = content.get(cpos)
            if grid is None:
                expected = None
                break
            expected[str(g)] = grid
        if expected is None:
            skipped["no grid at masked position"] += 1
            continue
        view = {"answer_grids": expected, "masked_positions": graded}
        new_mapped, new_correct, new_acc = grade_prediction(view, pred)
        old_correct, old_acc = t["correct"], t["cell_accuracy"] or 0.0
        if len(graded) == 1:
            cat = "A" if new_correct and not old_correct else "B"
        else:
            cat = "D"
        counts[(t["model_name"], t["condition"], cat)] += 1
        if new_correct != old_correct:
            counts[(t["model_name"], t["condition"], "correct_flips")] += 1
        changes.append({"trial_id": t["trial_id"], "puzzle_id": t["puzzle_id"], "model": t["model_name"],
                        "condition": t["condition"], "category": cat,
                        "old_correct": old_correct, "new_correct": new_correct,
                        "old_cell_accuracy": old_acc, "new_cell_accuracy": new_acc,
                        "old_predicted_grids": pred, "new_predicted_grids": new_mapped})

    # ---- report ----
    models_seen = sorted({k[0] for k in counts})
    conds = ["grids_only", "narrative_only", "both", "both_keywords", "both_shuffled"]
    print(f"{'dry run' if not do_apply else 'APPLY'}: {len(changes)} trials to rescore; skipped {dict(skipped)}")
    for cat in ("A", "B", "D", "correct_flips"):
        print(f"\n{cat}:  " + "".join(f"{c:>16}" for c in conds) + f"{'total':>8}")
        for m in models_seen:
            row = [counts[(m, c, cat)] for c in conds]
            if sum(row):
                print(f"  {m:16}" + "".join(f"{x:>16}" for x in row) + f"{sum(row):>8}")
        tot = [sum(counts[(m, c, cat)] for m in models_seen) for c in conds]
        print(f"  {'TOTAL':16}" + "".join(f"{x:>16}" for x in tot) + f"{sum(tot):>8}")

    if not do_apply or not changes:
        conn.close()
        return

    os.makedirs(audit_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    audit_path = os.path.join(audit_dir, f"rescore_miskeyed_{ts}.jsonl")
    with open(audit_path, "w") as f:
        for ch in changes:
            f.write(json.dumps(ch) + "\n")
    conn.execute("BEGIN")
    conn.executemany(
        "UPDATE trials SET correct=?, cell_accuracy=?, predicted_grids=? WHERE trial_id=?",
        [(ch["new_correct"], ch["new_cell_accuracy"], json.dumps(ch["new_predicted_grids"]), ch["trial_id"])
         for ch in changes])
    conn.commit()
    conn.close()
    print(f"\napplied {len(changes)} updates in one transaction; audit: {audit_path}")
    print("next: python classify.py --model M for every model, then the sensitivity backfills")


if __name__ == "__main__":
    main()

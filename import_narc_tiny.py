#!/usr/bin/env python3
"""Import the NARC-tiny sample into narc.db: generated puzzles from narc-tiny-stories (the
Narrative Hierarchy Model run on TinyStories) with the model results already collected for them.

    python import_narc_tiny.py --puzzles data/narc_tiny/puzzles \
        --matrix data/narc_tiny/matrix200.jsonl --order data/narc_tiny/order200.jsonl

What it writes, per puzzle (the way proto._promote_one does):
- a `puzzles` row, creator `narc-tiny`, status active, tags `source:narc-tiny`, `event:<masked
  primitive>`, `grids:`, `size:`, `mask:`, `nts-feature:<TinyStories prompt feature>`;
- two narrative variants: `original` (the TinyStory) and `grammar` (the event chain rendered back
  by the grammar's templates); the `original` mask variant; the (original x original) pair
  enabled, the (grammar x original) pair stored disabled so the corpus-wide matrix job does not
  re-run grids_only for it (collect_narc_tiny.py runs what is missing);
- `data/puzzles/<id>.json` with the full generator metadata (event chain, legend, mask analysis).

Trials (runs of narc-tiny-stories/scripts/06_eval_pilot.py and 20_order_experiments.py, which use
this repo's prompt builders and grader):
- matrix rows: grids_only / narrative_only / both -> base cell (variant NULL); both_grammar ->
  `both` under the grammar variant;
- order rows: both_shuffled / both_grammar_shuffled -> `both_shuffled`, repeat 1..K in seed order,
  last row per trial (re-runs supersede, as in 21_order_report.py).
Those runs kept the last 600 (matrix) or 400 (order) characters of each response and no timestamp,
so `response_text` is that tail, `raw_response` a provenance note, and `response_at` the day the
run finished. An answer with no gradable grid was counted as not solved (correct = 0), and is
imported that way. `predicted_grids` is set only for correct trials (it equals the answer).

Idempotent: an existing puzzle is left alone, an existing answered trial is not overwritten.
Then classify runs for every (puzzle, model). --dry-run reads and reports only.
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import db
import grids
import prompts
from classify import run_classify_job

CREATOR = "narc-tiny"
MATRIX_COND = {"grids_only": ("grids_only", False), "narrative_only": ("narrative_only", False),
               "both": ("both", False), "both_grammar": ("both", True)}
ORDER_COND = {"both_shuffled": False, "both_grammar_shuffled": True}
UNPARSED = "No gradable grid in the response (imported run; counted as not solved)"


def grammar_text(pz):
    return next((v["narrative"] for v in pz.get("narrative_variants", [])
                 if v.get("variant") == "grammar"), None)


def puzzle_tags(pz):
    m = pz["metadata"]
    keep = [t for t in m.get("tags", []) if t.startswith(("grids:", "size:", "mask:", "event:"))]
    keep.append("source:narc-tiny")
    keep += [f"nts-feature:{f}" for f in (m.get("story_features") or [])]
    return sorted(set(keep))


def variant_id(conn, pid, variant):
    r = conn.execute("SELECT variant_id FROM narrative_variants WHERE puzzle_id=? AND variant=?",
                     (pid, variant)).fetchone()
    return r["variant_id"] if r else None


def import_puzzle(conn, pz, export_dir, status):
    """Returns True if the puzzle was written, False if it was already there."""
    pid = pz["puzzle_id"]
    if db.get_puzzle(conn, pid):
        return False
    m = pz["metadata"]
    complete = grids.complete_sequence(pz["sequence"], pz["answer_grids"])
    display, answers = grids.apply_mask(complete, pz["masked_positions"])
    tags = puzzle_tags(pz)
    db.upsert_puzzle(conn, pid, pz["title"], pz["narrative"], json.dumps(display),
                     json.dumps(pz["masked_positions"]), json.dumps(answers),
                     creator=CREATOR, tags=",".join(tags))
    db.set_puzzle_status(conn, pid, status)
    if m.get("created_at"):
        conn.execute("UPDATE puzzles SET created_at=? WHERE puzzle_id=?",
                     (f"{m['created_at']} 00:00:00", pid))
    db.upsert_variant(conn, pid, "original", pz["narrative"], generator="tinystories")
    gid = db.upsert_variant(conn, pid, "grammar", grammar_text(pz), source_domain="grammar text",
                            generator=next(v.get("generator", "backrender")
                                           for v in pz["narrative_variants"]
                                           if v["variant"] == "grammar"))
    mask_id = db.upsert_mask_variant(conn, pid, "original", pz["masked_positions"])
    db.set_variant_pair(conn, pid, variant_id(conn, pid, "original"), mask_id, enabled=1)
    db.set_variant_pair(conn, pid, gid, mask_id, enabled=0)
    data = {"puzzle_id": pid, "title": pz["title"], "narrative": pz["narrative"],
            "sequence": display, "masked_positions": pz["masked_positions"],
            "answer_grids": answers,
            "variants": [{"variant": "grammar", "narrative": grammar_text(pz),
                          "source_domain": "grammar text"}],
            "metadata": {**m, "creator": CREATOR, "tags": tags}}
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / f"{pid}.json").write_text(json.dumps(data, indent=2))
    return True


def store_trial(conn, pz, model, condition, messages, vid, mask_id, repeat, row, source, day, tail):
    """Returns True if the trial was written, False if an answered one was already there."""
    pid = pz["puzzle_id"]
    tid = db.insert_trial(conn, pid, model, condition, json.dumps(messages), variant_id=vid,
                          repeat_num=repeat, mask_variant_id=mask_id)
    cur = conn.execute("SELECT response_text, error FROM trials WHERE trial_id=?",
                       (tid,)).fetchone()
    if cur["response_text"] is not None or cur["error"] is not None:
        return False
    note = {"imported_from": source, "kept": f"last {tail} characters of the response",
            "parsed": bool(row.get("parsed")), "extracted": row.get("extracted")}
    if "order" in row:
        note["grid_order"] = row["order"]
        note["shuffle_seed"] = row["repeat"]
    correct = int(bool(row["correct"]))
    conn.execute(
        """UPDATE trials SET raw_response=?, response_text=?, latency_ms=?, error=?,
                  response_at=?, predicted_grids=?, correct=?, cell_accuracy=?
           WHERE trial_id=?""",
        (json.dumps(note), row.get("response_tail") or "", row.get("latency_ms"),
         None if row.get("parsed") else UNPARSED, f"{day} 00:00:00",
         json.dumps(answer_of(pz)) if correct else None, correct,
         float(row.get("cell_acc") or 0.0), tid))
    return True


def answer_of(view):
    return {str(k): v for k, v in view["answer_grids"].items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzles", required=True, help="directory of generated puzzle JSON files")
    ap.add_argument("--matrix", required=True, help="matrix200.jsonl (four conditions)")
    ap.add_argument("--order", default=None, help="order200.jsonl (shuffled-order rows are used)")
    ap.add_argument("--matrix-day", default="2026-09-11", help="day the matrix run finished")
    ap.add_argument("--order-day", default="2026-09-15", help="day the order run finished")
    ap.add_argument("--status", default="active", choices=["draft", "active"])
    ap.add_argument("--export-dir", default=str(Path(__file__).parent / "data" / "puzzles"))
    ap.add_argument("--no-classify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    puzzles = {}
    for f in sorted(Path(a.puzzles).glob("*.json")):
        pz = json.loads(f.read_text())
        if not grammar_text(pz):
            raise SystemExit(f"{pz['puzzle_id']}: no grammar text")
        puzzles[pz["puzzle_id"]] = pz
    matrix = [json.loads(l) for l in open(a.matrix)]
    matrix = [r for r in matrix if r["puzzle_id"] in puzzles and r["condition"] in MATRIX_COND]
    last = {}
    if a.order:
        for l in open(a.order):
            r = json.loads(l)
            if r["puzzle_id"] in puzzles and r["condition"] in ORDER_COND:
                last[(r["puzzle_id"], r["model"], r["condition"], r.get("repeat", 1))] = r
    cells = defaultdict(list)
    for (pid, model, cond, _rep), r in last.items():
        cells[(pid, model, cond)].append(r)
    models = sorted({r["model"] for r in matrix})
    print(f"{len(puzzles)} puzzles, {len(matrix)} matrix rows, {len(last)} shuffled rows in "
          f"{len(cells)} cells, models: {models}")
    if a.dry_run:
        return

    conn = db.init_db()
    n_new = sum(import_puzzle(conn, pz, Path(a.export_dir), a.status) for pz in puzzles.values())
    print(f"puzzles written: {n_new} (already present: {len(puzzles) - n_new})")

    ids = {}
    for pid in puzzles:
        ids[pid] = (variant_id(conn, pid, "grammar"), db.get_original_mask_variant_id(conn, pid))
        if None in ids[pid]:
            raise SystemExit(f"{pid}: grammar variant or original mask missing")

    n_matrix = 0
    for r in matrix:
        pz = puzzles[r["puzzle_id"]]
        cond, is_grammar = MATRIX_COND[r["condition"]]
        gid, mask_id = ids[pz["puzzle_id"]]
        text = grammar_text(pz) if is_grammar else pz["narrative"]
        if cond == "grids_only":
            msgs = prompts.build_grids_only(pz)
        elif cond == "narrative_only":
            msgs = prompts.build_narrative_only(pz, narrative=text)
        else:
            msgs = prompts.build_both(pz, narrative=text)
        n_matrix += store_trial(conn, pz, r["model"], cond, msgs, gid if is_grammar else None,
                                mask_id, 1, r, "narc-tiny-stories data/eval/matrix200.jsonl",
                                a.matrix_day, 600)
    n_order = 0
    for (pid, model, cond), rows in sorted(cells.items()):
        pz = puzzles[pid]
        gid, mask_id = ids[pid]
        is_grammar = ORDER_COND[cond]
        text = grammar_text(pz) if is_grammar else pz["narrative"]
        for k, r in enumerate(sorted(rows, key=lambda x: x["repeat"]), start=1):
            view, order = grids.shuffle_view(pz, pz["masked_positions"], r["repeat"])
            if list(order) != list(r["order"]):
                raise SystemExit(f"{pid} {model} {cond} seed {r['repeat']}: grid order "
                                 f"{list(order)} does not match the stored {r['order']}")
            n_order += store_trial(conn, view, model, "both_shuffled",
                                   prompts.build_both(view, narrative=text),
                                   gid if is_grammar else None, mask_id, k, r,
                                   "narc-tiny-stories data/eval/order200.jsonl", a.order_day, 400)
    conn.commit()
    print(f"trials written: {n_matrix} matrix, {n_order} shuffled")
    if n_new:
        db.log_activity(conn, None, "import_narc_tiny", "puzzle", None,
                        f"Imported {n_new} NARC-tiny puzzles (narc-tiny-stories generator "
                        f"v{next(iter(puzzles.values()))['metadata'].get('generator_version')}) "
                        f"with {n_matrix} matrix and {n_order} shuffled-order trials")
    conn.close()

    if not a.no_classify:
        quiet = lambda *_a, **_k: None  # noqa: E731
        for model in models:
            total = narc = 0
            for pid in puzzles:
                res = run_classify_job(model=model, puzzle=pid, log_fn=quiet)
                total += res["total"]
                narc += res["narc"]
            print(f"classified {model}: {total} cells, {narc} NARC")

    conn = sqlite3.connect(db.DB_PATH)
    q = """SELECT CASE WHEN c.variant_id IS NULL THEN 'story' ELSE 'grammar' END AS text,
                  COUNT(*) AS cells, SUM(c.has_narc) AS narc,
                  SUM(c.narc_strength='strong') AS strong, SUM(c.narc_strength='partial') AS partial,
                  SUM(c.narc_strength='weak') AS weak
           FROM classifications c JOIN puzzles p ON p.puzzle_id=c.puzzle_id
           WHERE p.creator=? GROUP BY 1"""
    for row in conn.execute(q, (CREATOR,)):
        print("  %-8s cells %d, NARC %d, strong %s / partial %s / weak %s" % row)
    conn.close()


if __name__ == "__main__":
    main()

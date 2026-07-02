# Weak vs. Strong NARC — Order-Sensitivity Analysis Plan

**Date:** July 1, 2026
**Author:** Bert Baumgaertner (with Claude Code)
**Status:** Implemented & deployed (July 1, 2026). Backfill running on prod.
Reclassify cleaned stale orphans: corpus went 1261 → **1025** real NARC cells.

**Decisions (confirmed July 1, 2026):** K=3 shuffles; three-way verdict
(strong / partial / weak); no nondeterminism control; backfill **all 6 models**, but
sync prod → local first (local may be stale — prod is canonical for the puzzle set).

---

## 1. Motivation

A puzzle cell has the **NARC property** for a model when the masked grid is solved
*only* with narrative + grids (`grids_only=0`, `narrative_only=0`, `both=1`). NARC says
the narrative was *necessary*. It does **not** say the *order* of the grids mattered.

New question — **order sensitivity**:

> For a NARC cell, if the grids had been shown in a random order (same grids, same
> narrative), would the model still reconstruct the masked grid?

- **Weak NARC** — yes, still solved under shuffling. The narrative pins the answer by
  *identity/content*, not by sequence position. Order was incidental.
- **Strong NARC** — no, shuffling breaks it. The narrative disambiguates *because of the
  ordered relationship between grids*. Order is load-bearing.

This is a genuine strengthening of the NARC claim: strong NARC = "narrative necessary
**and** the sequential structure necessary." It sharpens the benchmark and gives a new
axis to sort puzzles by.

---

## 2. Current state (verified July 1, 2026)

### 2.1 How NARC is computed and stored

- `trials` — one row per `(puzzle, variant_id, mask_variant_id, model, condition, repeat_num)`.
  `condition ∈ {grids_only, narrative_only, both}`. Grading: `collect.grade_prediction`
  compares the model's grid to `view["answer_grids"]` via `grids.compare_grids`
  (exact list equality); result stored in `trials.correct` (0/1) + `cell_accuracy`.
- `classify.run_classify_job` groups trials by cell and writes `classifications`
  (`grids_only`, `narrative_only`, `both`, `has_narc`), PK
  `(puzzle_id, variant_id, mask_variant_id, model_name)`.
  `has_narc = int(grids_only==0 and narrative_only==0 and both==1)` (`classify.py:69`).
- **1,259 NARC cells** locally across 6 models / 289 puzzles (gpt-oss-120b 237,
  nemotron-3-super 245, qwen3.5-122b 229, gemma-4-26b 190, gemma-4-31b 180,
  gpt-oss-20b 178). Prod is canonical and at least this large.

### 2.2 Reusable shuffle infrastructure (already in repo)

The prior **ordering experiment** left a clean, deterministic shuffle we can reuse:

- `prompts_ordering._deterministic_shuffle(items, puzzle_id)` — seeded per puzzle
  (`seed = sha256(puzzle_id) % 2**32`), reproducible.
- `grids.remask / complete_sequence / apply_mask` — build a masked *view*
  (`sequence`, `masked_positions`, `answer_grids`) that all downstream code already
  consumes. `grade_prediction(view, ...)` works on any view.
- `prompts.sequence_to_text` labels grids "Grid {position+1}" and marks the masked
  slot `[MASKED]` — so if we renumber positions to display order, a shuffled view
  reads as a clean `Grid 1..N` sequence with `[MASKED]` in the right place.

### 2.3 How new submissions get tested today

`_run_review_job` (server.py:1408) runs `run_collect_job` → `run_classify_job` per
`(puzzle, model)`, queued from the **AI Review** admin tab
(`POST /api/admin/puzzles/<id>/run-review`). This is the hook point for automatic
sensitivity testing.

### 2.4 Inspect → Masking tab

Flask route `/inspect?tab=masking` → `_inspect_masking` (server.py:206) →
`templates/inspect.html`. Per-puzzle header **dots** (`N`/`G`/`L`/`×`) plus a per-model
results **table** (Grids Only / Narrative Only / Both / NARC). Static export mirror in
`inspector.py`; JSON export at `/api/inspect/export/masking.json`. NARC styled via
`.dot-narc` (green `#2ECC40` on `#134e1f`).

---

## 3. Experiment design

### 3.1 What we shuffle, and what we hold fixed

Only the **`both`** condition is meaningful — that is the condition whose success
*defines* the NARC property. `grids_only`/`narrative_only` don't need a shuffle variant
(narrative_only has no grids; grids_only failure is already recorded and isn't what NARC
hinges on).

For each **NARC cell**, run a new condition **`both_shuffled`**:

- Same grids, same narrative text (verbatim), **grids in a randomized display order**.
- The masked grid moves with the shuffle; the `[MASKED]` slot follows it. The model
  still reconstructs that grid's true content; grading is unchanged
  (`grade_prediction` against the shuffled view's `answer_grids`).
- **K distinct, non-identity permutations** per cell (default **K=3**), each
  deterministically seeded from `sha256(f"{puzzle_id}|{variant_id}|{mask_variant_id}|{k}")`.
  Identity permutations are skipped (they'd just re-run canonical `both`). Stored as
  `condition="both_shuffled"`, `repeat_num = 1..K`.

Why K>1: a single shuffle can get lucky (a permutation that preserves the locally
relevant adjacency) or unlucky. K shuffles give a solve *rate* and let us separate
"robustly order-dependent" from "flaky."

### 3.2 Classification rule

Per NARC cell, let `s = #shuffles solved`, `K = #shuffles run`:

| Verdict      | Rule (default)      | Meaning                                   |
|--------------|---------------------|-------------------------------------------|
| **strong**   | `s == 0`            | fails every shuffle → order necessary     |
| **weak**     | `s == K`            | solves every shuffle → order incidental   |
| **partial**  | `0 < s < K`         | order-sensitive but not absolute          |
| *(untested)* | `K == 0`            | no shuffle trials yet → strength `NULL`    |

We **store the raw `s` and `K`** (not just the label) so the threshold can be retuned
without re-running the API. Default UI is three-way (strong / partial / weak) with
`s/K` in the tooltip; collapsing `partial` into the nearest of strong/weak (threshold
0.5) is a one-line display change if you prefer strict binary.

Non-NARC cells get strength `NULL` — the distinction only applies where NARC holds.

### 3.3 Nondeterminism control (optional refinement)

Models are stochastic, so a "strong" verdict could be a flaky model rather than true
order-dependence. Optional: alongside the K shuffles, re-run **one canonical `both`**
(identity order) as a control. If the control *also* fails, the cell is unstable and we
tag it `unstable` rather than trusting `strong`. Off by default (keeps cost/complexity
down); easy to enable via config.

---

## 4. Data model changes

**No new table; no rebuild.** Two nullable columns added to `classifications`
(`ALTER TABLE ADD COLUMN`, guarded by a `_has_column` check in `db._apply_migrations`,
so it auto-applies on app restart and is idempotent):

```sql
ALTER TABLE classifications ADD COLUMN narc_strength  TEXT;     -- 'strong'|'weak'|'partial'|NULL
ALTER TABLE classifications ADD COLUMN shuffle_solved INTEGER;  -- s
ALTER TABLE classifications ADD COLUMN shuffle_total  INTEGER;  -- K
```

Shuffle attempts live in the existing `trials` table (`condition="both_shuffled"`),
so they inherit the AI-Review UI, skip-completed/resume logic, and the two-pass
protocol for free. The permutation is fully derivable from the seed, so nothing extra
is persisted per shuffle.

---

## 5. Code changes

1. **`grids.py`** — add `shuffle_view(puzzle_json, masked_positions, seed)`:
   `complete_sequence` → permute items by seeded RNG → **renumber positions to display
   order** → track where masked grid(s) landed → `apply_mask`. Returns a view; skips
   identity permutations (caller advances the seed). Reuses the ordering-experiment
   seeding style.

2. **`collect.py`** — add `run_sensitivity_job(model, puzzle=None, shuffles=3,
   concurrency=8, dry_run=False, log_fn=print)`:
   - Read NARC cells from `classifications` (`has_narc=1`, optional puzzle filter).
   - For each cell × k: build `shuffle_view`, `prompts.build_both(view, narrative=cell_narrative)`,
     `insert_trial(condition="both_shuffled", variant_id, mask_variant_id, repeat_num=k)`,
     run two-pass, grade against the view, `update_trial_evaluation`.
   - **Skip-completed** (resumable) and **dry-run is non-mutating** (enumerate `planned`
     before any insert) — same discipline as `run_matrix_job`.

3. **`collect_sensitivity.py`** (new) — thin Click CLI mirroring `collect_matrix.py`:
   `--model --puzzle --shuffles --concurrency --dry-run --all-models`.

4. **`classify.py`** — extend `run_classify_job` so it is the **sole writer** of
   strength: after computing `has_narc`, if NARC, read that cell's `both_shuffled`
   trials → `s`, `K` → derive `narc_strength`; write via extended
   `db.upsert_classification`. Non-NARC → strength `NULL`. (Idempotent; re-running
   classify recomputes strength from whatever shuffle trials exist.)

5. **`db.py`** — `upsert_classification` gains `narc_strength/shuffle_solved/shuffle_total`;
   add the migration in `_apply_migrations`.

6. **`config.yaml`** — under `experiment:` add `shuffles: 3` (and optional
   `sensitivity_control: false`).

---

## 6. Inspect → Masking tab UI

- **`_inspect_masking`** (server.py) — add `narc_strength, shuffle_solved, shuffle_total`
  to the SELECT and per-model result dict.
- **`templates/inspect.html`**:
  - Header dot: for a NARC model, style by strength — **filled** green `N` = strong,
    **hollow/outlined** `N` = weak, half-shade for partial. `title` tooltip: e.g.
    "gpt-oss-120b: strong NARC (0/3 shuffles solved)".
  - Results table: repurpose the NARC cell (or add an **Order** column) to show
    `Strong / Weak / Partial (s/K)` badges. New CSS: `.dot-narc-weak`,
    `.dot-narc-partial`.
  - Update the legend/intro card to define weak vs. strong NARC.
- **`inspector.py`** — mirror the badge logic in the static generator.
- **`/api/inspect/export/masking.json`** — carries the new fields automatically.
- Optional: corpus summary counters (how many cells are strong / weak / partial per
  model) and a sort/filter by strength.

---

## 7. Automatic testing of new submissions

Extend `_run_review_job` (server.py) to a 4-step chain per `(puzzle, model)`:

```
run_collect_job          # canonical grids_only / narrative_only / both
run_classify_job         # marks has_narc
run_sensitivity_job      # K shuffled 'both' trials for NARC cells only
run_classify_job         # recompute strength from shuffle trials (no API calls)
```

`run_sensitivity_job` only touches cells that came back NARC, so cost scales with how
NARC-rich the puzzle is (often zero). Same log file, same job-status plumbing — no
schema change to `review_jobs`. The AI-Review tab shows strength as soon as the job
finishes.

---

## 8. Backfilling the deployed corpus

Per project convention (prod is canonical; snapshot before schema/data migration):

1. **Snapshot prod DB.**
2. **Deploy code** (migration auto-applies on restart via `_apply_migrations`).
3. **Run backfill on prod** (against the prod DB, resumable):
   ```
   python collect_sensitivity.py --all-models        # loops models, NARC cells only
   python classify.py --model <each>                 # fold strength into classifications
   ```
   Size: ~1,259 NARC cells × K=3 × 2-pass ≈ **3.8k trials / ~7.5k API calls** total
   across 6 models (prod ≥ this). Skip-completed makes it interruptible/resumable.
   Honor MindRouter limits ([[feedback_concurrency]] / [[feedback_503_retries]]):
   concurrency 8 for gpt-oss-120b, drop to 1 on 503 storms.
4. **Verify** counts + spot-check a few strong/weak verdicts against raw responses.
5. **Sync** so local matches (prod → local per [[feedback_db_sync_direction]]).

Backfill can alternatively be triggered per-puzzle from the **AI Review** tab once the
code is deployed (each re-review now includes the sensitivity step).

---

## 9. Open decisions (please confirm)

1. **K (shuffles per cell):** default **3**. Higher K = more confidence, linear cost.
2. **Verdict granularity:** three-way (**strong / partial / weak**, recommended) vs.
   strict binary at a 0.5 threshold.
3. **Nondeterminism control (§3.3):** off by default — enable to guard against flaky
   "strong" verdicts?
4. **Backfill scope:** all 6 models, or start with gpt-oss-120b to validate the pipeline
   before spending the full budget?

---

## 10. Suggested build order

1. Schema migration + `db.upsert_classification` extension.
2. `grids.shuffle_view` + a unit check (permutation ≠ identity, masked grid tracked,
   `answer_grids` correct).
3. `run_sensitivity_job` + `collect_sensitivity.py` (validate with `--dry-run`, then one
   puzzle on gpt-oss-120b).
4. `classify.py` strength pass.
5. Inspect UI (route + template + inspector.py).
6. Wire into `_run_review_job`.
7. Deploy → snapshot → backfill → verify → sync.

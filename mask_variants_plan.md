# Mask Variants — Design & Implementation Plan

**Date:** July 1, 2026 (addendum July 16, 2026)
**Author:** Bert Baumgaertner (with Claude Code)
**Status:** Shipped — all four steps live; pipeline integration + full backfill completed July 16, 2026 (see §11)

---

## 1. Motivation

NARC currently supports **narrative variants**: multiple story clues over the *same*
sequence of grids with the *same* masked position(s). The `narrative_variants` table
holds them, and `trials` / `classifications` reference them by `variant_id`, so they
are first-class in the experimental pipeline.

There is no equivalent for **masking a different grid**. To test "same grids, but mask
grid 4 instead of grid 2" today you must author an entirely new puzzle row. That
duplicates `sequence_json` across rows (grid-drift risk), provides no explicit link
between the related puzzles, and cannot be flipped from a single puzzle's page.

**Goal:** introduce *mask variants* as a first-class axis, orthogonal in storage to
narrative variants, plus a creator-controlled **test matrix** that selects which
(narrative variant × mask variant) combinations are actually run. Not every cell of
the cross product is meaningful — for some puzzles one narrative disambiguates any
single mask; for others the clue only fits one specific mask — so the creator picks.

---

## 2. Current state (verified July 1, 2026)

### 2.1 Where the data lives

A `puzzles` row bundles three things:

| Concern   | Column(s)                              |
|-----------|----------------------------------------|
| Grids     | `sequence_json`                        |
| Mask      | `masked_positions` + `answer_grids`    |
| Narrative | `narrative`                            |

- `narrative_variants (variant_id, puzzle_id, variant, narrative, ...)` — swaps *only*
  the narrative. Grids + mask stay pinned. Keyed `UNIQUE(puzzle_id, variant)`.
- `trials` and `classifications` FK to `variant_id` (nullable) → narrative variants are
  experimentally first-class.
- Separately, `puzzles.parent_puzzle_id` and `puzzles.stance_group` already model
  *sibling puzzle rows* that share grids but differ otherwise. So the codebase already
  contains two philosophies: **sub-rows** (narrative variants) vs **linked siblings**
  (grid / stance variants). Mask variants adopt the sub-row philosophy.

### 2.2 Critical data fact — the split truth

Audit of all 201 masked slots in `narc.db`:

| Situation                                             | Count |
|------------------------------------------------------|-------|
| `sequence_json` has a **hole** at the masked pos     | 171   |
| `sequence_json` holds the true grid at the masked pos| 30    |
| Mismatch (sequence grid ≠ answer_grids)              | 0     |

So for most puzzles the complete story is **split**: unmasked grids live in
`sequence_json`, the masked grid lives *only* in `answer_grids`. The union is the full
truth (never contradictory), but because the truth is split you cannot simply flip
which position is masked — the newly-masked position's grid is present, but the
previously-masked position's grid is not in `sequence_json`.

This split is also the root cause of the recent `IndexError` / out-of-range-mask fixes
(`validate_puzzle_geometry`, commits `cd2ed30`, `8009ca2`): masked positions and the
sequence can fall out of sync.

### 2.3 Key touch points

- `grids.py:sequence_to_text` — renders `[MASKED]` from `masked_positions` alone; reads
  `item["grid"]` for every non-masked position. **Already mask-agnostic** — works for
  any mask the moment holes are filled.
- `prompts.py` — builders read `puzzle["masked_positions"]` and `puzzle["answer_grids"]`
  (lines 8–12, 26, 45, 65, 85, 106, 141–151).
- `server.py:normalize_puzzle_input` (108) and `validate_puzzle_geometry` (125) — accept
  old/new format, guard mask ⟷ sequence consistency.
- `collect.py` — iterates narrative variants; extraction maps predictions to
  `answer_grids` / `masked_positions` (lines 63–69, 115, 173–178).
- `db.py` — `upsert_variant`, `get_variants`, `insert_trial`, `upsert_classification`.

---

## 3. Target model

Two orthogonal variant axes under one puzzle, plus an explicit selector for which
combinations to test:

```
puzzle (complete sequence, no holes)
 ├── narrative_variants   (N rows)   ← existing
 ├── mask_variants        (M rows)   ← NEW
 └── variant_pairs        (≤ N×M rows, creator-enabled)  ← NEW
        └── trials / classifications key on (variant_id, mask_variant_id, ...)
```

- **Narrative variant** = which story clue. Changes `narrative`.
- **Mask variant** = which position(s) are hidden. Changes `masked_positions` only
  (answer derived).
- **Variant pair** = a testable cell. Creator toggles `enabled`. `collect` runs only
  enabled pairs.

---

## 4. Schema changes

### 4.1 `mask_variants` (new)

```sql
CREATE TABLE IF NOT EXISTS mask_variants (
    mask_variant_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id        TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    label            TEXT NOT NULL DEFAULT 'original',  -- 'mask-2', 'mask-4', 'mask-2+3'
    masked_positions TEXT NOT NULL,                     -- JSON array of ints, e.g. [3]
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(puzzle_id, label)
);
```

**No `answer_grids` column** — derived from the complete sequence (§5).

### 4.2 `variant_pairs` (new) — the test matrix

```sql
CREATE TABLE IF NOT EXISTS variant_pairs (
    pair_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id        TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    variant_id       INTEGER NOT NULL REFERENCES narrative_variants(variant_id),
    mask_variant_id  INTEGER NOT NULL REFERENCES mask_variants(mask_variant_id),
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(variant_id, mask_variant_id)
);
```

Semantics: a row exists (enabled=1) iff the creator wants that narrative tested against
that mask. "One narrative fits every mask" → enable that narrative's whole row. "Clue
only fits mask-2" → enable one cell.

### 4.3 `trials` — add `mask_variant_id`

```
trials.mask_variant_id  INTEGER REFERENCES mask_variants(mask_variant_id)
new UNIQUE(puzzle_id, variant_id, mask_variant_id, model_name, condition, repeat_num)
```

- `grids_only` condition: `variant_id` NULL (narrative irrelevant), `mask_variant_id`
  set (mask always matters).
- `narrative_only` / `both`: both set.
- Backfill: every existing trial → the puzzle's `original` mask variant.

### 4.4 `classifications` — add `mask_variant_id`

```
new PRIMARY KEY(puzzle_id, variant_id, mask_variant_id, model_name)
```

NARC-property is now computed per mask. A puzzle can be NARC for mask-2 but not mask-4 —
strictly richer than today.

> **SQLite gotcha (already learned):** UNIQUE / PK treats NULLs as distinct, so
> `INSERT OR REPLACE` will not dedupe rows where `variant_id` or `mask_variant_id` is
> NULL. Keep the existing DELETE-then-INSERT pattern in `upsert_classification` and
> extend it to include `mask_variant_id`.

---

## 5. Complete-sequence storage (the enabling move)

Migrate `puzzles.sequence_json` so **every position holds its true grid** — fill the 171
holes from `answer_grids`:

```python
full[p].grid = answer_grids[str(p)]   if p was masked
full[p].grid = sequence[p].grid       otherwise
```

Consequences:

- **`answer_grids` becomes derived, never stored:**
  `answer_grids_for(mask_positions) = { str(p): full_sequence[p].grid for p in mask_positions }`.
- A mask variant is *only* a position list — masks can be swapped freely with no
  per-mask answer storage.
- The whole out-of-range / `IndexError` bug class disappears: every position always has
  a grid, so a mask never dangles.
- `sequence_to_text` needs **zero change** (it already reads `item["grid"]` for
  non-masked positions and honors `masked_positions` for the mask).

Helpers to add (probably `grids.py`):

```python
def complete_sequence(puzzle_row) -> list          # holes filled; single source of truth
def answer_grids_for(complete_seq, masked_positions) -> dict
```

`puzzle_to_json` and the `prompts.py` builders switch to `answer_grids_for(...)` instead
of reading the stored column.

**Back-compat:** the puzzle JSON file format (`data/puzzles/*.json`) can keep holes +
`answer_grids` on import; the importer fills holes before insert. The stored
`answer_grids` column may be retained (ignored) through the transition, then dropped in a
later cleanup once nothing reads it.

---

## 6. UI

On the puzzle admin / solve-admin page:

1. **Mask-variant editor.** From the grid strip, select which position(s) to mask, name
   the variant (`mask-4`), save → new `mask_variants` row. Mirrors the "add narrative
   variant" affordance.
2. **Test-matrix panel.** A checkbox grid — narrative variants as rows, mask variants as
   columns. Each checkbox = a `variant_pairs.enabled` toggle. Row/column "select all"
   for the common "one narrative, every mask" and "one mask, every narrative" cases.
3. **Solve UI.** The existing narrative selector gains a sibling mask selector; picking a
   mask re-renders which grid shows `[MASKED]` (answer derived on the fly). Labels stay
   generic per existing rules.

---

## 7. Pipeline changes

- **`collect.py`:** iterate `variant_pairs WHERE enabled=1` instead of narrative variants
  alone. For each pair, build prompts with the mask variant's `masked_positions` and the
  derived `answer_grids`. Record `mask_variant_id` on every trial (including `grids_only`,
  where `variant_id` is NULL but `mask_variant_id` is set). Skip-completed logic extends
  its key to include `mask_variant_id`.
- **`classify.py`:** compute NARC-property per `(narrative_variant, mask_variant, model)`;
  write `mask_variant_id` into `classifications`.
- **`analyze.py` / `inspector.py`:** group and report by the (narrative, mask) cell;
  surface per-mask NARC counts.
- **`prompts.py`:** builder signatures take `masked_positions` + a complete sequence (or
  the puzzle + a mask variant) rather than reading `puzzle["answer_grids"]` directly.

---

## 8. Migration order (each step shippable alone)

1. **Complete-sequence backfill.** Fill holes in `sequence_json`; route
   `answer_grids` through `answer_grids_for(...)`. **No behavior change, no schema
   change, removes the IndexError bug class.** Do this first regardless of the rest.
2. **`mask_variants` table + seed.** One `original` row per puzzle from current
   `masked_positions`. Read-only wiring; nothing consumes it yet.
3. **`mask_variant_id` on `trials` + `classifications`.** Backfill existing rows to the
   `original` mask variant. Update `collect` / `classify` to write it. Real pipeline
   touch; keep old rows valid.
4. **`variant_pairs` + test-matrix UI.** The payoff: creator selects combinations;
   `collect` iterates enabled pairs.

Steps 1–2 are safe and independently useful. Step 3 is the substantive pipeline change.
Step 4 delivers the feature.

**DB deploy note:** schema/data changes must run on **both** local and production DBs
(prod is canonical; snapshot prod DB before migrating live data — see deploy workflow).
Code deploys auto via git.

---

## 9. Edge cases & open questions

- **Multi-mask.** `masked_positions` is already a JSON array; a mask variant naturally
  supports `[2, 3]`. Labels should encode it (`mask-2+3`). No special-casing needed.
- **Grid geometry per mask.** Answer dimensions are the true grid's dimensions at each
  masked position — always available from the complete sequence, so
  `validate_puzzle_geometry` simplifies (range check only; the "answer_grids missing"
  branch goes away).
- **`solve_attempts`.** Currently keyed on `puzzle_id` + `active_variant` (narrative
  label). To record which mask a human solved, add an `active_mask_variant` column
  (analogous to `active_variant`). Lower priority than the model pipeline.
- **Old JSON import path.** Decide whether `data/puzzles/*.json` gains an optional
  `mask_variants` array, or whether mask variants are DB-only (authored via UI). Suggest:
  DB-only initially; add to the JSON schema only if a generator needs to emit them.
- **Deriving vs. storing answer_grids.** Recommend fully deriving (drop the column after
  transition). If any external tooling reads `answer_grids` directly, keep the column as
  a generated mirror until those readers are migrated.
- **Degenerate cells.** `variant_pairs` prevents running meaningless combinations, but
  nothing *validates* that an enabled narrative actually disambiguates its mask — that
  remains the creator's judgment (or a future NARC-property check).

---

## 10. Effort estimate

| Step | Scope                                        | Rough effort |
|------|----------------------------------------------|--------------|
| 1    | Backfill + derive answer_grids               | ~half day    |
| 2    | `mask_variants` table + seed + helpers       | ~half day    |
| 3    | `mask_variant_id` in trials/classifications, collect/classify wiring | ~1–1.5 days |
| 4    | `variant_pairs` + test-matrix UI + solve UI  | ~1–2 days    |

Total ≈ 3–4 focused days, front-loaded with the two safe/independent steps.

---

## 11. Completion addendum — July 16, 2026

Steps 1–4 shipped July 1–2. On July 16 the feature became fully automatic and fully
backfilled:

**Pipeline integration.** AI Review jobs (`server.py:_run_review_job`) now run
collect → **matrix** → classify → sensitivity → re-classify, so every enabled
(narrative × mask) cell is tested the moment a puzzle is reviewed — no manual
`collect_matrix.py` pass needed. `run_matrix_job` skips the (original × original) cell
by default: that cell is exactly the base 3-condition run stored under `variant_id NULL`,
so re-running it duplicated API calls and produced contradictory duplicate rows
(`collect_matrix.py --include-original` forces it).

**NULL-dedupe fix (the §4.3 gotcha struck again).** The `trials` UNIQUE constraint
includes nullable `variant_id`/`mask_variant_id`, so SQLite never fired it for
base-protocol rows — `INSERT OR IGNORE` re-inserted (and re-ran) every base trial on
each full collect pass. `db.insert_trial` now does a NULL-safe lookup-before-insert,
preferring an answered row so legacy duplicates don't shadow completed work. 213
duplicate-shadowed pending rows were purged from prod + local.

**Inspect drill-down.** The Masking tab shows the original-cell results table plus an
expandable "Variant results (N narrative × mask cells)" table — rows = cells, columns =
models, N/G/L/× badges with weak/strong tooltips. Header dots remain the
best-across-cells roll-up (drives filters and summary counts).

**Full-corpus backfill (completed July 16, ~6.5 h, prod container).** Per model:
collect → collect_matrix → collect_sensitivity. Final corpus: 587 puzzles, 26,858
trials, 7,416 classification cells, ~0.5% parse errors. Per-model cells / NARC / strong:

| Model | Cells | NARC | Strong |
|-------|-------|------|--------|
| gpt-oss-120b | 983 | 266 | 93 |
| gpt-oss-20b | 987 | 162 | 85 |
| qwen3.5-122b | 992 | 229 | 128 |
| nemotron-3-super | 992 | 265 | 109 |
| gemma-4-26b | 993 | 197 | 96 |
| gemma-4-31b | 993 | 199 | 117 |
| qwen3.6-27b | 1476 | 214 | 117 |

Every NARC cell (variant cells included) is order-sensitivity tested — zero untested.
Reviewer accounts were audited for variant/mask/matrix access: full parity with owners
already existed (all routes and UI panels gate on `('owner', 'reviewer')`); no change
was needed.

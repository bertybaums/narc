# NARC — Narrative Augmented Reasoning Challenges

## What This Project Is

NARC is a puzzle benchmark where sequences of 3-8 colored grids tell an abstract "story." One or more grids are masked, and the solver must reconstruct them pixel-perfectly. A short narrative clue accompanies each puzzle — without it the puzzle is ambiguous, but with it the answer is uniquely determined (the "NARC-property").

Sibling project to MARC2 (`/Users/bbaum/Documents/claude-projects/marc2`). Shares the ARC 10-color palette (0-9), MindRouter infrastructure, and two-pass testing protocol.

## Architecture

- **Backend**: Flask (`server.py`), port 8000
- **Database**: SQLite with WAL (`narc.db`), schema in `schema.sql`
- **Frontend**: Vanilla JS + Bootstrap 5, no build step
- **Grid rendering**: Python-side via `grids.py` (PIL), browser-side via `static/js/grids.js`
- **Pipeline**: `collect.py` → `classify.py` → `analyze.py` (+ `generate_alternatives.py`, `generate_gap_puzzles.py`)
- **Config**: `config.yaml` for MindRouter model configs

## Key Files

- `server.py` — Flask app, routes, API
- `db.py` — SQLite helpers (puzzles, variants, trials, classifications, solve_attempts)
- `grids.py` — Grid ↔ text, PNG rendering, response parsing (`parse_response_grids`)
- `prompts.py` — 3-condition prompt builders (grids_only, narrative_only, both), supports multi-mask
- `models.py` — MindRouter LLM client, two-pass protocol
- `collect.py` — Run 3-condition testing on subject models
- `classify.py` — Compute NARC-property per (puzzle, variant, model)
- `analyze.py` — Generate HTML analysis reports
- `generate_alternatives.py` — Generate domain-diverse narrative variants via Claude subagents
- `generate_gap_puzzles.py` — Three strategies: visual-first, narrative-first, remix

## Puzzle Format

JSON files in `data/puzzles/`. Key fields:
- `masked_positions`: JSON array (supports multi-mask, e.g. `[2, 3]`)
- `answer_grids`: JSON dict keyed by position string (e.g. `{"2": [[...]], "3": [[...]]}`)
- `metadata.human_difficulty` / `metadata.ai_difficulty`: 1-5 predicted ratings
- `metadata.tags`: Array of namespaced tags (see Taxonomy below)

Old-format puzzles (`masked_position` singular, `answer_grid` singular) are auto-converted by `server.py:normalize_puzzle_input()`.

## Puzzle Taxonomy (Tags)

| Dimension | Prefix | Values |
|-----------|--------|--------|
| Audience | `audience:` | general, humanities, science, ai-native |
| Narrative Arc | `arc:` | linear, rise-and-fall, cyclical, subversion, transformation, convergence, divergence |
| Clue Type | `clue:` | direction, identity, exception, rule, timing, causation |
| Domain | `domain:` | literature, mythology, physics, biology, ecology, philosophy, ethics, social-science, mathematics, computer-science, ml, music, psychology, earth-science, astronomy, information-theory |
| Spectrum | `spectrum:` | ai-forte, human-forte, ai-edge, human-edge, balanced, domain-dependent |
| Grid count | `grids:` | 3-8 |
| Grid size | `size:` | uniform, varying |
| Mask | `mask:` | single, multi |
| Strategy | `strategy:` | ai-native, visual-first, narrative-first, remix |

## Corpus Stats (as of March 31, 2026)

- 240 puzzles, 60 unique grid sizes, 400+ narrative variants
- Puzzle categories: 50 regular, 15 AI-native, 15 gap-maximizing, 100 spectrum, 60 focalization
- Spectrum: 52 ai-forte, 41 human-forte, 41 balanced, 20 domain-dependent
- Dual difficulty: human_difficulty (1-5) + ai_difficulty (1-5) on all puzzles
- Taxonomy: audience, arc, clue-type, domain, spectrum, grids, size, mask tags

## Cross-Model Results (March 31, 2026)

Tested on 4 MindRouter models (qwen3.5-400b excluded — unreliable):

| Model | NARC | Grids Suff | Unsolvable | Narrative Lift |
|-------|------|-----------|------------|---------------|
| qwen3.5-122b | 42 (17.5%) | 56 (23.3%) | 128 (53.3%) | +12.5pp |
| nemotron-3-super | 35 (14.6%) | 54 (22.5%) | 143 (59.6%) | +12.1pp |
| gpt-oss-120b | 25 (13.8%) | 46 (25.4%) | 101 (55.8%) | +7.5pp |
| gpt-oss-20b | 22 (9.2%) | 42 (17.5%) | 168 (70.0%) | +3.7pp |

10 puzzles are NARC on all 4 models (robust). 172 puzzles are NARC on 0 models.

## Focalization Experiment

Tested whether narrative perspective (active/observer/absent actor) affects solvability.
180 trials on gpt-oss-120b. Mixed results — the hypothesis didn't hold cleanly.
Results in focal_results_gpt-oss-120b.json and focal_analysis_gpt-oss-120b.html.

## Generated Reports

- `inspect.html` — interactive inspector with all 4 models (regenerate: `python inspector.py`)
- `analysis_*.html` — per-model reports (regenerate: `python analyze.py --model NAME`)
- `focal_analysis_*.html` — focalization report, 17 human-edge, 9 ai-edge

## Solving UI

Two-phase flow: solver can attempt without narrative first (optional), then reveal clue. Both attempts recorded. Labels hidden until clue is revealed.

## Running

```bash
python server.py                              # http://localhost:8000
python collect.py --model gpt-oss-120b        # 3-condition testing
python classify.py --model gpt-oss-120b       # NARC-property classification
python analyze.py --model gpt-oss-120b        # HTML report
python generate_alternatives.py --puzzle narc_001 --domains 5
python generate_gap_puzzles.py --strategy visual-first --count 5
```

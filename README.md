# NARC: Narrative Augmented Reasoning Challenges

Live site: **https://narc.insight.uidaho.edu**

A NARC puzzle is a sequence of 3 to 8 small colour grids that tell an abstract story. One or
more grids are hidden and the solver must reconstruct them exactly. A short narrative clue
accompanies each puzzle. The clue never mentions grids; it helps only when read figuratively.
A puzzle has the **NARC property** for a given model when the model solves it with grids and
clue together but not with either alone. The property is measured per (puzzle, narrative
variant, mask variant, model) cell, with order-shuffle and keyword-ablation follow-ups.

Bert Baumgaertner, University of Idaho. A paper on the masking battery is under review
(September 2026); citation and data release will be added here when they exist.

## What is where

| Path | What |
|---|---|
| `server.py` | Flask app: About, Browse, Solve, Create, Inspect, Narrate, admin, JSON API |
| `db.py`, `schema.sql` | SQLite (WAL) helpers and schema; the database itself is not in the repo |
| `grids.py`, `prompts.py`, `models.py` | grid text/PNG rendering, three-condition prompts, MindRouter client |
| `collect.py`, `collect_matrix.py`, `collect_sensitivity.py`, `collect_narrative_sensitivity.py` | trial collection: base conditions, variant matrix, order shuffles, keyword ablation |
| `classify.py` | NARC / strength / dependence verdicts per cell |
| `narrate.py`, `narrate_db.py` | the invited Narrate study (separate database, gitignored) |
| `templates/`, `static/` | Bootstrap 5 + vanilla JS front end |
| `data/puzzles/` | puzzle JSON exports |
| `docs/` | redirect for the old GitHub Pages export; the site lives at the URL above |
| `*_report.md`, `story_prism_results.md`, `four_narrative_strategies_preliminary.md` | spring 2026 pilot reports (ordering, Story Prism, ABT); small studies, not the paper's claims |
| `James-Story_Prism_Audit.pdf` | Erin James's Story Prism worksheet, included with permission |

## How much to trust what

- **Documented:** the Masking tab on Inspect (three conditions, ablations, all current models,
  every active puzzle). Single-sample per cell; on a re-run of a 34-puzzle core about one NARC
  verdict in six flips.
- **Pilot:** Ordering, Odd-One-Out, Stances tabs and the narrative-variant reports above.
- **Exploratory:** anonymous human solve attempts on the site. Counts only; no human study.
- **Retired:** predicted `human_difficulty` / `ai_difficulty` and `spectrum:` tags on
  AI-generated puzzles. They remain in the data as provenance but are not shown or used.

## Running locally

```bash
pip install -r requirements.txt
python server.py                      # http://localhost:8000 (needs a narc.db; see schema.sql)
python collect.py --model gpt-oss-120b --puzzle narc_001
python classify.py --model gpt-oss-120b
```

Model access goes through the University of Idaho MindRouter endpoint (`config.yaml`);
set `MINDROUTER_API_KEY`. Deployment: `docker compose up -d --build` (see `docker-compose.yml`).

## Provenance of puzzles

Every puzzle card is labelled human-written, AI-generated (a language model working from a
strategy prompt or as an autonomous agent), or AI + human (an AI draft revised by a person).
Every active puzzle was approved by a reviewer and then run through the model battery.

## License

Not yet chosen. A data licence will be set with the release; please ask before reusing
puzzles or results.

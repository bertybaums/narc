#!/bin/bash
# Run one model over an ORDERED list of puzzles, puzzle by puzzle, full battery each:
# base 3 conditions -> narrative x mask matrix -> classify -> order sensitivity -> keyword
# ablation. The Inspect page fills in puzzle by puzzle in the list's order, so the puzzles
# you care about most show first. Afterwards run the normal lane (backfill_model.sh /
# run_backfills_lanes.sh) for everything else; standing rows are skipped, so nothing is redone.
#
#   ./run_priority.sh MODEL LIST.json [CONCURRENCY]     (LIST.json = ["puzzle_id", ...])
#
# Built October 4, 2026 for kimi-k3 (Bert: team picks first, then puzzles Fable solved, then
# puzzles that separate stronger from weaker models). The list for that run was written by a
# one-off query to data/kimi_priority.json: 🧩 team picks (24) -> Fable solved/NARC (57) ->
# NARC on >= 6 of the 8 original models (59) -> NARC on >= 2 of the top 3 and none of the
# bottom 3 (38) -> unsolved by every model at the original cell (9); 134 puzzles after dedupe.
#
# On prod, detached:  docker exec -d narc-narc-1 ./run_priority.sh kimi-k3 data/kimi_priority.json 3
# Log: data/backfill_logs/MODEL-priority.log
set -u
MODEL="${1:?usage: run_priority.sh MODEL LIST.json [CONCURRENCY]}"
LIST="${2:?usage: run_priority.sh MODEL LIST.json [CONCURRENCY]}"
CONC="${3:-3}"
PY="${PYTHON:-python}"
LOGDIR="data/backfill_logs"
LOG="$LOGDIR/$MODEL-priority.log"
mkdir -p "$LOGDIR"
PUZZLES=$("$PY" -c "import json,sys; print(' '.join(json.load(open(sys.argv[1]))))" "$LIST")
N=$(echo "$PUZZLES" | wc -w | tr -d ' ')
echo "$MODEL PRIORITY START $N puzzles concurrency=$CONC $(date -u)" >> "$LOGDIR/_status.log"
i=0
for p in $PUZZLES; do
    i=$((i+1))
    echo "===== $MODEL priority $i/$N $p ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) =====" >> "$LOG"
    "$PY" collect.py --model "$MODEL" --puzzle "$p" --concurrency "$CONC" >> "$LOG" 2>&1
    "$PY" collect_matrix.py --model "$MODEL" --puzzle "$p" --concurrency "$CONC" >> "$LOG" 2>&1
    "$PY" classify.py --model "$MODEL" --puzzle "$p" >> "$LOG" 2>&1
    "$PY" collect_sensitivity.py --model "$MODEL" --puzzle "$p" --concurrency "$CONC" >> "$LOG" 2>&1
    "$PY" collect_narrative_sensitivity.py --model "$MODEL" --puzzle "$p" --concurrency "$CONC" >> "$LOG" 2>&1
    "$PY" classify.py --model "$MODEL" --puzzle "$p" >> "$LOG" 2>&1
done
echo "$MODEL PRIORITY DONE $(date -u)" >> "$LOGDIR/_status.log"

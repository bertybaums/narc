#!/bin/bash
# Full retroactive test pipeline for ONE subject model over the whole corpus.
#
#   ./backfill_model.sh MODEL [CONCURRENCY]      (default concurrency 4)
#
# Mirrors what an AI Review job does per puzzle (server._run_review_job), but
# corpus-wide and sequential: collect -> matrix -> classify -> order-sensitivity
# -> narrative-sensitivity, then one retry pass at concurrency 1 for trials that
# failed at the HTTP layer, then a final classify. Every step is resumable —
# the collect scripts only run trials that have no answer yet — so killing and
# relaunching this script just picks up where it left off.
#
# Rate limits: all requests go through the shared SQLite token bucket
# (ratelimit.py, 95 req/min across every process using this narc.db), so the
# backfill and live review jobs can't jointly exceed MindRouter's ceiling.
#
# Run on prod from inside the container so it uses the live DB + key:
#   ssh devops@bbaum.insight.uidaho.edu \
#     'cd ~/narc && docker exec -d narc-narc-1 ./backfill_model.sh qwen3.8-27b 4'
#   tail -f ~/narc/data/backfill_logs/qwen3.8-27b.log
# NOTE: a redeploy (docker compose up --build) restarts the container and kills
# a running backfill. Relaunch afterwards; nothing is lost.
set -u
MODEL="${1:?usage: backfill_model.sh MODEL [CONCURRENCY]}"
CONC="${2:-4}"
PY="${PYTHON:-python}"
LOGDIR="data/backfill_logs"
LOG="$LOGDIR/$MODEL.log"
mkdir -p "$LOGDIR"

phase() {  # phase NAME CMD...
    local name="$1"; shift
    echo "===== $MODEL $name ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) =====" >> "$LOG"
    "$@" >> "$LOG" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "===== $MODEL $name FAILED (exit $rc) =====" >> "$LOG"
        echo "$MODEL FAILED at $name $(date -u)" >> "$LOGDIR/_status.log"
        exit 1
    fi
}

echo "$MODEL START concurrency=$CONC $(date -u)" >> "$LOGDIR/_status.log"

phase "collect"               "$PY" collect.py --model "$MODEL" --concurrency "$CONC"
phase "matrix"                "$PY" collect_matrix.py --model "$MODEL" --concurrency "$CONC"
phase "classify"              "$PY" classify.py --model "$MODEL"
phase "sensitivity"           "$PY" collect_sensitivity.py --model "$MODEL" --concurrency "$CONC"
phase "narrative-sensitivity" "$PY" collect_narrative_sensitivity.py --model "$MODEL" --concurrency "$CONC"

# Retry pass: reset HTTP-layer failures to pending, refill gently.
phase "retry-reset"           "$PY" retry_errors.py --model "$MODEL"
phase "retry-collect"         "$PY" collect.py --model "$MODEL" --concurrency 1
phase "retry-matrix"          "$PY" collect_matrix.py --model "$MODEL" --concurrency 1
phase "retry-sensitivity"     "$PY" collect_sensitivity.py --model "$MODEL" --concurrency 1
phase "retry-narrative-sens"  "$PY" collect_narrative_sensitivity.py --model "$MODEL" --concurrency 1

phase "final-classify"        "$PY" classify.py --model "$MODEL"

echo "===== $MODEL done ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) =====" >> "$LOG"
echo "$MODEL DONE $(date -u)" >> "$LOGDIR/_status.log"

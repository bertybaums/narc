#!/bin/bash
# Follow-up runs for the NARC-tiny puzzles, every model in turn (collect_narc_tiny.py):
# grammar text alone, classify, order shuffles for untested NARC cells, keyword ablation,
# classify. Resumable: relaunching picks up where it left off.
#
#   ./run_narc_tiny.sh [CONCURRENCY]      (default concurrency 4)
#
# Run on prod from inside the container so it uses the live DB + key:
#   ssh devops@bbaum.insight.uidaho.edu \
#     'cd ~/narc && docker exec -d narc-narc-1 ./run_narc_tiny.sh 4'
#   tail -f ~/narc/data/backfill_logs/narc_tiny.log
# NOTE: a redeploy restarts the container and kills a running job. Relaunch afterwards.
set -u
CONC="${1:-4}"
PY="${PYTHON:-python}"
LOGDIR="data/backfill_logs"
LOG="$LOGDIR/narc_tiny.log"
mkdir -p "$LOGDIR"

echo "narc_tiny START concurrency=$CONC $(date -u)" >> "$LOGDIR/_status.log"
echo "===== narc_tiny start ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) =====" >> "$LOG"
"$PY" collect_narc_tiny.py --all-models --concurrency "$CONC" >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    echo "===== narc_tiny FAILED (exit $rc) =====" >> "$LOG"
    echo "narc_tiny FAILED $(date -u)" >> "$LOGDIR/_status.log"
    exit 1
fi
echo "===== narc_tiny done ($(date -u '+%Y-%m-%d %H:%M:%S UTC')) =====" >> "$LOG"
echo "narc_tiny DONE $(date -u)" >> "$LOGDIR/_status.log"

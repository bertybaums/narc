#!/bin/bash
# Run backfill_model.sh for several models ONE AT A TIME.
#
#   ./run_backfills_sequential.sh [CONCURRENCY] [MODEL ...]     (default 6; default = every subject model)
#
# MindRouter loads models on demand, so ten parallel backfills make the cluster swap models
# in and out constantly: long latencies and 504s from the gateway while a model loads
# (Bert, September 29, 2026). One model at a time keeps it warm; use the per-model
# concurrency for throughput instead. Each model's run is resumable and a failure moves
# on to the next model; relaunch to fill gaps.
#
# On prod, detached:
#   docker exec -d narc-narc-1 ./run_backfills_sequential.sh 6
#   tail -f ~/narc/data/backfill_logs/_status.log
set -u
CONC="${1:-6}"; shift || true
if [ $# -gt 0 ]; then MODELS="$*"; else
    MODELS=$(python -c "import yaml; print(' '.join(m['name'] for m in yaml.safe_load(open('config.yaml'))['models'] if m.get('role')=='subject'))")
fi
mkdir -p data/backfill_logs
echo "sequential chain START concurrency=$CONC [$MODELS] $(date -u)" >> data/backfill_logs/_status.log
for m in $MODELS; do ./backfill_model.sh "$m" "$CONC"; done
echo "sequential chain END $(date -u)" >> data/backfill_logs/_status.log

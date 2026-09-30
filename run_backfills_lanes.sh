#!/bin/bash
# Run backfill_model.sh for every subject model IN PARALLEL, one lane per model, with a
# per-model concurrency matched to that model's MindRouter backends.
#
#   ./run_backfills_lanes.sh                      (all lanes below)
#   ./run_backfills_lanes.sh glm-5.3-flash 3      (one model, given concurrency)
#
# Why lanes, not sequential and not "everything at 4" (September 29, 2026):
#   * Each of our models has dedicated, named backends (GET /v1/models -> "backends"),
#     so models never evict each other; running them side by side is free.
#   * What is NOT free is load on one backend: under 8-way contention gpt-oss-20b's
#     same prompt ran 5.5K -> 65K tokens (3 of 6 hit the ceiling; alone it finished in
#     5.5K), so a heavy lane makes a reasoner loop and fail a puzzle it can solve.
#     Keep each lane at about one to two sequences per backend.
#   * models.call_llm streams (protocol v3): MindRouter's 180 s per-attempt / 300 s
#     total timeout applies to non-streaming requests only.
#   * The shared bucket (200 req/min) still caps the total.
#
# Backend counts from /v1/models on September 29, 2026 — recheck when MindRouter changes:
#   gpt-oss-120b 2 · gpt-oss-20b 1 · qwen3.5-122b 2 · qwen3.6-27b 1 · qwen3.8-27b 5 ·
#   nemotron 1 · gemma-4-26b 1 · gemma-4-31b 2 · glm 1 engine on 4 GPUs · mimo 1 on 4 GPUs
#
# On prod, detached:  docker exec -d narc-narc-1 ./run_backfills_lanes.sh
# Progress:           tail ~/narc/data/backfill_logs/_status.log ; per-model logs beside it.
set -u
LANES="gpt-oss-120b:3 gpt-oss-20b:2 qwen3.5-122b:3 qwen3.6-27b:2 qwen3.8-27b:5 nemotron-3-super:2 gemma-4-26b:3 gemma-4-31b:3 glm-5.3-flash:3 mimo-v2.6-flash:4"
if [ $# -ge 2 ]; then LANES="$1:$2"; fi
mkdir -p data/backfill_logs
echo "lanes START [$LANES] $(date -u)" >> data/backfill_logs/_status.log
for lane in $LANES; do
    ./backfill_model.sh "${lane%%:*}" "${lane##*:}" &
    sleep 2
done
wait
echo "lanes END $(date -u)" >> data/backfill_logs/_status.log

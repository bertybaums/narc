#!/bin/bash
# Copy the NHM simulator (nhmsim package + grammars) from the sibling ../nhm repository into
# nhm_vendor/, which is committed here so production (which git-pulls this repo) has it.
# Local development imports ../nhm directly when it exists (see proto.py), so run this
# before deploying whenever nhm/ changed.
set -e
cd "$(dirname "$0")"
SRC="${NHM_PATH:-../nhm}"
test -d "$SRC/nhmsim" || { echo "no nhmsim at $SRC"; exit 1; }
rm -rf nhm_vendor
mkdir -p nhm_vendor
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$SRC/nhmsim" "$SRC/grammars" nhm_vendor/
(cd "$SRC" && git rev-parse --short HEAD 2>/dev/null || echo "uncommitted") > nhm_vendor/SOURCE_COMMIT
echo "nhm_vendor/ refreshed from $SRC ($(cat nhm_vendor/SOURCE_COMMIT))"

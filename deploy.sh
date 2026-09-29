#!/bin/bash
# Deploy NARC to narc.insight.uidaho.edu
# Before deploying: run ./sync_nhm.sh if ../nhm changed (the Prototype tab imports nhm_vendor/ in prod).
set -e

echo "Deploying NARC..."
ssh devops@bbaum.insight.uidaho.edu "cd ~/narc && git pull && docker compose up -d --build && docker builder prune -f >/dev/null && sleep 5 && docker compose ps --format '{{.Name}} {{.Status}}' && df -h / | tail -1"
echo "Done. Site: https://narc.insight.uidaho.edu  (check the container says 'Up N seconds', not hours: a full disk leaves the old one running)"

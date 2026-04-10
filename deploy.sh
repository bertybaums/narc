#!/bin/bash
# Deploy NARC to narc.insight.uidaho.edu
set -e

echo "Deploying NARC..."
ssh devops@bbaum.insight.uidaho.edu "cd ~/narc && git pull && docker compose up -d --build"
echo "Done. Site: https://narc.insight.uidaho.edu"

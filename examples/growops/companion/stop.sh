#!/usr/bin/env bash
# Take the GrowOps Azure demo DOWN to ~$0 idle cost.
#
# Deletes the only two resources that bill while idle: the container apps
# (compute) and the container registry (ACR Basic, a fixed daily fee). KEEPS the
# Azure OpenAI account and the Container Apps environment — both cost nothing when
# idle, and keeping them avoids the Cognitive Services soft-delete dance and makes
# start.sh ~3x faster (no model/quota re-provisioning).
#
# Reverse with ./start.sh (~5 min).
set -euo pipefail

RG=growops-rg
ACR=growopsacr7a4315

echo "→ Deleting container apps (growops, growops-sensors) ..."
az containerapp delete -n growops          -g "$RG" --yes -o none || true
az containerapp delete -n growops-sensors  -g "$RG" --yes -o none || true

echo "→ Deleting registry $ACR ..."
az acr delete -n "$ACR" -g "$RG" --yes -o none || true

echo "✅ Stopped. Idle cost ~\$0 — Azure OpenAI + Container Apps env are kept (free at idle)."
echo "   Bring it back with: ./start.sh"
echo
echo "   # For a FULL wipe instead (true \$0, but start.sh must re-create the"
echo "   # Azure OpenAI account + gpt-4o deployment, and may hit a 48h soft-delete"
echo "   # name lock), run:"
echo "   #   az group delete -n $RG --yes"

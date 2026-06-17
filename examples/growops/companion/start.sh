#!/usr/bin/env bash
# Bring the GrowOps Azure demo UP from a stopped state (see ./stop.sh).
#
# Recreates the container registry + the mock sensor gateway + the agent app,
# reusing the kept Azure OpenAI account and Container Apps environment. Run this
# before a demo; run ./stop.sh after.
#
# Prereqs: az login, the `abp` CLI on PATH, and the kept resources still present
# (Azure OpenAI account + Container Apps environment). If you did a full
# `az group delete`, use the WALKTHROUGH.md M16 steps instead — this script does
# not re-create the model.
set -euo pipefail

# --- config: must match the deploy.azure block in ../growops.yml ---
RG=growops-rg
ACR=growopsacr7a4315
ENV=growops-env
AOAI=growops-openai7a4315
SENSORS_APP=growops-sensors
# -------------------------------------------------------------------

HERE="$(cd "$(dirname "$0")" && pwd)"
BLUEPRINT="$(cd "$HERE/.." && pwd)/growops.yml"

echo "→ Recreating registry $ACR ..."
az acr create -n "$ACR" -g "$RG" --sku Basic -o none

echo "→ Building the mock sensor image in ACR ..."
az acr build --registry "$ACR" --image mock-sensors:latest "$HERE" -o none

echo "→ Deploying the mock sensor gateway ..."
az containerapp create -n "$SENSORS_APP" -g "$RG" --environment "$ENV" \
  --image "$ACR.azurecr.io/mock-sensors:latest" --registry-server "$ACR.azurecr.io" \
  --ingress external --target-port 8080 --min-replicas 1 --max-replicas 1 -o none
SENSOR_FQDN=$(az containerapp show -n "$SENSORS_APP" -g "$RG" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "→ Deploying the GrowOps agent ..."
export AZURE_OPENAI_ENDPOINT="$(az cognitiveservices account show -n "$AOAI" -g "$RG" --query properties.endpoint -o tsv)"
export AZURE_OPENAI_API_KEY="$(az cognitiveservices account keys list -n "$AOAI" -g "$RG" --query key1 -o tsv)"
export SENSOR_GATEWAY_URL="https://$SENSOR_FQDN"
export SENSOR_API_KEY=demo-sensor-key
export ACTUATOR_TOKEN=demo-actuator-token
export ACTUATOR_GATEWAY_URL="https://actuators.growops.invalid"   # placeholder — actuators are human-gated
abp deploy "$BLUEPRINT"

echo
echo "✅ GrowOps is up. The agent URL is printed above; the mock sensor gateway is"
echo "   https://$SENSOR_FQDN"
echo "   Smoke test:  curl https://<app-fqdn>/health"

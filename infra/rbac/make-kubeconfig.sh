#!/usr/bin/env bash
# Builds a kubeconfig for the agent's ServiceAccount with a SHORT-LIVED token (default 24h).
# Usage: ./make-kubeconfig.sh [readonly|operator] [duration]
set -euo pipefail
ROLE="${1:-readonly}"
DURATION="${2:-24h}"
PROFILE="${PROFILE:-sre-poc}"
SA="sre-agent-${ROLE}"
NS="sre-agent"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/generated"
OUT="${OUT_DIR}/kubeconfig-agent-${ROLE}"
mkdir -p "$OUT_DIR"

SERVER="$(kubectl config view --minify --context "$PROFILE" -o jsonpath='{.clusters[0].cluster.server}')"
# minikube references the CA by file; kind embeds it (certificate-authority-data).
CA_FILE="$(kubectl config view --minify --context "$PROFILE" -o jsonpath='{.clusters[0].cluster.certificate-authority}')"
if [[ -z "$CA_FILE" ]]; then
  CA_FILE="$(mktemp)"
  trap 'rm -f "$CA_FILE"' EXIT
  kubectl config view --raw --minify --context "$PROFILE" \
    -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "$CA_FILE"
fi
TOKEN="$(kubectl --context "$PROFILE" create token "$SA" -n "$NS" --duration="$DURATION")"

rm -f "$OUT"
KUBECONFIG="$OUT" kubectl config set-cluster "$PROFILE" --server="$SERVER" --certificate-authority="$CA_FILE" --embed-certs=true >/dev/null
KUBECONFIG="$OUT" kubectl config set-credentials "$SA" --token="$TOKEN" >/dev/null
KUBECONFIG="$OUT" kubectl config set-context "$SA" --cluster="$PROFILE" --user="$SA" --namespace=demo >/dev/null
KUBECONFIG="$OUT" kubectl config use-context "$SA" >/dev/null
chmod 600 "$OUT"
echo "wrote $OUT (token valid ${DURATION})"

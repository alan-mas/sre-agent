#!/usr/bin/env bash
# Local cluster for the SRE agent PoC. Idempotent.
set -euo pipefail
PROFILE="${PROFILE:-sre-poc}"
CPUS="${CPUS:-4}"
MEMORY="${MEMORY:-5g}"

if minikube status -p "$PROFILE" >/dev/null 2>&1; then
  echo "minikube profile '$PROFILE' already running"
else
  minikube start -p "$PROFILE" --driver=docker --cpus="$CPUS" --memory="$MEMORY"
fi
minikube addons enable metrics-server -p "$PROFILE"
kubectl config use-context "$PROFILE"
kubectl wait --for=condition=Available deployment/metrics-server -n kube-system --timeout=180s
kubectl get nodes -o wide

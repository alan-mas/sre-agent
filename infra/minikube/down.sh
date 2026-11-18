#!/usr/bin/env bash
# Stop (default) or delete the cluster: ./down.sh [--delete]
set -euo pipefail
PROFILE="${PROFILE:-sre-poc}"
if [[ "${1:-}" == "--delete" ]]; then minikube delete -p "$PROFILE"; else minikube stop -p "$PROFILE"; fi

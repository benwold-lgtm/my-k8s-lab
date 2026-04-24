#!/usr/bin/env bash
# Starts the IMAGE lab — scales all workloads back to 1 and re-enables ArgoCD
# auto-sync. Run this after IMAGE-shutdown.sh.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# Hardcoded IMAGE stack — intentionally explicit to avoid touching other labs
IMAGE_APPS=(comfyui)
IMAGE_NAMESPACES=(comfyui)

echo
echo "╔═══════════════════════════════════════════╗"
echo "║        my-k8s-lab  —  IMAGE Startup       ║"
echo "╚═══════════════════════════════════════════╝"
echo

# Scale all IMAGE deployments back to 1
info "Scaling all IMAGE deployments to 1..."
for ns in "${IMAGE_NAMESPACES[@]}"; do
  count=$(kubectl get deployments -n "$ns" --no-headers 2>/dev/null | wc -l)
  if [[ "$count" -gt 0 ]]; then
    kubectl scale deployment --all --replicas=1 -n "$ns"
    ok "  Scaled to 1: $ns"
  else
    warn "  No deployments in namespace $ns (skipping)"
  fi
done

# Re-enable ArgoCD auto-sync — ArgoCD will reconcile to current git state
info "Re-enabling ArgoCD auto-sync on IMAGE apps..."
for app in "${IMAGE_APPS[@]}"; do
  if kubectl get application "$app" -n argocd &>/dev/null; then
    kubectl patch application "$app" -n argocd --type=merge \
      -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
    ok "  Auto-sync enabled: $app"
  else
    warn "  Not found in ArgoCD: $app (skipping)"
  fi
done

echo
echo "─────────────────────────────────────────────────────────────────"
ok "IMAGE lab starting up."
echo
echo "  Watch pods come up:"
echo "    kubectl get pods -A -w"
echo
echo "  Service endpoints (once pods reach Running):"
echo "    ComfyUI      →  http://192.168.1.112:30084"
echo "─────────────────────────────────────────────────────────────────"

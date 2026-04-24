#!/usr/bin/env bash
# Shuts down the IMAGE lab — disables ArgoCD auto-sync on all IMAGE apps then
# scales every workload to 0. Storage and git config are untouched.
# Run IMAGE-startup.sh to bring everything back online.
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
echo "║       my-k8s-lab  —  IMAGE Shutdown       ║"
echo "╚═══════════════════════════════════════════╝"
echo

# Disable ArgoCD auto-sync first so it doesn't scale pods back up
info "Disabling ArgoCD auto-sync on IMAGE apps..."
for app in "${IMAGE_APPS[@]}"; do
  if kubectl get application "$app" -n argocd &>/dev/null; then
    kubectl patch application "$app" -n argocd --type=merge \
      -p '{"spec":{"syncPolicy":null}}'
    ok "  Auto-sync disabled: $app"
  else
    warn "  Not found in ArgoCD: $app (skipping)"
  fi
done

# Scale all IMAGE deployments to 0
info "Scaling all IMAGE deployments to 0..."
for ns in "${IMAGE_NAMESPACES[@]}"; do
  count=$(kubectl get deployments -n "$ns" --no-headers 2>/dev/null | wc -l)
  if [[ "$count" -gt 0 ]]; then
    kubectl scale deployment --all --replicas=0 -n "$ns"
    ok "  Scaled to 0: $ns"
  else
    warn "  No deployments in namespace $ns (skipping)"
  fi
done

echo
echo "─────────────────────────────────────────────────────────────────"
ok "IMAGE lab suspended. GPU and RAM are now free."
echo
echo "  Verify all IMAGE pods are stopped:"
echo "    kubectl get pods -n comfyui"
echo
echo "  Bring the IMAGE lab back up:"
echo "    ./scripts/IMAGE-startup.sh"
echo "─────────────────────────────────────────────────────────────────"

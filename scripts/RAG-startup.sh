#!/usr/bin/env bash
# Starts the RAG lab — scales all workloads back to 1 and re-enables ArgoCD
# auto-sync. Run this after RAG-shutdown.sh.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# Hardcoded RAG stack — intentionally explicit to avoid touching other labs
RAG_APPS=(vllm-inference-server ai-agent embedding ingestion open-webui qdrant)
RAG_NAMESPACES=(ai-stack ai-agent embedding ingestion open-webui qdrant)

echo
echo "╔═══════════════════════════════════════════╗"
echo "║        my-k8s-lab  —  RAG Startup         ║"
echo "╚═══════════════════════════════════════════╝"
echo

# Scale all RAG deployments back to 1
info "Scaling all RAG deployments to 1..."
for ns in "${RAG_NAMESPACES[@]}"; do
  count=$(kubectl get deployments -n "$ns" --no-headers 2>/dev/null | wc -l)
  if [[ "$count" -gt 0 ]]; then
    kubectl scale deployment --all --replicas=1 -n "$ns"
    ok "  Scaled to 1: $ns"
  else
    warn "  No deployments in namespace $ns (skipping)"
  fi
done

# Re-enable ArgoCD auto-sync — ArgoCD will reconcile to current git state
info "Re-enabling ArgoCD auto-sync on RAG apps..."
for app in "${RAG_APPS[@]}"; do
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
ok "RAG lab starting up. Note: vLLM takes a few minutes to load the model."
echo
echo "  Watch pods come up:"
echo "    kubectl get pods -A -w"
echo
echo "  Service endpoints (once pods reach Running):"
echo "    Open-WebUI   →  http://192.168.1.112:30080"
echo "    vLLM API     →  http://192.168.1.112:30000/v1"
echo "    ai-agent     →  http://192.168.1.112:30081"
echo "    Qdrant REST  →  http://192.168.1.112:30333"
echo "─────────────────────────────────────────────────────────────────"

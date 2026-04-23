#!/usr/bin/env bash
# Bootstrap script for my-k8s-lab.
# Run this on vmenuc (or any host with kubectl + helm configured for the cluster).
# Assumes: Kubernetes cluster is up and nodes are joined. kubeconfig is set.
# Does NOT provision VMs or install Kubernetes itself.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[FAIL]${NC}  $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Config ─────────────────────────────────────────────────────────────────────
NFS_SERVER="192.168.1.250"
# Both nfs-client PVCs (ingestion, open-webui) and the vLLM static PV share this
# export. nfs-subdir-external-provisioner creates subdirectories within it, so
# there is no conflict with the vLLM models stored at the root.
NFS_PATH="/NFS_K8S_PV"

ARGOCD_MANIFEST="https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml"
LOCAL_PATH_MANIFEST="https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml"

# ── Pre-flight ─────────────────────────────────────────────────────────────────
echo
echo "╔═══════════════════════════════════════════╗"
echo "║        my-k8s-lab  —  Bootstrap           ║"
echo "╚═══════════════════════════════════════════╝"
echo
info "Running pre-flight checks..."
for cmd in kubectl helm; do
  command -v "$cmd" &>/dev/null || die "'$cmd' not found in PATH"
done
kubectl cluster-info &>/dev/null || die "Cannot reach cluster — check kubeconfig"
ok "Cluster reachable"

# ── ArgoCD ─────────────────────────────────────────────────────────────────────
if kubectl get deployment argocd-server -n argocd &>/dev/null; then
  warn "ArgoCD already installed — skipping"
else
  info "Creating argocd namespace..."
  kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
  info "Applying ArgoCD stable manifests (takes ~1 min)..."
  kubectl apply -n argocd -f "$ARGOCD_MANIFEST"
  info "Waiting for argocd-server to become ready..."
  kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
  ok "ArgoCD ready"
fi

# ── Storage: local-path-provisioner (used by qdrant) ───────────────────────────
if kubectl get storageclass local-path &>/dev/null; then
  warn "StorageClass 'local-path' already exists — skipping"
else
  info "Installing local-path-provisioner..."
  kubectl apply -f "$LOCAL_PATH_MANIFEST"
  ok "local-path-provisioner installed"
fi

# ── Storage: nfs-subdir-external-provisioner (used by ingestion, open-webui) ───
if kubectl get storageclass nfs-client &>/dev/null; then
  warn "StorageClass 'nfs-client' already exists — skipping"
else
  info "Adding nfs-subdir-external-provisioner Helm repo..."
  helm repo add nfs-subdir-external-provisioner \
    https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/ --force-update
  info "Installing nfs-client StorageClass (NFS ${NFS_SERVER}:${NFS_PATH})..."
  helm install nfs-subdir-external-provisioner \
    nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
    --namespace kube-system \
    --set nfs.server="${NFS_SERVER}" \
    --set nfs.path="${NFS_PATH}" \
    --set storageClass.name=nfs-client \
    --set storageClass.reclaimPolicy=Retain
  ok "nfs-client StorageClass installed"
fi

# ── Namespaces ─────────────────────────────────────────────────────────────────
# ArgoCD creates namespaces on sync (CreateNamespace=true), but secrets must
# exist before the first sync or pods will fail to start.
info "Creating namespaces for pre-sync secrets..."
for ns in ai-agent qdrant ai-stack; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
ok "Namespaces ready"

# ── Secrets ────────────────────────────────────────────────────────────────────
prompt_secret() {
  local prompt="$1" varname="$2" value=""
  while [[ -z "$value" ]]; do
    read -rsp "  ${prompt}: " value; echo
    [[ -z "$value" ]] && warn "Value cannot be empty — try again."
  done
  printf -v "$varname" '%s' "$value"
}

echo
info "Creating Kubernetes secrets — input is hidden, press Enter after each value."
echo

if kubectl get secret ai-agent-secrets -n ai-agent &>/dev/null; then
  warn "Secret 'ai-agent-secrets' already exists — skipping"
else
  prompt_secret "BRAVE_API_KEY (for ai-agent web search)" BRAVE_API_KEY
  prompt_secret "QDRANT_API_KEY (for ai-agent RAG access)" QDRANT_API_KEY_AGENT
  kubectl create secret generic ai-agent-secrets -n ai-agent \
    --from-literal=BRAVE_API_KEY="${BRAVE_API_KEY}" \
    --from-literal=QDRANT_API_KEY="${QDRANT_API_KEY_AGENT}"
  ok "Created secret 'ai-agent-secrets' in namespace ai-agent"
fi

if kubectl get secret qdrant-secrets -n qdrant &>/dev/null; then
  warn "Secret 'qdrant-secrets' already exists — skipping"
else
  prompt_secret "QDRANT_API_KEY (for qdrant)" QDRANT_API_KEY
  kubectl create secret generic qdrant-secrets -n qdrant \
    --from-literal=QDRANT_API_KEY="${QDRANT_API_KEY}"
  ok "Created secret 'qdrant-secrets' in namespace qdrant"
fi

if kubectl get secret hf-token-secret -n ai-stack &>/dev/null; then
  warn "Secret 'hf-token-secret' already exists — skipping"
else
  prompt_secret "HF_TOKEN (Hugging Face, for vLLM model download)" HF_TOKEN
  kubectl create secret generic hf-token-secret -n ai-stack \
    --from-literal=token="${HF_TOKEN}"
  ok "Created secret 'hf-token-secret' in namespace ai-stack"
fi

# ── App of Apps ────────────────────────────────────────────────────────────────
info "Applying ArgoCD master app (triggers full AI stack sync)..."
kubectl apply -f "${REPO_ROOT}/argocd-apps/master-app.yaml"
ok "master-app applied — ArgoCD will now sync all six services"

# ── Done ───────────────────────────────────────────────────────────────────────
echo
echo "─────────────────────────────────────────────────────────────────"
ok "Bootstrap complete."
echo
echo "  Watch rollout:"
echo "    kubectl get pods -A -w"
echo "    kubectl get applications -n argocd"
echo
echo "  Service endpoints (available once pods reach Running):"
echo "    Open-WebUI   →  http://192.168.1.112:30080"
echo "    vLLM API     →  http://192.168.1.112:30000/v1"
echo "    ai-agent     →  http://192.168.1.112:30081"
echo "    embedding    →  http://192.168.1.112:30082"
echo "    ingestion    →  http://192.168.1.112:30083"
echo "    Qdrant REST  →  http://192.168.1.112:30333"
echo
echo "  ArgoCD UI (via port-forward from vmenuc):"
echo "    kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo "    https://localhost:8080"
echo "─────────────────────────────────────────────────────────────────"

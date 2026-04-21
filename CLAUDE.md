# my-k8s-lab

GitOps homelab running a self-hosted AI stack on Kubernetes, managed via ArgoCD using the App of Apps pattern. All config lives in this repo; ArgoCD syncs it to the cluster automatically.

---

## Hardware

| Host | Type | CPU | RAM | Storage | NIC |
|---|---|---|---|---|---|
| `vmenuc` | Intel NUC (physical) | Intel i3 | 48 GB | 480 GB SSD | 1 GbE |
| `bengpu1` | Tower (physical) | AMD Ryzen 5900X | 128 GB | 2 TB NVMe M.2 | 1 GbE |
| Workstation | Laptop (Windows 11) | AMD Ryzen AI 7 350 | — | — | WiFi |

`vmenuc` runs KVM and hosts the three Kubernetes VMs. `bengpu1` has an RTX 3090 and runs all AI workloads. Both physical nodes connect to a 1 GbE Unifi switch.

**Software on hosts:**
- `vmenuc`: KVM, kubectl, helm, conda
- `k8s-master`: kubectl
- Workstation: VS Code, Claude Code, mPutty

---

## IP Address Map

| Hostname | IP | Role |
|---|---|---|
| `vmenuc` | `192.168.1.216` | KVM hypervisor, admin tooling |
| `k8s-master` | `192.168.1.202` | Kubernetes control plane only |
| `k8s-worker1` | `192.168.1.190` | Platform tooling (ArgoCD) |
| `k8s-worker2` | `192.168.1.206` | Future workloads / overflow |
| `bengpu1` | `192.168.1.112` | AI stack (all AI workloads pinned here) |
| NFS Server | `192.168.1.250` | Shared persistent storage |

---

## Kubernetes Cluster

Three VMs hosted on `vmenuc` via KVM, plus `bengpu1` joined as a bare-metal worker.

| Node | Role |
|---|---|
| `k8s-master` | Control plane only — no workloads scheduled |
| `k8s-worker1` | Platform tooling (ArgoCD lives here) |
| `k8s-worker2` | Reserved for future / overflow workloads |
| `bengpu1` | All AI stack workloads pinned here via `nodeSelector` |

---

## AI Stack

All services run on `bengpu1` via `nodeSelector: kubernetes.io/hostname: bengpu1`.

| Service | Image | Description |
|---|---|---|
| `vllm-server` | `vllm/vllm-openai:latest` | LLM inference — Mistral-Nemo-Instruct-FP8-2407 |
| `open-webui` | `ghcr.io/open-webui/open-webui:main` | Chat UI — talks to ai-agent as its backend |
| `ai-agent` | `ghcr.io/benwold-lgtm/ai-agent:latest` | Custom RAG agent — calls vLLM + Qdrant |
| `embedding` | `ghcr.io/benwold-lgtm/embedding:latest` | Embedding service — nomic-embed-text-v1.5 |
| `ingestion` | `ghcr.io/benwold-lgtm/ingestion:latest` | Document ingestion pipeline |
| `qdrant` | `qdrant/qdrant:v1.9.0` | Vector database |

### NodePort Assignments

Do not reuse these ports.

| NodePort | Service | Internal Port | Protocol |
|---|---|---|---|
| `30000` | vllm-server | 8000 | HTTP (OpenAI-compatible API) |
| `30080` | open-webui | 8080 | HTTP |
| `30081` | ai-agent | 8000 | HTTP |
| `30082` | embedding | 8001 | HTTP |
| `30083` | ingestion | 8002 | HTTP |
| `30333` | qdrant | 6333 | HTTP (REST) |
| `30334` | qdrant | 6334 | gRPC |

Next available NodePort: `30084` (for new services, continue from here).

### Internal Service URLs (cluster-DNS)

| Service | URL |
|---|---|
| vllm-server | `http://192.168.1.112:30000/v1` |
| ai-agent | `http://ai-agent.ai-agent.svc.cluster.local:8000` |
| embedding | `http://embedding.embedding.svc.cluster.local:8001` |
| ingestion | `http://ingestion.ingestion.svc.cluster.local:8002` |
| qdrant | `http://qdrant.qdrant.svc.cluster.local:6333` |

---

## Repo Structure

```
my-k8s-lab/
├── .github/workflows/       # CI: build & push Docker images to ghcr.io/benwold-lgtm
├── ai-stack/
│   ├── charts/              # Helm charts — one per service
│   │   ├── ai-agent/
│   │   ├── embedding/
│   │   ├── ingestion/
│   │   ├── open-webui/
│   │   ├── qdrant/
│   │   └── vllm-server/
│   └── services/            # Python microservice source (Dockerfile + main.py + requirements.txt)
│       ├── ai-agent/
│       ├── embedding/
│       └── ingestion/
├── argocd-apps/             # ArgoCD Application manifests (App of Apps pattern)
├── docs/                    # (empty — reserved)
├── lab-config/              # (empty — reserved)
└── scripts/                 # (empty — reserved)
```

Each Helm chart follows the same layout:
```
charts/<service>/
├── Chart.yaml
├── values.yaml              # All configurable values live here
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    └── pvc.yaml             # (where applicable)
```

ArgoCD watches `argocd-apps/` via the master app (`master-app.yaml`), which in turn manages all individual Application manifests. Each app syncs its chart from `ai-stack/charts/<service>/`.

---

## Coding Standards

### Configuration belongs in values.yaml

Never hardcode cluster-specific values in templates. All environment-specific config (node names, IPs, ports, resource limits, image tags) goes in `values.yaml` and is referenced from templates.

**Correct:**
```yaml
# templates/deployment.yaml
nodeSelector: {{ toYaml .Values.nodeSelector | nindent 8 }}
```

**Wrong:**
```yaml
# templates/deployment.yaml
nodeSelector:
  kubernetes.io/hostname: bengpu1
```

### nodeSelector

Every chart must pin workloads via `values.yaml`:
```yaml
# values.yaml
nodeSelector:
  kubernetes.io/hostname: bengpu1
```

Referenced in `templates/deployment.yaml` as:
```yaml
nodeSelector: {{ toYaml .Values.nodeSelector | nindent 8 }}
```

### Ports

Always declare `nodePort` in `values.yaml` and reference it in the service template via `{{ .Values.service.nodePort }}`. Check the NodePort table above before adding a new service — do not reuse or guess ports.

---

## Git Workflow

Always show a `git diff` and get explicit approval before committing or pushing. This is a GitOps repo — a push to `main` triggers ArgoCD sync and directly affects the running cluster.

1. Make changes
2. Run `git diff` and show the output
3. Wait for approval
4. Commit and push only after explicit confirmation

CI pipelines (`.github/workflows/`) build and push Docker images to `ghcr.io/benwold-lgtm` on push to `main` for the three custom services (ai-agent, embedding, ingestion).

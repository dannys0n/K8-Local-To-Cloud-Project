# TCP Kubernetes Infrastructure Lab

A small, replaceable application used to exercise Kubernetes infrastructure:

```text
client -> NodePort/NLB -> HAProxy Deployment -> TCP Server StatefulSet -> PVC
```

It intentionally omits the eventual coordinator, database, ownership, fencing, authentication, and custom proxy logic.

## What runs

- **HAProxy:** two replicas, raw TCP mode, live statistics page.
- **TCP server:** three StatefulSet replicas with stable names and one PVC each.
- **Client entry:** `127.0.0.1:9000` on kind; an AWS NLB in the EKS overlay.
- **Manifest management:** a shared Kustomize base plus kind and EKS overlays.

## Prerequisites

Install and make available on `PATH`:

- Docker Desktop or Docker Engine
- kind
- kubectl

The interactive PowerShell client uses only .NET. The Python clients are optional.

## Run on Windows PowerShell

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/up.ps1
powershell -ExecutionPolicy Bypass -File tools/smoke.ps1
powershell -ExecutionPolicy Bypass -File tools/client.ps1
```

Open the live HAProxy page:

```text
http://127.0.0.1:8404/stats
```

Inspect the cluster:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/status.ps1
```

Delete everything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/down.ps1
```

## Run on Linux, macOS, or WSL

```bash
./scripts/up.sh
python3 tools/smoke.py
python3 tools/client.py
```

Delete everything:

```bash
./scripts/down.sh
```

## Expected response

Send a line such as `hello` and receive one JSON line:

```json
{"server":"tcp-server-0","counter":1,"message":"hello","time":"2026-07-31T00:00:00Z"}
```

A single persistent client connection stays on one HAProxy pod and one selected backend server. Open multiple clients to observe distribution among servers.

## Useful commands

```bash
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
kubectl logs -n tcp-lab deployment/haproxy
kubectl logs -n tcp-lab tcp-server-0
kubectl scale deployment/haproxy -n tcp-lab --replicas=3
kubectl scale statefulset/tcp-server -n tcp-lab --replicas=5
```

See:

- `docs/failure-drills.md`
- `docs/visualization.md`
- `docs/eks.md`

## Design boundaries

This is an infrastructure template. HAProxy and the tiny server are placeholders. The stable reusable pieces are the Services, Deployments, StatefulSets, PVCs, probes, disruption budgets, topology rules, and kind/EKS overlays.

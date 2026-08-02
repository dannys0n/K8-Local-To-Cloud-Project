# TCP Kubernetes Infrastructure Lab

A small, replaceable application used to exercise Kubernetes infrastructure:

```text
client -> NodePort/NLB -> Gateway Deployment -> TCP Server pool
                                              -> PostgreSQL + Redis
```

It intentionally keeps ownership and fencing inside PostgreSQL instead of
adding a coordinator or operator.

## What runs

- **Gateway:** four generic Go replicas that preserve the client connection
  while switching downstream location servers at runtime.
- **TCP server:** ten interchangeable Deployment replicas: five active location
  owners and five ready hot spares.
- **PostgreSQL:** authoritative location identity, counters, leases, and ownership
  generations; one PVC on the dedicated kind database worker.
- **Redis:** ephemeral server-presence and counter-cache keys on the same kind
  database worker.
- **Client entry:** `127.0.0.1:9000`, with an optional location handshake; an
  AWS NLB in the EKS overlay.
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

Select a stable virtual location:

```powershell
powershell -ExecutionPolicy Bypass -File tools/client.ps1 -Location los-angeles
powershell -ExecutionPolicy Bypass -File tools/client.ps1 -Location new-york
powershell -ExecutionPolicy Bypass -File tools/client.ps1 -Location london
powershell -ExecutionPolicy Bypass -File tools/client.ps1 -Location singapore
powershell -ExecutionPolicy Bypass -File tools/client.ps1 -Location frankfurt
```

The location endpoints are deliberately simple and fixed for this lab:

| Location | kind port | Logical identity |
|---|---:|---|
| Los Angeles | 9000 | `tcp-server-0` |
| New York | 9000 | `tcp-server-1` |
| London | 9000 | `tcp-server-2` |
| Singapore | 9000 | `tcp-server-3` |
| Frankfurt | 9000 | `tcp-server-4` |

The client sends a small `@location` handshake that the gateway uses to select
the stable backend. The same command can change backends later without replacing
the client connection. After a gateway disconnect, the client reconnects with
the same handshake.
The logical server identity and counter remain in PostgreSQL when a pod is
replaced. An expired 1.5-second lease is claimed by an already-running spare;
the generation increases to fence the old owner. Redis presence keys expire and
repopulate automatically. A request
whose response is lost during a disconnect may be retried, so this toy protocol
is not an exactly-once protocol. Without `-Location`, port 9000 remains the
original round-robin endpoint.

While the client is running, change locations or force a fresh connection:

```text
/location london
/location singapore
/reconnect
/status
```

`/location` keeps the client socket open and asks its current gateway to switch
the downstream server. `/reconnect` keeps the location but creates a new client
connection, which can land on any gateway replica. Use the aggregate dashboard
to see the gateway and backend independently.

Open the selected gateway replica's live page:

```text
http://127.0.0.1:8404/
```

Open the local aggregate connection dashboard (requires Python and a running
`kubectl` context):

```powershell
python tools/dashboard.py
```

Then visit `http://127.0.0.1:8080`. This combines every gateway pod and lists
active client sessions, routes, generations, and backend instances.

Inspect the cluster:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/status.ps1
```

Run the scheduling regression tests:

```bash
python -m unittest discover -s tests -v
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
{"server":"tcp-server-0","location":"los-angeles","instance":"tcp-server-abc","generation":3,"counter":1,"message":"hello","time":"2026-07-31T00:00:00Z"}
```

A single persistent client connection stays on one gateway pod, while its
selected backend server can change. Open multiple clients to observe both forms
of distribution.

## Useful commands

```bash
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
kubectl logs -n tcp-lab deployment/gateway
kubectl logs -n tcp-lab deployment/tcp-server
kubectl scale deployment/gateway -n tcp-lab --replicas=4
kubectl scale deployment/tcp-server -n tcp-lab --replicas=10
```

See:

- `docs/failure-drills.md`
- `docs/visualization.md`
- `docs/eks.md`

## Design boundaries

This is an infrastructure template. The small gateway, tiny server, and local
database deployments are placeholders. The reusable pieces are the Services,
Deployments, probes, disruption budgets, topology rules, and kind/EKS overlays.
The credentials in the kind overlay are development-only.

Kind labels `tcp-lab-worker` (the first worker) as its database worker.
PostgreSQL and Redis use
hard affinity and tolerate its `NoSchedule` taint; gateways and servers cannot
schedule there. The kind PostgreSQL PVC is node-local and cannot follow its pod
to another node without a shared storage class. The EKS overlay deploys no
database pods and expects managed PostgreSQL and Redis-compatible services
outside the worker pool.
Changing the dedicated kind database worker requires recreating the cluster;
the startup scripts reject an in-place move that would strand the local PVC.

Kubernetes restores failed pods and nodes, but location recovery does not wait
for node eviction. Ready spare pods poll PostgreSQL-backed leases every 250ms;
after a 1.5-second lease expires, one spare atomically claims the location and
increments its fencing generation. These lab defaults are configurable through
`ASSIGNMENT_LEASE_DURATION` and `ASSIGNMENT_RENEW_INTERVAL`; production values
must be validated against database and network latency. The replacement
application remains responsible for resumable sessions and application-specific
durability semantics.

Startup probes give server and gateway containers up to 90 seconds to initialize
without liveness restarts. Once startup succeeds, their readiness and liveness
checks switch to aggressive runtime detection. Fresh kind clusters report node
status every second, allow five seconds without a heartbeat, and immediately
evict these application pods once the failed-node taint appears. Kind also
raises both node-eviction rates so simultaneous worker losses are processed
together instead of at the conservative default rate.

Gateway health is independent from server ownership. Kubernetes readiness and
liveness checks remove or restart an unhealthy gateway, and the EKS NLB checks
gateway targets directly. Gateways discover every active location owner and do
not claim, rebalance, or exclusively own servers. Hard hostname spreading keeps
the ten server pods and four gateways distributed across kind's four general
workers; spread counts the current rollout revision, and failed-node taints are
honored so replacement pods can consolidate on survivors. The five ready server
spares provide immediate location handoff, while Kubernetes promptly creates
replacement pods to replenish that pool.
Kubernetes does not automatically rebalance healthy pods when repaired workers
return; the failure drill documents the explicit rolling rebalance command.

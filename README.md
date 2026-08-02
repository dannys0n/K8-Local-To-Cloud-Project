# TCP Kubernetes Infrastructure Lab

A small, replaceable application used to exercise Kubernetes infrastructure:

```text
client -> NodePort/NLB -> HAProxy Deployment -> TCP Server pool
                                              -> PostgreSQL + Redis
```

It intentionally keeps ownership and fencing inside PostgreSQL instead of
adding a coordinator, operator, or custom proxy.

## What runs

- **HAProxy:** three replicas, raw TCP mode, live statistics page.
- **TCP server:** ten interchangeable Deployment replicas: five active location
  owners and five ready hot spares.
- **PostgreSQL:** authoritative location identity, counters, leases, and ownership
  generations; one PVC in kind.
- **Redis:** ephemeral server-presence and counter-cache keys.
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

The client sends a small `@location` handshake that HAProxy uses to select the
stable backend, then reconnects with the same handshake after a disconnect.
The logical server identity and counter remain in PostgreSQL when a pod is
replaced. An expired three-second lease is claimed by an already-running spare;
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

`/location` closes the existing socket and connects to the selected stable
server. `/reconnect` keeps the location but creates a new connection, which can
land on any HAProxy replica. Use the aggregate dashboard to see the proxy and
backend change.

Open the live HAProxy page:

```text
http://127.0.0.1:8404/stats
```

Open the local aggregate connection dashboard (requires Python and a running
`kubectl` context):

```powershell
python tools/dashboard.py
```

Then visit `http://127.0.0.1:8080`. Unlike HAProxy's per-replica statistics
page, this combines every HAProxy pod and lists active client sessions.

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

A single persistent client connection stays on one HAProxy pod and one selected backend server. Open multiple clients to observe distribution among servers.

## Useful commands

```bash
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
kubectl logs -n tcp-lab deployment/haproxy
kubectl logs -n tcp-lab deployment/tcp-server
kubectl scale deployment/haproxy -n tcp-lab --replicas=4
kubectl scale deployment/tcp-server -n tcp-lab --replicas=8
```

See:

- `docs/failure-drills.md`
- `docs/visualization.md`
- `docs/eks.md`

## Design boundaries

This is an infrastructure template. HAProxy, the tiny server, and the local
database deployments are placeholders. The reusable pieces are the Services,
Deployments, probes, disruption budgets, topology rules, and kind/EKS overlays.
The credentials in the kind overlay are development-only.

Worker nodes have no workload-specific roles. Topology spreading is a scheduler
preference, so healthy replicas spread when capacity exists but may all run on
one surviving worker. The kind PostgreSQL PVC is the local exception: durable
storage cannot follow its pod to another node without a shared storage class.
The EKS overlay expects managed PostgreSQL outside the worker pool.

Kubernetes restores failed pods and nodes, but location recovery does not wait
for node eviction. Ready spare pods poll PostgreSQL-backed leases every 500ms;
after a three-second lease expires, one spare atomically claims the location and
increments its fencing generation. These lab defaults are configurable through
`ASSIGNMENT_LEASE_DURATION` and `ASSIGNMENT_RENEW_INTERVAL`; production values
must be validated against database and network latency. The replacement
application remains responsible for resumable sessions and application-specific
durability semantics.

Proxy health is independent from server ownership. Kubernetes readiness and
liveness checks remove or restart an unhealthy proxy, and the EKS NLB checks
proxy targets directly. These signals must not claim or rebalance servers.
Future exclusive proxy-to-server ownership must use its own PostgreSQL-backed
lease and fencing generation so a replacement proxy can claim only an expired
assignment atomically.

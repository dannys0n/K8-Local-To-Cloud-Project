# Live connection visibility

## Grafana live-resource dashboard

The kind dependency installer deploys a small Grafana instance and
`kube-state-metrics` alongside the existing Prometheus collector. Start local
access explicitly:

```bash
kubectl port-forward -n tcp-lab service/grafana 3000:3000
```

Open `http://127.0.0.1:3000/d/tcp-lab-live`. The single provisioned dashboard
shows gateway TCP sessions, ready workload counts, pod CPU against requests,
memory against limits, pod network rates, restart totals, and requested node
capacity. Grafana is anonymous read-only and has no NodePort or EKS load-balancer
exposure.

Prometheus receives bounded per-pod gateway connection counters. Exact client
UIDs and TCP-session relationships stay in the aggregate dashboard below rather
than becoming high-cardinality Prometheus labels.

## Aggregate dashboard

Run the development-only dashboard from the repository root:

```bash
python tools/dashboard.py
```

Open `http://127.0.0.1:8080`. The page discovers all gateway pods, combines
their current session totals, routes, and active sessions from each gateway's
read-only statistics endpoint. Backend rows are one per logical location and show the
currently assigned physical server pod and worker node. `Logical server` is the durable location identity such as
`tcp-server-1`; `Active instance` is the replaceable Kubernetes pod currently
holding that identity. It uses the current `kubectl` context and binds only
to local loopback by default. It reads pod endpoints through the Kubernetes API;
the per-replica statistics Service is not exposed publicly on EKS.

Application pod inventory retains failed and terminating API objects for
diagnostics but excludes them from ready gateway and server-replica counts.
Separate counters report `NotReady` and `Unknown` pods. Map infrastructure nodes
use red for `NotReady`, gray for `Unknown`, and normal styling for ready pods.
Health combines the pod readiness
condition with the assigned node's Kubernetes `Ready` condition.

Client counters intentionally measure different layers. `Total clients` is the
number of TCP sessions accepted by reachable gateways, including sessions waiting
for a backend. `Connected clients` counts only sessions currently routed through
both a gateway and server. `Dummy threads` is the number of bot threads owned by
this dashboard process, whether ready, gateway-only, or disconnected. The session
table exposes `gateway` and `ready` status so the difference is inspectable.

The map update-rate slider targets 1–20 Hz and persists its setting in the
browser. Refreshes use a one-request-in-flight scheduler: if Kubernetes inventory
collection takes longer than the selected period, the next refresh waits rather
than overlapping or queuing stale snapshots.

Use the `Interactive map` tab, or open `http://127.0.0.1:8080/map`, to see
active geographic locations, client positions, and gateways.
The layer switches only affect visualization. The dashboard can spawn and
despawn local dummy-client batches, but location creation is owned exclusively
by the server autoscaler and the map provides no location mutation controls.

The client is independent of the infrastructure dashboard. It displays only
application data returned through its gateway connection: its selected gateway,
logical server, active server pod, locations, and nearby clients. The proxy layer
always draws the current gateway below the map with an edge to the client. Gateways
previously observed while that client page remains open stay in memory and appear
gray; they are forgotten on reload, and it does not enumerate gateway pods the
client has never used. Kubernetes
inventory such as every gateway, server pod, worker, and unhealthy pod remains
dashboard-only and is read through the operator's current `kubectl` context.
This keeps Kubernetes credentials and administrative data out of external
clients and the public EKS load balancer.

Other connected clients on the same authoritative server are shown as cyan map
pins when they fall within the existing spatial relevance radius. Normal input
responses carry this local view directly from in-memory server state; servers do
not exchange entity visibility through Valkey. Client markers turn gray after
one second without an observation and disappear after five seconds. Visibility
is never used for connection truth, entity authority, or recovery.

The Data services table uses Kubernetes pod status to show the in-cluster
Valkey instances, their worker placement, pod IPs, stable Service
endpoints, restart counts, and readiness. It does not inspect Valkey contents
or credentials. Managed EKS data services run outside the cluster and therefore do
not appear in this local infrastructure table.

The client address represents the network connection seen by the gateway. Once
the client has sent entity input, the gateway statistics also include its
application client UID and latest authoritative coordinates for map display.

## Per-replica page

Open the gateway statistics page selected through the local Service:

```text
http://127.0.0.1:8404/
```

It shows one gateway replica's discovered routes, current sessions, generations,
and accepted connection count. Refresh is set to two seconds. To inspect a
specific replica, port-forward that pod directly.

To inspect a specific gateway pod:

```bash
kubectl get pods -n tcp-lab -l app=gateway
kubectl port-forward -n tcp-lab pod/<gateway-pod-name> 8405:8404
```

Then open `http://127.0.0.1:8405/`.

For pod placement and restarts:

```bash
kubectl get pods -n tcp-lab -o wide -w
```

Pixie is intentionally not included in the local package: its current requirements list kind and Docker Desktop as unsupported because kind runs nodes inside containers. Pixie remains an optional EKS observability tool later.

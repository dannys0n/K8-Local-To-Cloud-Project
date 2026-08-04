# Live connection visibility

## Aggregate dashboard

Run the development-only dashboard from the repository root:

```bash
python tools/dashboard.py
```

Open `http://127.0.0.1:8080`. The page discovers all gateway pods, combines
their current counters, routes, and active sessions from each gateway's
read-only statistics endpoint. Backend rows are one per logical location and show the
currently assigned physical server pod and worker node, while the summary shows
available hot spares. `Logical server` is the durable location identity such as
`tcp-server-1`; `Active instance` is the replaceable Kubernetes pod currently
holding that identity. It uses the current `kubectl` context and binds only
to local loopback by default. It reads pod endpoints through the Kubernetes API;
the per-replica statistics Service is not exposed publicly on EKS.

Application pod inventory retains failed and terminating API objects for
diagnostics but excludes them from ready gateway, server-pool, and hot-spare
counts. Separate counters report `NotReady` and `Unknown` pods. Map infrastructure
nodes use red for `NotReady`, gray for `Unknown`, yellow for ready spares, and the
normal gateway/active styling for ready pods. Health combines the pod readiness
condition with the assigned node's Kubernetes `Ready` condition.

The map update-rate slider targets 1–20 Hz and persists its setting in the
browser. Refreshes use a one-request-in-flight scheduler: if Kubernetes inventory
collection takes longer than the selected period, the next refresh waits rather
than overlapping or queuing stale snapshots.

Use the `Interactive map` tab, or open `http://127.0.0.1:8080/map`, to see
active geographic locations, client positions, gateways, and spare server pods.
The layer switches only affect visualization. The map also provides explicit
debug controls to spawn/despawn local dummy-client batches, create a randomly
positioned location, create a location at a clicked coordinate, or disable a
selected location. Location controls call the PostgreSQL lifecycle functions
through the dashboard process and its current `kubectl` context; they are not
served by the public gateway or exposed through the EKS load balancer.

While the dashboard is running, the local map client can enable `Show proxies`
and `Show hot swaps`. Proxies appear in a screen-space row below the map, hot
spare server pods appear above it, and the selected gateway is connected to the
client marker. These optional layers read the dashboard's localhost-only JSON;
the gateway and server protocols do not expose Kubernetes inventory.

Other connected clients are shown as cyan map pins. Active servers exchange
best-effort visibility snapshots through Redis at 20 Hz, and normal input
responses carry the current combined view. Input sequences discard stale
pre-handoff copies, and entities expire after one second without a heartbeat.
Visibility is never used for entity authority or recovery.

The Data services table uses Kubernetes pod status to show the in-cluster
PostgreSQL and Redis instances, their worker placement, pod IPs, stable Service
endpoints, restart counts, and readiness. It does not inspect database contents
or credentials. Managed EKS databases run outside the cluster and therefore do
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

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

While the dashboard is running, the local map client can enable `Show proxies`
and `Show hot swaps`. Proxies appear in a screen-space row below the map, hot
spare server pods appear above it, and the selected gateway is connected to the
client marker. These optional layers read the dashboard's localhost-only JSON;
the gateway and server protocols do not expose Kubernetes inventory.

The Data services table uses Kubernetes pod status to show the in-cluster
PostgreSQL and Redis instances, their worker placement, pod IPs, stable Service
endpoints, restart counts, and readiness. It does not inspect database contents
or credentials. Managed EKS databases run outside the cluster and therefore do
not appear in this local infrastructure table.

The client address represents the network connection seen by the gateway, not an
application user identity. This lab's line protocol has no client identity.

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

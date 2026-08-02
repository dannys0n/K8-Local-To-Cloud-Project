# Live connection visibility

## Aggregate dashboard

Run the development-only dashboard from the repository root:

```bash
python tools/dashboard.py
```

Open `http://127.0.0.1:8080`. The page discovers all HAProxy pods, combines
their current frontend and backend counters, and lists active sessions reported
by HAProxy's Runtime API. Backend rows are one per logical location and show the
currently assigned physical server pod, while the summary shows available hot
spares. It uses the current `kubectl` context and binds only
to local loopback by default. The Runtime API also binds only to loopback inside
each HAProxy pod and is not exposed by a Kubernetes Service.

The client address represents the network connection seen by HAProxy, not an
application user identity. This lab's line protocol has no client identity.

## Built-in per-replica page

Open the built-in HAProxy statistics page:

```text
http://127.0.0.1:8404/stats
```

It shows the HAProxy replica selected for that browser connection, including backend endpoint health, current sessions, totals, errors, and traffic counters. Refresh is set to two seconds. To inspect a specific replica, port-forward that pod directly.

To inspect a specific HAProxy pod:

```bash
kubectl get pods -n tcp-lab -l app=haproxy
kubectl port-forward -n tcp-lab pod/<haproxy-pod-name> 8405:8404
```

Then open `http://127.0.0.1:8405/stats`.

For pod placement and restarts:

```bash
kubectl get pods -n tcp-lab -o wide -w
```

Pixie is intentionally not included in the local package: its current requirements list kind and Docker Desktop as unsupported because kind runs nodes inside containers. Pixie remains an optional EKS observability tool later.

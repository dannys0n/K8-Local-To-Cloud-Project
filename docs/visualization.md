# Live connection visibility

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

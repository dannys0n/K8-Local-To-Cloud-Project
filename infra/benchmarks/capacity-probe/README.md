# Capacity benchmark probe

This isolated workload measures Kubernetes scheduling, container startup,
readiness, deletion, HPA reaction, and worker provisioning. It has no Service
and never connects to the TCP lab or its databases.

Use `python tools/scale_benchmark.py --help`. Generated telemetry is written
under `.generated/scale-benchmarks/` and is intentionally ignored by Git.

Typical commands from the repository root:

```powershell
# One pod, many pods, HPA, kind worker loss, and optional application load.
python tools/scale_benchmark.py run --environment kind --mode full --application

# Force enough demand to observe EKS Auto Mode worker provisioning.
python tools/scale_benchmark.py run --environment eks --mode node-capacity `
  --replicas 24 --cpu 1 --memory 512Mi --pull-policy Always

python tools/scale_benchmark.py report
python tools/scale_benchmark.py cleanup
```

The runner refuses an environment that does not match the current Kubernetes
context. Every synthetic run recreates only `tcp-lab-benchmark`; it never
changes the TCP lab Deployments or autoscaler settings. Application tests use
the existing headless-client implementation and only observe those settings.

Raw JSON contains Kubernetes context, parameters, pod lifecycle timestamps,
node identity/capacity, HPA and Deployment transitions, Kubernetes Events,
cluster warnings, and the derived milestones used by `summary.csv`,
`aggregates.csv`, and `report.html`.

`report.html` is a self-contained, dependency-free report. Open it directly in
a browser to compare timing distributions across kind and EKS, then select an
individual run to inspect replica, CPU, client-connection, worker, pod lifecycle,
and milestone timelines. Rebuild it from the retained JSON at any time with:

```powershell
python tools/scale_benchmark.py report
start .generated\scale-benchmarks\report.html
```

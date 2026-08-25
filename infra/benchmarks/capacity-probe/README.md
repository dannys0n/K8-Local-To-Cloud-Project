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
cluster warnings, and the derived milestones used by `summary.csv` and
`report.html`.

Report columns are deliberately mode-specific. Direct replica requests, HPA
decisions, application autoscaling, worker-loss recovery, and new-node
provisioning/removal use separate start-to-end timing names. A blank cell means
that measurement does not apply to the run; values from different modes are
never combined under one generic autoscaler field.

## Visual and network evidence

The optional host-side evidence recorder starts local Grafana, topology-map,
and interactive-client views, takes synchronized screenshots at benchmark
milestones, exports Prometheus network telemetry, and records short 1920x1080
tours of Grafana's locked run timeline, the map, client, and static HTML report.
It does not record hours of idle browser footage, deploy recording software, or
add traffic collectors to the cluster.

Install its optional browser dependency once:

```powershell
python -m pip install -r tools/requirements-evidence.txt
python -m playwright install chromium
python tools/benchmark_evidence.py --self-test
```

Run it instead of invoking `scale_benchmark.py run` directly. Arguments after
`--` are unchanged benchmark arguments:

```powershell
python tools/benchmark_evidence.py -- `
  --environment kind --mode full --application --repetitions 3

python tools/benchmark_evidence.py -- `
  --environment eks --mode full --application --repetitions 3 `
  --clients 500 --load-seconds 180 --settle-seconds 180
```

The active `kubectl` context must already point at the intended cluster. The
recorder manages only its local processes and port-forward; cluster creation,
EKS login, and final EKS teardown remain explicit. A session is written to
`.generated/scale-benchmarks/<timestamp>-evidence-<environment>/` with:

- independent Grafana, client, topology-map, and report WebM recordings;
- milestone screenshots paired with the exact event JSON;
- aggregate and per-pod Prometheus network, CPU, memory, and latency telemetry;
- one-second external-client connection, route, and round-trip samples;
- before/after Kubernetes resource snapshots and the tested Git revision;
- a session-only HTML/CSV timing report (unmixed with previous benchmark runs);
- process logs, a manifest, a browsable `index.html`, and SHA-256 checksums.

Use `--headed` to watch the automated Chromium session. Use
`--skip-local-services` only when Grafana, the dashboard, and the client are
already listening on ports 3000, 8080, and 8081 respectively.

The evidence index summarizes saved external-client RTT, internal gateway-to-
server and server-to-Valkey latency, workload RX/TX throughput, and packet
drops. Missing telemetry is explicitly shown as `N/A`. When an application-load
window exists, summary values are scoped to that window while charts retain the
full run and shade the load interval. Existing evidence can be rebuilt without
a cluster or Playwright installation:

```powershell
python tools/benchmark_evidence.py --rebuild-session <session-directory-or-id>
```

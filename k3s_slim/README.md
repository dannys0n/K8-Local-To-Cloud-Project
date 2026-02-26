# k3s homelab cluster (k3sup + Helm)

Goal: a small, easy-to-understand multi-node k3s setup with automation.

This repo is split into two concerns:

1) **Bootstrap (imperative)**: create/join the cluster via SSH using `k3sup`.
2) **Platform + apps (declarative)**: install add-ons with Helm and deploy manifests under `k8s/`.

## Prereqs (on your laptop/desktop)
- SSH access to all nodes (key-based login recommended)
- `kubectl` installed
- `helm` installed
- `k3sup` installed (or let `scripts/bootstrap.sh` install it)

## Quickstart
1) Create config:
   ```bash
   cp config/cluster.env.example config/cluster.env
   # edit config/cluster.env
   ```

2) (Optional) If you are overwriting an old layout, remove legacy folders:
   ```bash
   ./scripts/prune_old_layout.sh
   ```

3) Bootstrap the cluster:
   ```bash
   ./scripts/bootstrap.sh
   ```

4) Install platform add-ons (optional, safe to re-run):
   ```bash
   ./scripts/platform.sh
   ```

5) Verify:
   ```bash
   export KUBECONFIG="$(pwd)/config/kubeconfig"
   kubectl get nodes -o wide
   ```

## What gets installed by default
- k3s server + agents (via `k3sup`)
- Nothing else unless you enable it in `config/cluster.env` (see `platform.sh`)

## Common workflows
- Re-run bootstrap after adding a new worker IP to `WORKER_IPS`.
- Put your app manifests in `k8s/` (or install via Helm in `scripts/platform.sh`).

## Notes
- `config/kubeconfig` is intentionally gitignored (cluster-admin credentials).
- For bare-metal LoadBalancer IPs, enable MetalLB (see `config/cluster.env.example`).

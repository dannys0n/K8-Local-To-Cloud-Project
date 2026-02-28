# k3s slim (Linux server + Pi worker)

Minimal automation to build a small k3s cluster with `k3sup`.

## What this does
- Sets up SSH key access to server and worker.
- Sets up passwordless sudo for those SSH users (if needed).
- Installs k3s server and joins worker.
- Writes kubeconfig to `config/kubeconfig`.
- Automatically symlinks `~/.kube/config` to that file (backs up existing non-symlink config first).

## Prereqs
- Run from your Linux control machine.
- Node IPs reachable via SSH.
- `kubectl`, `helm`, and `ssh` tools installed locally.

## 1) Configure `.env`
```bash
cp .env.example .env
```

Edit `.env`:
- `SERVER_IP`
- `SERVER_SSH_USER`
- `PI_WORKER_IP`
- `WORKER_SSH_USER` (SSH username on the worker; this is not hostname)
- `SSH_KEY` (default is usually fine)
- `K3SUP_USE_SUDO=true` (default)

## 2) Run cluster setup
```bash
make ssh-setup
make sudo-setup
make linux-pi
```

## 3) Verify
```bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

`kubectl` should work directly because `make linux-pi` links `~/.kube/config` for you.

## Optional add-ons
```bash
make platform
```

This uses `config/cluster.env` flags for metrics-server / MetalLB / ingress / cert-manager.

## Make targets
- `make ssh-setup`: create SSH key (if missing) and copy to server + worker.
- `make sudo-setup`: enable passwordless sudo for SSH users on server + worker.
- `make linux-pi`: install k3s server and join worker.
- `make nodes`: quick `kubectl get nodes -o wide` using project kubeconfig.
- `make platform`: install optional platform add-ons.

## Troubleshooting
- `Permission denied (publickey,password)`:
  run `make ssh-setup`.
- `sudo: Authentication failed`:
  run `make sudo-setup`, or use root SSH users and set `K3SUP_USE_SUDO=false`.
- `kubectl` tries `localhost:8080`:
  your kubeconfig is not active; rerun `make linux-pi` or check `~/.kube/config` symlink.

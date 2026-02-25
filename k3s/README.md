# k3s homelab cluster (server + Raspberry Pi OS agents)

This repo intentionally separates:

1) **Cluster lifecycle** (imperative): installing k3s server/agents and preparing hosts.
2) **Workloads** (declarative): Kubernetes YAML/Helm for Proxy/DBs/shards (put these under `k8s/`).

## Directory layout

- `config/` – environment/config examples
- `scripts/` – automation scripts
  - `scripts/lib/` – shared helpers
  - `scripts/cluster/server/` – run on server/master node
  - `scripts/cluster/agent/` – run on agent/worker node
  - `scripts/orchestrate/` – join/teardown workers over SSH
- `k8s/` – (placeholder) manifests/Helm for your apps
- `docs/` – reference docs (`docs/FILES.md`, `docs/K8S_PRIMITIVES.md`)

## Quickstart (manual)

### 0) Set a stable server DNS name

- Give the server a stable IP (DHCP reservation or static IP)
- Create a DNS A record (example): `k3s-api.lan -> <server-ip>`

### 1) Server

```bash
cp config/cluster.env.example config/cluster.env
# edit K3S_API_ENDPOINT

make server-setup
make server-up

sudo k3s kubectl get nodes -o wide
```

### 2) Agent/worker (on the Raspberry Pi)

```bash
make agent-setup
# reboot if setup says so
make agent-up
```

## Quickstart (recommended: join workers over SSH)

From the server (or any machine that can run `sudo k3s token create`):

```bash
cp config/cluster.env.example config/cluster.env
# edit K3S_API_ENDPOINT

scripts/orchestrate/join-agent-ssh.sh pi4-worker.lan pi5-worker.lan
```

## Notes on tokens

For first-time joins, prefer short-lived bootstrap tokens:

```bash
sudo k3s token create --ttl 30m
```

You can also use the server’s agent token:

```bash
sudo cat /var/lib/rancher/k3s/server/agent-token
```

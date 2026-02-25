# File map

This repo is split into **cluster provisioning** (imperative) and **Kubernetes workloads** (declarative).

## Top-level directories

- `config/`
  - `cluster.env.example`: example settings (server DNS name, token TTL, default SSH user)

- `scripts/`
  - `lib/`: shared bash helpers (logging, root writes, apt installs)
  - `cluster/server/`: run these on the **server/master** node
  - `cluster/agent/`: run these on an **agent/worker** node (Raspberry Pi OS supported)
  - `orchestrate/`: run these from the **server** (or admin machine) to manage workers over SSH

- `k8s/`
  - Reserved for Kubernetes manifests/Helm charts for your applications (Proxy, DBs, shards, etc.)

- `docs/`
  - Explanations and reference docs

## Cluster scripts

### Server (run on server node)

- `scripts/cluster/server/setup.sh`
  - Prepares the host for k3s server (sysctls, iptables prerequisites, etc.)

- `scripts/cluster/server/up.sh`
  - Installs/starts k3s server using `https://get.k3s.io`
  - Uses `K3S_API_ENDPOINT` (DNS name) for TLS SANs so agents can join by DNS

- `scripts/cluster/server/down.sh`
  - Stops k3s server (does not uninstall)

- `scripts/cluster/server/teardown.sh`
  - Uninstalls k3s server and removes lab config

### Agent/worker (run on worker node)

- `scripts/cluster/agent/setup.sh`
  - Prepares Raspberry Pi OS for k3s agent (cgroups, iptables tools, iptables backend selection)

- `scripts/cluster/agent/up.sh`
  - Installs/starts k3s agent and joins the server
  - Caches `K3S_URL` in `/etc/k3s-lab/agent.env`
  - Does **not** cache the join token unless `CACHE_JOIN_TOKEN=1`

- `scripts/cluster/agent/down.sh`
  - Stops k3s agent (does not uninstall)

- `scripts/cluster/agent/teardown.sh`
  - Uninstalls k3s agent and restores host iptables settings

## Orchestration scripts

- `scripts/orchestrate/join-agent-ssh.sh`
  - Creates a short-lived bootstrap token on the server (`k3s token create --ttl ...`)
  - Copies the `scripts/` bundle to the worker over SSH
  - Runs `agent/setup.sh` then `agent/up.sh` remotely

- `scripts/orchestrate/teardown-agent-ssh.sh`
  - Copies the `scripts/` bundle to the worker over SSH
  - Runs `agent/teardown.sh` remotely

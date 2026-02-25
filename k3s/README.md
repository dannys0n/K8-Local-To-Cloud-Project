# k3s local lab (Ubuntu server + Raspberry Pi worker) — no SSH required

This setup avoids SSH entirely by running commands **locally on each machine** via `make`.
It trades security for convenience: the server exposes the join token over plain HTTP on your LAN.

## Targets
Server (run on Ubuntu server):
- `make server-setup`
- `make server-up`
- `make server-down`
- `make server-teardown`

Worker (run on Raspberry Pi worker):
- `make worker-setup`
- `make worker-up`
- `make worker-down`
- `make worker-teardown`

## Default discovery
Workers try to find the server at: `k3s-server.local`
- Server setup installs mDNS (avahi) and sets the hostname to `k3s-server` so `k3s-server.local` resolves.
- If your LAN doesn't support mDNS, the worker will prompt once for the server IP and remember it.

## Insecure join token endpoint
Server serves an unauthenticated token at:
- `http://k3s-server.local:8088/agent-token`

LAN only. Do not expose to the internet.

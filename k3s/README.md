# k3s local lab (Linux laptop server + Raspberry Pi worker)

This repo keeps k3s setup simple and follows the official install/join flow from `docs.k3s.io`.

Official docs:
- https://docs.k3s.io/quick-start
- https://docs.k3s.io/installation/requirements
- https://docs.k3s.io/cluster-access
- https://docs.k3s.io/installation/uninstall

## 1) On the Linux laptop (server node)
Run:

```bash
make server-setup
```

Then get the worker join token:

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

## 2) On the Raspberry Pi (worker node)
Run:

```bash
make worker-setup
make worker-up
```

If `make worker-setup` says cgroup args were added, reboot the Pi first:

```bash
sudo reboot
```

After reboot, run:

```bash
make worker-up
```

`make worker-up` will ask for:
- server hostname or IP (for example `192.168.1.10`)
- token from the server command above

It saves this to `/etc/k3s-lab/worker.env` so future `make worker-up` runs do not prompt again.

## 3) Verify from the server

```bash
sudo k3s kubectl get nodes -o wide
```

You should see both the laptop and Raspberry Pi.

## Lifecycle commands
Server (run on laptop):
- `make server-setup`
- `make server-up`
- `make server-down`
- `make server-teardown`

Worker (run on Raspberry Pi):
- `make worker-setup`
- `make worker-up`
- `make worker-down`
- `make worker-teardown`

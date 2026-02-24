# k3d + Headlamp + Prometheus/Grafana (Demo)

Minimal demo project to spin up:
- k3d cluster (k3s-in-docker)
- Headlamp (Kubernetes web UI dashboard)
- kube-prometheus-stack (Prometheus + Grafana)

This uses **Helm** to install third-party apps and **kubectl** to inspect/operate the cluster.

## Prereqs
- docker
- k3d
- kubectl
- helm

## 1) Create cluster + install apps
```bash
./scripts/install.sh
```

## 2) Access UIs (simple, no Ingress)
Run these in separate terminals:

Grafana:
```bash
kubectl -n monitoring port-forward svc/kps-grafana 3000:80
```
Open: http://localhost:3000
Login: admin / admin

Headlamp:
```bash
kubectl -n kube-system port-forward svc/headlamp 8081:80
```
Open: http://localhost:8081

### Headlamp login note
Headlamp typically expects a **token** (recommended: a ServiceAccount token) or client-certificate for authentication.
For local dev, you can create a short-lived token when you need it:
```bash
kubectl -n kube-system create token headlamp-admin
```

## 3) Optional: access via Ingress on http://*.localtest.me:8080
If you created the cluster with port mapping (default in install.sh), you can apply:
```bash
kubectl apply -f manifests/ingress.yaml
```

Then:
- http://grafana.localtest.me:8080
- http://headlamp.localtest.me:8080

## 4) Uninstall (keeps Docker images)
```bash
./scripts/uninstall.sh
```

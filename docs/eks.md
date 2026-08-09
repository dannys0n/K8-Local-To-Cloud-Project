# EKS overlay

The local lab is fully runnable. The EKS overlay assumes an existing EKS cluster with:

- AWS Load Balancer Controller installed.
- Worker capacity across multiple Availability Zones.
- The server and gateway images pushed to ECR.
- A managed Valkey-compatible cluster-mode endpoint available to the cluster.
- The repository's minimal Prometheus deployment installed.
- A `tcp-server-valkey` Secret containing comma-separated seed `addresses`.

Before applying:

1. Replace the example ECR repository in `deploy/overlays/eks/kustomization.yaml`.
2. Build and push `app/server`, `app/gateway`, and `infra/autoscaler` to their
   ECR repositories.
3. Create the Valkey Secret from your AWS-integrated secret workflow; do not
   copy the kind development credentials.
4. Confirm the NLB annotations match your controller version and security requirements.
5. Apply with `kubectl apply -k deploy/overlays/eks`.
6. Read the external endpoint with `kubectl get service gateway -n tcp-lab`.

The resulting Secret contract is:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tcp-server-valkey
  namespace: tcp-lab
stringData:
  addresses: valkey-seed.example.internal:6379
  username: application-user
  password: replace-through-your-secret-workflow
  tls: "true"
```

`username`, `password`, and `tls` are optional so the same base manifests work
with the unauthenticated kind cluster. Rolling the server and server-autoscaler
Deployments reloads rotated credentials.

Install the pinned autoscaling prerequisites before applying the overlay:

```bash
kubectl apply -f deploy/base/namespace.yaml
kubectl apply -k infra/autoscaler/prometheus
kubectl rollout status deployment/prometheus -n tcp-lab --timeout=180s
```

Prometheus reads the kubelet's compact resource metrics through the Kubernetes node proxy and is
not exposed outside the cluster. The EKS overlay maps `tcp-server-autoscaler` to its own ECR
repository alongside the server and gateway images.
The two scaler processes are ordinary one-replica Deployments, so a ReplicaSet
can replace them on another worker after node loss without fixed pod-name conflicts.

The base CPU query uses a 30-second rate window because the normal kubelet
cAdvisor housekeeping interval is ten seconds. Faster CPU decisions require the
EKS worker bootstrap configuration to set kubelet
`--housekeeping-interval=1s`; lowering only the Prometheus scrape or autoscaler
interval cannot produce fresher CPU counters.

The stats Service remains internal on EKS. Access it temporarily with:

```bash
kubectl port-forward -n tcp-lab service/gateway-stats 8404:8404
```

Then open `http://127.0.0.1:8404/`.

The NLB Service publishes port `9000`. Location-aware clients begin with the
small lab `@location` handshake. The gateway resolves it to the active location
owner and can change downstream servers while preserving the client connection.
Valkey leases assign each identity to one server pod and fence stale owners
with a monotonically increasing generation.

The NLB registers gateway pod IPs directly and performs TCP health checks on the
traffic port every five seconds, requiring two successes or failures to change
target health. Kubernetes uses separate one-second HTTP checks against each
gateway's local health endpoint for faster in-cluster readiness and liveness
detection. Neither health mechanism changes server ownership.

All worker nodes are general capacity. Zone and hostname spreading use a maximum
skew of one and honor failed-node taints. Cold replacement pods may consolidate
across the remaining eligible workers after a failure.

The workload startup probes protect up to 90 seconds of initialization before
liveness checks can restart a container. The zero-second `NotReady` and
`Unreachable` tolerations evict application pods as soon as the EKS control
plane marks a node unhealthy. For infrastructure repair, use EKS managed node groups with node auto
repair enabled and install the EKS node monitoring agent; keep enough existing
worker capacity for replacement pods because launching a new EC2 node is not a
realtime recovery path.

## Valkey availability

Server pods are diskless. Valkey is authoritative for location and entity
recovery state. The EKS overlay does not deploy it. Use a managed, private,
Multi-AZ cluster-mode service with persistence, backups, TLS, credential
rotation, and appropriate network policies.

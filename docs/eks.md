# EKS overlay

The local lab is fully runnable. The EKS overlay assumes an existing EKS cluster with:

- AWS Load Balancer Controller installed.
- Worker capacity across multiple Availability Zones.
- The server image pushed to ECR.
- Managed PostgreSQL and Redis-compatible endpoints available to the cluster.
- A `tcp-server-databases` Secret containing `postgres-dsn` and `redis-addr`.

Before applying:

1. Replace the example ECR repository in `deploy/overlays/eks/kustomization.yaml`.
2. Build and push `app/server` to that ECR repository.
3. Create the database Secret from your AWS-integrated secret workflow; do not
   copy the kind development credentials.
4. Confirm the NLB annotations match your controller version and security requirements.
5. Apply with `kubectl apply -k deploy/overlays/eks`.
6. Read the external endpoint with `kubectl get service haproxy -n tcp-lab`.

The stats Service remains internal on EKS. Access it temporarily with:

```bash
kubectl port-forward -n tcp-lab service/haproxy-stats 8404:8404
```

Then open `http://127.0.0.1:8404/stats`.

The NLB Service publishes port `9000`. Location-aware clients begin with the
small lab `@location` handshake, which HAProxy inspects to select a stable
StatefulSet identity. A larger or dynamic location catalog would require a real
routing layer rather than fixed HAProxy rules.

## Database availability

Server pods are diskless. PostgreSQL is authoritative for durable server state;
Redis is disposable and non-authoritative. The EKS overlay does not deploy
either database. Production should use managed, private, Multi-AZ services with
TLS, credential rotation, backups, and appropriate network policies.

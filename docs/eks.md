# EKS overlay

The local lab is fully runnable. The EKS overlay assumes an existing EKS cluster with:

- AWS Load Balancer Controller installed.
- EBS CSI driver installed.
- A `gp3` StorageClass.
- Worker capacity across multiple Availability Zones.
- The server image pushed to ECR.

Before applying:

1. Replace the example ECR repository in `deploy/overlays/eks/kustomization.yaml`.
2. Build and push `app/server` to that ECR repository.
3. Confirm the NLB annotations match your controller version and security requirements.
4. Apply with `kubectl apply -k deploy/overlays/eks`.
5. Read the external endpoint with `kubectl get service haproxy -n tcp-lab`.

The stats Service remains internal on EKS. Access it temporarily with:

```bash
kubectl port-forward -n tcp-lab service/haproxy-stats 8404:8404
```

Then open `http://127.0.0.1:8404/stats`.

## Storage limitation

Each server pod owns one `ReadWriteOnce` volume. EBS volumes are Availability-Zone scoped. This lab proves StatefulSet/PVC behavior, not cross-AZ replication of application data. A real application should place durable shared state in an appropriate replicated datastore.

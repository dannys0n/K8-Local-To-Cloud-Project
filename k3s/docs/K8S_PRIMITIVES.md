# Kubernetes primitives (quick reference)

## Workloads

- **Pod**
  - Smallest runnable unit (one or more containers that share network + volumes).
  - You almost never create Pods directly in production.

- **Deployment**
  - Runs a stateless app with N replicas and supports rolling updates.
  - Typical for APIs, web services, and anything that can be restarted anywhere.

- **StatefulSet**
  - Like Deployment, but gives stable identities (`pod-0`, `pod-1`) + stable storage per replica.
  - Typical for databases, queues, anything that needs stable disk + stable hostname.

- **DaemonSet**
  - Ensures exactly one copy of a Pod runs on every (or selected) node.
  - Typical for node-level agents: log shippers, CNI helpers, metrics agents.

- **Job**
  - Runs to completion (batch work).

- **CronJob**
  - Runs Jobs on a schedule.

## Networking

- **Service** (stable virtual IP + load-balancing to Pods)
  - **ClusterIP**: internal-only (default). Most services use this.
  - **NodePort**: exposes on every node’s IP at a high port (debug/dev).
  - **LoadBalancer**: asks for an external load balancer (cloud LB, or MetalLB in homelab).
  - **Headless Service** (`clusterIP: None`): no virtual IP; used with StatefulSets for stable DNS per pod.

- **Ingress**
  - HTTP/HTTPS routing (host/path-based) to Services.
  - For UDP, you typically use a `Service type: LoadBalancer` instead of Ingress.

## Config + secrets

- **ConfigMap**
  - Non-secret config (env vars, config files).

- **Secret**
  - Sensitive config (tokens, passwords). Base64-encoded, but treat as sensitive.
  - In real deployments: integrate with a secrets manager + sealed/encrypted secrets.

## Storage

- **PersistentVolume (PV)** / **PersistentVolumeClaim (PVC)**
  - PV: actual storage resource.
  - PVC: request for storage, mounted into Pods.

- **StorageClass**
  - Defines how dynamic PVs get provisioned (local-path, Longhorn, EBS, etc.).

## Cluster organization + security

- **Namespace**
  - Logical grouping/isolation.

- **RBAC** (Role/ClusterRole + RoleBinding/ClusterRoleBinding)
  - Controls who/what can do what.

## Scaling + reliability

- **HPA** (HorizontalPodAutoscaler)
  - Scales replica count based on CPU/memory/custom metrics.

- **PDB** (PodDisruptionBudget)
  - Limits voluntary disruptions so updates/maintenance don’t take down too many replicas.

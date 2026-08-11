# EKS lab

The EKS path uses the same Kubernetes base as kind. Terraform owns AWS
infrastructure; Kustomize owns workloads. The default is EKS Auto Mode so AWS
supplies elastic worker capacity and the Network Load Balancer without a
repository-managed node scaler or load-balancer controller.

## What Terraform creates

- A three-AZ VPC with public and private subnets.
- EKS Auto Mode with its general-purpose node pool.
- Immutable ECR repositories for the server, gateway, and autoscaler.
- A private, TLS-only ElastiCache Serverless Valkey cache.
- Security-group access from EKS workloads to Valkey.

One NAT gateway is the lab default to reduce cost. Set
`single_nat_gateway = false` for independent NAT gateways in every AZ.

Terraform does not install Kubernetes workloads. The scripts build an ignored
`.generated/eks` Kustomize overlay containing the ECR URLs, immutable image tag, and NLB
source ranges, then apply the normal EKS overlay.

## Prerequisites

- Terraform 1.8 or newer
- AWS CLI v2 with an authenticated profile or credential environment
- Docker with Buildx
- kubectl
- An AWS account allowed to create VPC, EKS, IAM, ECR, EC2, NLB, and
  ElastiCache resources

Copy the example variables and restrict public client access before creating
anything:

```powershell
Copy-Item infra/eks/terraform.tfvars.example infra/eks/terraform.tfvars
$env:AWS_PROFILE = "tcp-lab"
aws sso login
```

`terraform.tfvars` is ignored. The `nlb_source_cidrs` default is
`0.0.0.0/0` only so an explicit first experiment is possible; a personal `/32`
or controlled test range is safer.

## Manage from Windows

Create/update all AWS resources, configure kubectl, build and push immutable
Linux/AMD64 images, and apply the workloads:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action up
```

The combined command can take several minutes because EKS and ElastiCache are
managed AWS services. The steps can also be run independently:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action infra-up
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action push -ImageTag test-001
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action deploy -ImageTag test-001
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action status
```

`deploy` requires an already-pushed immutable tag. Reusing a tag is intentionally
rejected by ECR.

## Manage from Linux, macOS, or WSL

```bash
export AWS_PROFILE=tcp-lab
aws sso login
bash scripts/eks.sh up

# Or independently:
bash scripts/eks.sh infra-up
bash scripts/eks.sh push test-001
bash scripts/eks.sh deploy test-001
bash scripts/eks.sh status
```

Buildx publishes Linux/AMD64 and Linux/ARM64 images, so EKS Auto Mode can choose
either architecture and the same command works from Apple Silicon.

## Deployment behavior

The public NLB targets gateway pod IPs directly and exposes TCP port 9000. AWS
balances new connections; established TCP sessions remain on their existing
gateway. Gateway and server autoscalers continue to use the repository's small
Prometheus collector. When they create Pending pods, EKS Auto Mode supplies new
worker capacity.

The Valkey connection is generated from Terraform output and stored in the
namespace-scoped `tcp-server-valkey` Secret. Only its private endpoint and TLS
flag are stored; no cloud credentials are placed in Kubernetes.

Prometheus remains internal and ephemeral. Gateway statistics are never exposed
through the EKS NLB. Use port-forwarding for diagnostics:

```bash
kubectl port-forward -n tcp-lab service/gateway-stats 8404:8404
kubectl port-forward -n tcp-lab service/prometheus 9090:9090
```

## State and teardown

Local Terraform state is ignored, but it exists only on this workstation. For
anything beyond an initial disposable test, create a versioned encrypted S3
bucket and initialize with the supplied backend example:

```bash
terraform -chdir=infra/eks init -backend-config=backend.hcl -migrate-state
```

Destroying the environment removes billable resources:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/eks.ps1 -Action down
```

Review the Terraform destroy plan before confirming. ElastiCache Serverless is
authoritative for this lab, so destroying it removes the lab state after its
configured snapshot behavior.

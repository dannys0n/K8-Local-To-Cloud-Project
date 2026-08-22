# Infrastructure as Code TODO

This checklist records the next infrastructure-focused improvements after the
successful EKS workload and node-scaling test. It is intentionally deferred
while additional reliability tests are performed.

## Priority 1: Correct ownership and access boundaries

- [ ] Split `nlb_source_cidrs` into two independent settings:
  - `cluster_endpoint_public_access_cidrs` for the Kubernetes API.
  - `gateway_source_cidrs` for public TCP client access.
- [ ] Confirm Terraform remains the sole owner of AWS resources: VPC, EKS,
  ECR, ElastiCache, IAM, security groups, and Pod Identity.
- [ ] Confirm Kustomize remains the sole owner of Kubernetes workloads,
  Services, autoscalers, probes, disruption budgets, and observability.
- [ ] Keep lifecycle scripts limited to orchestration; do not duplicate
  infrastructure definitions in PowerShell or shell code.

## Priority 2: Validate infrastructure changes automatically

- [ ] Add Terraform checks to CI:

  ```text
  terraform fmt -check -recursive
  terraform init -backend=false
  terraform validate
  ```

- [ ] Continue rendering both kind and EKS Kustomize overlays in CI.
- [ ] Ensure `.terraform.lock.hcl` remains committed.
- [ ] Ensure state, plans, real `.tfvars`, generated overlays, credentials, and
  tool caches remain ignored.

## Priority 3: Make the Terraform workflow reviewable

- [ ] Add an explicit `plan` action to `scripts/eks.ps1` and `scripts/eks.sh`.
- [ ] Keep infrastructure creation, image publishing, workload deployment,
  status inspection, and teardown as distinct actions.
- [ ] Preserve immutable ECR image tags in EKS deployments.
- [ ] Keep deletion of the public Gateway Service before Terraform teardown so
  the AWS load balancer finalizer can complete cleanly.

## Priority 4: Finish the state-management decision

- [ ] Keep local Terraform state as the documented default while this remains
  a disposable, single-user lab.
- [ ] Finish the optional S3 backend workflow before shared or automated use:
  - bucket versioning;
  - server-side encryption;
  - `use_lockfile = true`;
  - partial backend configuration;
  - least-privilege state access;
  - no credentials committed to configuration.
- [ ] Remove `backend.hcl.example` if the optional remote-state workflow is not
  going to be supported.

## Priority 5: Security and reliability audit

- [ ] Review the public EKS API endpoint CIDRs independently of application
  client CIDRs.
- [ ] Review IAM and Pod Identity policies for least privilege.
- [ ] Review security groups and confirm Valkey accepts traffic only from the
  intended EKS workloads.
- [ ] Review workload security contexts without introducing app-specific
  frameworks.
- [ ] Decide whether EKS control-plane logging is worth its cost for this lab.
- [ ] Keep the single NAT gateway as an explicit cost-saving, single-AZ lab
  compromise; use per-AZ NAT only when testing that failure domain.

## Priority 6: Operational checks

- [ ] Add lightweight drift inspection where it provides actionable results.
- [ ] Verify teardown leaves no NLB, EKS, ElastiCache, NAT gateway, or ECR
  resources that continue incurring cost.
- [ ] Document which tests require real EKS because kind cannot reproduce VPC
  CNI, NLB, EC2 networking, ElastiCache, or worker provisioning behavior.

## Explicit non-goals

- Helm conversion
- Argo CD or another GitOps controller
- Crossplane or a custom Kubernetes operator
- Service mesh
- Repository-managed EKS node autoscaler while EKS Auto Mode is sufficient
- Production monitoring, authentication, or multi-environment platform code


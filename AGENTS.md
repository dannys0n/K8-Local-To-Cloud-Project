# Codex instructions

- Keep this repository an infrastructure lab, not the final distributed application.
- Preserve the simple path: client -> Kubernetes TCP Service -> generic Gateway -> replaceable server pool.
- Do not add a coordinator, service mesh, custom operator, gRPC, authentication framework, or proxy-to-proxy routing unless explicitly requested.
- Use Kustomize for first-party manifests. Do not convert the project to Helm unless packaging requirements appear.
- Keep the Go gateway dependency-free and both workloads replaceable.
- Do not add unit tests unless explicitly requested. Manual smoke checks and failure drills are allowed.
- Keep kind and EKS differences in overlays rather than duplicating the base resources.
- Never expose Gateway statistics publicly in the EKS overlay.

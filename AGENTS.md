# Codex instructions

- Keep this repository an infrastructure lab, not the final distributed application.
- Preserve the simple path: client -> Kubernetes TCP Service -> HAProxy -> StatefulSet server.
- Do not add a coordinator, Redis, PostgreSQL, service mesh, custom operator, gRPC, authentication framework, or proxy-to-proxy routing unless explicitly requested.
- Use Kustomize for first-party manifests. Do not convert the project to Helm unless packaging requirements appear.
- Keep the Go server dependency-free and replaceable.
- Do not add unit tests unless explicitly requested. Manual smoke checks and failure drills are allowed.
- Keep kind and EKS differences in overlays rather than duplicating the base resources.
- Never expose HAProxy statistics publicly in the EKS overlay.

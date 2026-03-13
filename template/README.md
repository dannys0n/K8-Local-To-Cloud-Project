# Kubernetes Template Tiers

This template is designed from the top down.

- `tier3/` is the full reusable base: cluster bootstrap plus cluster services and generic workload deployment.
- `tier2/` strips tier 3 down to multi-node cluster formation only.
- `tier1/` strips tier 2 down again to the bare minimum single-node bootstrap.

The intent is that a real project like AtlasNet could have started from `tier3/`, while `tier2/` and `tier1/` remain understandable reductions of the same shape.

Upstream references:
- `k3d`: https://k3d.io/stable/#quick-start
- `k3sup`: https://github.com/alexellis/k3sup

## Layout

- [tier3](/home/danny/Desktop/AtlasNet/k8s/template/tier3): full base template
- [tier2](/home/danny/Desktop/AtlasNet/k8s/template/tier2): multi-node stripped variant
- [tier1](/home/danny/Desktop/AtlasNet/k8s/template/tier1): minimal stripped variant
- [common](/home/danny/Desktop/AtlasNet/k8s/template/common): shared bootstrap and helper scripts

All tiers default to `~/.kube/config`.

## Quick Start

```bash
cd k8s/template/tier3 && cp .env.example .env && make render
cd k8s/template/tier2 && cp .env.example .env && make k3sup
cd k8s/template/tier1 && cp .env.example .env && make k3d
```

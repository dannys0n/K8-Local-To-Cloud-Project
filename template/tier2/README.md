# Tier 2

Tier 2 is tier 3 with the workload layer stripped away.

It keeps:
- configurable local or remote cluster bootstrap
- multi-node defaults

It removes:
- MetalLB installation
- Redis
- arbitrary deployables

Quick start:

```bash
cd k8s/template/tier2
cp .env.example .env
make k3d
make k3sup
```

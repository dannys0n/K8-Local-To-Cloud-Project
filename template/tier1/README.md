# Tier 1

Tier 1 is tier 2 stripped down again to the smallest useful bootstrap.

It keeps:
- local or remote cluster bootstrap

It removes:
- multi-node defaults
- workload deployment
- cluster services

Quick start:

```bash
cd k8s/template/tier1
cp .env.example .env
make k3d
make k3sup
```

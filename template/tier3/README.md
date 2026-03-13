# Tier 3

Tier 3 is the full base template.

It includes:
- configurable local or remote cluster bootstrap
- optional MetalLB installation
- optional Redis
- arbitrary user-defined deployables

This is the tier a project like AtlasNet would build on.

Quick start:

```bash
cd k8s/template/tier3
cp .env.example .env
cp examples/values.yaml values.local.yaml
make k3d
make k3sup
```

If the cluster already exists:

```bash
make deploy BASE_STACK_VALUES=./values.local.yaml
```

# Repository Architecture

## .

- .gitignore
- mkdocs.yml
- Makefile

## docs

- architecture.md
- getting-started.md
- index.md

## infra/databases

- postgres.yaml
- namespace.yaml
- redis.yaml

## infra/kind

- cluster.yaml

## infra/managing

- portainer-values.yaml

## infra/monitoring

- values.yaml

## infra/scripts

- cluster.sh
- port-forward.sh
- managers.sh
- stop-port-forward.sh
- monitors.sh
- teardown.sh
- ping-redis.sh
- ping-postgres.sh

## infra/test-service

- echo.yaml

## tools

- generate_docs.py

# Dependency Graph

```mermaid
graph LR
managers_sh["managers.sh"]
scripts_md["scripts.md"]
Path["Path"]
portainer_values_yaml["portainer-values.yaml"]
redis_yaml["redis.yaml"]
getting_started_md["getting-started.md"]
echo_yaml["echo.yaml"]
ping_postgres_sh["ping-postgres.sh"]
teardown_sh["teardown.sh"]
dependencies_md["dependencies.md"]
architecture_md["architecture.md"]
cluster_yaml["cluster.yaml"]
index_md["index.md"]
values_yaml["values.yaml"]
infrastructure_md["infrastructure.md"]
services_md["services.md"]
cluster_sh["cluster.sh"]
re["re"]
architecture_auto_md["architecture-auto.md"]
k8s_topology_md["k8s-topology.md"]
README_md["README.md"]
port_forward_sh["port-forward.sh"]
namespace_yaml["namespace.yaml"]
monitors_sh["monitors.sh"]
os["os"]
ping_redis_sh["ping-redis.sh"]
postgres_yaml["postgres.yaml"]
repository_map_md["repository-map.md"]
stop_port_forward_sh["stop-port-forward.sh"]
mkdocs_yml["mkdocs.yml"]
generate_docs_py["generate_docs.py"]
generate_docs_py --> Path
generate_docs_py --> os
generate_docs_py --> re
```
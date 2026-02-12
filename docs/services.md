# Service Interaction Map

```mermaid
graph LR
"port-forward.sh" --> "port-3000"
"port-forward.sh" --> "port-9090"
"port-forward.sh" --> "port-9000"
"port-forward.sh" --> "port-6379"
"port-forward.sh" --> "port-5432"
"ping-redis.sh" --> "port-6379"
"ping-postgres.sh" --> "port-5432"
```
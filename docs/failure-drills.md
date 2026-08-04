# Manual failure drills

These are infrastructure checks, not unit tests.

## Gateway pod failure

Keep `tools/client.ps1` or `tools/client.py` connected, then delete one gateway:

```bash
kubectl get pods -n tcp-lab -l app=gateway
kubectl delete pod -n tcp-lab <one-gateway-pod-name> --wait=false
```

An existing TCP connection through the deleted gateway will close. A new client
connection should work through a surviving gateway while Kubernetes creates a
replacement.

## Independent scaling

```bash
kubectl scale deployment/gateway -n tcp-lab --replicas=4
kubectl scale deployment/tcp-server -n tcp-lab --replicas=10
kubectl get pods -n tcp-lab -w
```

Every gateway discovers server endpoints through the headless Service DNS.

## Server persistence

1. Start `tools/client.ps1`, then click near New York on the browser map.
2. Press the durable counter button several times and note the numeric location ID, logical server, and client counter.
3. Find the physical owner and generation in PostgreSQL:

   ```bash
   kubectl exec -n tcp-lab postgres-0 -- psql -U tcp_lab -d tcp_lab -c "select * from tcp_server_assignment order by server_id"
   ```

4. Delete the owner pod while leaving the client open.
5. Move again. The gateway retains the client connection, discovers the
   replacement, and the new server restores the durable counter and last claim
   coordinate from PostgreSQL.
   Any unacknowledged counter operation retries with the same operation ID.
6. Confirm an existing spare owns `tcp-server-1`, the generation increased, and
   the PostgreSQL `client_state` counter continues from its previous value.

The server-side TCP socket cannot survive a pod failure, but the client-to-gateway
socket remains open. Counter commands have operation IDs. Movement since the
last server claim is transient and is intentionally not recovered after a
server failure.

The default ownership lease is 1.5 seconds and renews every 250ms. Gateways
discover eligible backends every 200ms, with a separate 500ms discovery timeout,
and re-resolve immediately after an error.
The expected application-level handoff is therefore a few seconds and does not
wait for Kubernetes to declare the worker `NotReady`. The generation change is
the safety boundary: database writes from the former owner no longer match the
authoritative assignment row.

## Worker failure

Find which worker hosts a gateway, then stop its kind node container:

```bash
docker ps --format '{{.Names}}'
docker stop tcp-lab-worker3
```

Observe which pods reschedule:

```bash
kubectl get pods -n tcp-lab -o wide -w
```

Server pods are diskless and can reschedule on another worker. Local kind still
uses a node-local PVC for PostgreSQL, so losing the PostgreSQL worker can leave
the database unavailable until that worker returns.

For an abrupt hardware-style failure, use `docker kill` instead of draining the
node. Fresh kind clusters use one-second kubelet status updates and a five-second
controller grace period. Application pods have zero additional tolerance for
`NotReady` or `Unreachable`, so replacement begins as soon as the failed-node
taint is applied. The controller permits ten failed-node evictions per second,
including the small-cluster unhealthy-zone path, so simultaneous worker losses
are not serialized by Kubernetes' conservative default rate. That slower loop
replenishes the five-pod spare pool; it is not the location failover
mechanism. EKS node repair remains a background capacity mechanism.

Server and gateway startup probes allow up to 90 seconds for initialization and
prevent readiness or liveness checks from running until startup succeeds. This
does not delay healthy pods: the probe runs every second and completes as soon
as the listening endpoint is available. Runtime checks are intentionally much
faster after that boundary.

Kubernetes does not move healthy replacement pods when a repaired node returns.
After every general worker is `Ready`, restore the warm per-node distribution:

```bash
kubectl rollout restart deployment/tcp-server deployment/gateway -n tcp-lab
```

## Database restarts

Restart Redis and confirm its expiring presence keys repopulate:

```bash
kubectl delete pod -n tcp-lab -l app=redis
kubectl exec -n tcp-lab deployment/redis -- redis-cli --scan --pattern 'tcp-lab:*'
```

Restart PostgreSQL and confirm counters remain on its single kind PVC:

```bash
kubectl delete pod -n tcp-lab postgres-0
kubectl wait --for=condition=Ready pod/postgres-0 -n tcp-lab --timeout=180s
```

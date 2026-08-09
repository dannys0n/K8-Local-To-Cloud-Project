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

## Autoscaler status

```bash
kubectl get deployment/gateway-autoscaler deployment/tcp-server-autoscaler -n tcp-lab
kubectl get deployment/gateway deployment/tcp-server -n tcp-lab -w
```

Independent Prometheus-backed autoscalers own the gateway and server replica
counts. Every gateway discovers server endpoints through the headless Service DNS.

## Server persistence

1. Start `tools/client.ps1`, then click near New York on the browser map.
2. Note the numeric location ID, logical server, and client coordinate.
3. Find the physical owner and generation in Valkey:

   ```bash
   kubectl exec -n tcp-lab valkey-0 -- valkey-cli -c ZRANGE tcp-lab:locations 0 -1
   kubectl exec -n tcp-lab valkey-0 -- valkey-cli -c HGETALL 'tcp-lab:location:{<id>}'
   ```

4. Delete the owner pod while leaving the client open.
5. Move again. The gateway retains the client connection, discovers the
   replacement, and the new server restores the last claimed coordinate from
   Valkey.
6. Confirm a cold replacement owns the same logical server and its generation
   increased.

The server-side TCP socket cannot survive a pod failure, but the client-to-gateway
socket remains open. Movement since the last server claim is transient and is
intentionally not recovered after a server failure.

The default ownership lease is three seconds and renews every 500ms. Gateways
discover eligible backends every 200ms, with a separate 500ms discovery timeout,
and re-resolve immediately after an error.
The expected application-level handoff is therefore a few seconds and does not
wait for Kubernetes to declare the worker `NotReady`. The generation change is
the safety boundary: state operations from the former owner no longer match the
authoritative Valkey record.

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

Server pods are diskless and can reschedule on another worker. Valkey primaries
and replicas are spread across workers; losing one worker should promote a
surviving replica while the failed pod's local PVC remains attached to its node.

For an abrupt hardware-style failure, use `docker kill` instead of draining the
node. Fresh kind clusters use one-second kubelet status updates and a five-second
controller grace period. Application pods have zero additional tolerance for
`NotReady` or `Unreachable`, so replacement begins as soon as the failed-node
taint is applied. The controller permits ten failed-node evictions per second,
including the small-cluster unhealthy-zone path, so simultaneous worker losses
are not serialized by Kubernetes' conservative default rate. That slower loop
replenishes available server capacity; it is not the location failover
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

## Valkey failover

Inspect cluster ownership, delete one primary, and confirm its replica promotes:

```bash
kubectl exec -n tcp-lab valkey-0 -- valkey-cli cluster nodes
kubectl delete pod -n tcp-lab <one-primary-valkey-pod>
kubectl exec -n tcp-lab valkey-0 -- valkey-cli cluster info
```

The cluster should return to `cluster_state:ok`; location and entity keys remain
available, and clients reconnect through the normal gateway/server paths.

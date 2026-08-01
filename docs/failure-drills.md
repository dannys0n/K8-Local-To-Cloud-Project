# Manual failure drills

These are infrastructure checks, not unit tests.

## Proxy pod failure

Keep `tools/client.ps1` or `tools/client.py` connected, then delete one proxy:

```bash
kubectl get pods -n tcp-lab -l app=haproxy
kubectl delete pod -n tcp-lab <one-haproxy-pod-name> --wait=false
```

An existing TCP connection through the deleted proxy will close. A new client connection should work through the surviving proxy while Kubernetes creates a replacement.

## Independent scaling

```bash
kubectl scale deployment/haproxy -n tcp-lab --replicas=3
kubectl scale statefulset/tcp-server -n tcp-lab --replicas=5
kubectl get pods -n tcp-lab -w
```

HAProxy discovers new server endpoints through the headless Service DNS.

## Server persistence

1. Start a location client, for example `tools/client.ps1 -Location new-york`.
2. Send several messages and note `tcp-server-1`, `new-york`, and its counter.
3. Delete `tcp-server-1` while leaving the client open.
4. Send another message. The client retries while the endpoint is unavailable.
5. Wait for the StatefulSet to recreate the pod.
6. Confirm the client reconnects to `tcp-server-1` and its counter continues
   from the value stored on the existing PVC.

The original TCP socket cannot survive a pod failure. The lab minimizes the
visible interruption by reconnecting to the same stable logical endpoint. A
message in flight at disconnect can be processed more than once.

## Worker failure

Find which worker hosts a proxy, then stop its kind node container:

```bash
docker ps --format '{{.Names}}'
docker stop tcp-lab-worker2
```

Observe which pods reschedule:

```bash
kubectl get pods -n tcp-lab -o wide -w
```

Local kind volumes are node-local. A server whose PVC is tied to the stopped node may not move elsewhere. That limitation is expected in this local lab.

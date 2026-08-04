# TCP Kubernetes Infrastructure Lab

A small, replaceable application used to exercise Kubernetes infrastructure:

```text
client -> NodePort/NLB -> Gateway Deployment -> TCP Server pool
                                              -> PostgreSQL + Redis
```

It intentionally keeps ownership and fencing inside PostgreSQL instead of
adding a coordinator or operator.

## What runs

- **Gateway:** four generic Go replicas that preserve the client connection
  while switching downstream location servers at runtime.
- **TCP server:** ten interchangeable Deployment replicas: five active location
  owners and five ready hot spares. Each active process runs a 20 Hz
  authoritative simulation clock and includes its current tick in responses.
- **PostgreSQL:** authoritative location identity, counters, leases, and ownership
  generations; one PVC on the dedicated kind database worker.
- **Redis:** ephemeral server presence and 20 Hz entity visibility on the same
  kind database worker. It is not part of authoritative client state.
- **Client entry:** `127.0.0.1:9000`; the browser map keeps one TCP connection
  through its local bridge. The EKS overlay uses an AWS NLB.
- **Manifest management:** a shared Kustomize base plus kind and EKS overlays.

## Prerequisites

Install and make available on `PATH`:

- Docker Desktop or Docker Engine
- kind
- kubectl

- Python 3 for the local browser client and optional dashboard
- Internet access for Leaflet and OpenStreetMap tiles in the browser

## Run on Windows PowerShell

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/up.ps1
powershell -ExecutionPolicy Bypass -File tools/smoke.ps1
powershell -ExecutionPolicy Bypass -File tools/client.ps1
```

The lab initially creates five numeric locations. On a new database, the server
application chooses each location's latitude and longitude once; PostgreSQL then
preserves the numeric ID and coordinates across pod replacement and cluster
restarts. Existing databases retain their coordinates during migration.

Names are intentionally not part of location identity or routing. Clients display
`Location <id>` on the Leaflet map. PostgreSQL coordinates are canonical Leaflet
`LatLng` values and can later be indexed with Redis GEO; Leaflet's Web Mercator
projection remains a browser display detail and is not persisted.

PostgreSQL exposes two operations for the future location-management interface:

```sql
-- Create a location at a random coordinate.
SELECT * FROM tcp_create_location();

-- Or create one at an explicit Leaflet coordinate.
SELECT * FROM tcp_create_location(35.0, -120.0);

-- Disable a location without reusing its identity.
SELECT tcp_delete_location(6);
```

Deletion is intentionally a soft delete so historical entity and transaction
records keep a valid location reference. Servers reload enabled locations once per
second. Disabling a location atomically fences its assignment; its former server
drops the in-memory claim on refresh and gateways remove the route through normal
discovery. The PostgreSQL sequence allocates IDs atomically across concurrent
callers. Location IDs are permanent identities, not list indexes: they are never
renumbered, compacted, or reused. Deletion can therefore leave gaps, and sequence
values can also be skipped by rolled-back creation attempts.

`tools/client.py` asks the operating system for a free local port, prints the
resulting URL, and opens it in the default browser. It keeps a stable client UID
and unacknowledged counter operations in browser local storage. Map clicks are
sequenced transient teleport intents applied by the authoritative server tick.
The separate counter button durably increments the per-client counter;
PostgreSQL records its idempotency key and committed result. The gateway routes
teleports to the nearest active server without replacing the client
connection. Run the command again for each
additional independent client; every process receives its own available port.
Use `--listen-port 8082` only when a fixed port is useful.

The client sidebar can spawn dummy clients in batches of up to 500. Every bot
uses its own gateway TCP connection, chooses a random normalized movement
direction every three seconds, sends movement heartbeats at 20 Hz, and performs
one idempotent durable counter increment per second. Batches can be despawned
individually or together. Their cyan pins disappear from visibility after one
second, and abandoned in-memory server entities are removed after 30 seconds.
When routing is unavailable, bots pause application commands and retry their
existing reconnect/resume path with 0.75–1.25 seconds of per-bot jitter. The UI
reports ready, gateway-only, and disconnected bots separately instead of treating
every allocated bot thread as connected.

The internal `@location` handshake remains available to smoke checks. Browser
clients use `@teleport CLIENT_UID SEQUENCE LATITUDE LONGITUDE` and
`@increment CLIENT_UID OPERATION_ID`; `@locations`
returns sanitized active-server markers. Servers batch pending counter commands
on their 20 Hz tick and acknowledge them only after a synchronous PostgreSQL
commit. Retrying an operation ID returns its recorded counter without applying
it twice.

WASD sends transient `@input CLIENT_UID SEQUENCE X Y` intents at up to 40 Hz.
The browser never sends a position for movement: the current logical server
keeps only the newest sequence, normalizes diagonal input, and advances at 40
projected degrees per second on its authoritative tick. Longitude wraps at the
date line. Latitude moves in Leaflet's Web Mercator space and wraps between its
north and south limits, keeping apparent map speed consistent. Input stops automatically if no
refresh arrives for eight ticks (400 ms). Movement is not written on each tick.
PostgreSQL records the last server-claim coordinate as a recovery point and
stores the durable counter independently.

Active servers publish one best-effort visibility snapshot per tick and pull
the combined snapshots into a local cache. Input responses include that cache,
so the map renders other clients as cyan pins. Newer input sequences fence stale
copies left behind by direct teleports. Idle clients send zero-axis heartbeats
at 20 Hz, and entities disappear after one second without input. Redis failure
only hides those markers; it cannot affect movement, ownership, or durable state.

After every movement tick, the server checks the resulting coordinate against
the geographic locations. When ownership changes, it returns a transient
handoff snapshot; the gateway resumes that snapshot on the destination server
before switching its downstream socket. The browser-to-gateway connection does
not change. A completed handoff records one entity claim in PostgreSQL.

After a gateway disconnect, the local bridge uses its last coordinate as a
routing hint. The destination server restores the durable counter from
PostgreSQL, while the browser retries any unacknowledged counter operation with
the same operation ID.
The logical server identity, durable counter, and last entity claim remain in
PostgreSQL when a pod is replaced. Movement after that claim remains transient.
An expired 1.5-second lease is claimed by an already-running spare;
the generation increases to fence the old owner. Redis presence keys expire and
repopulate automatically. Generic test messages remain at-least-once, while
counter increments have exactly-once database effects. Without a location handshake, port 9000 remains the
original round-robin endpoint.

Use the map's reconnect button to replace the gateway connection while retaining
the selected coordinate. Use the aggregate dashboard to see the gateway and
backend independently.

Open the selected gateway replica's live page:

```text
http://127.0.0.1:8404/
```

Open the local aggregate connection dashboard (requires Python and a running
`kubectl` context):

```powershell
python tools/dashboard.py
```

Then visit `http://127.0.0.1:8080`. This combines every gateway pod and lists
active client sessions, routes, generations, and backend instances.

Inspect the cluster:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/status.ps1
```

Run the scheduling regression tests:

```bash
python -m unittest discover -s tests -v
```

Delete everything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/down.ps1
```

## Run on Linux, macOS, or WSL

```bash
./scripts/up.sh
python3 tools/smoke.py
python3 tools/client.py
```

Delete everything:

```bash
./scripts/down.sh
```

## Expected response

Send a line such as `hello` and receive one JSON line:

```json
{"gateway":"gateway-abc","server":"tcp-server-0","location_id":1,"latitude":34.0522,"longitude":-118.2437,"client_uid":"a-client-uuid","operation_id":"an-operation-uuid","client_latitude":34.1,"client_longitude":-118.2,"instance":"tcp-server-abc","generation":3,"counter":1,"message":"increment","tick":42,"time":"2026-07-31T00:00:00Z"}
```

A single browser-client process keeps one persistent TCP connection on one
gateway pod, while map clicks can change its selected backend server.

## Useful commands

```bash
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
kubectl logs -n tcp-lab deployment/gateway
kubectl logs -n tcp-lab deployment/tcp-server
kubectl scale deployment/gateway -n tcp-lab --replicas=4
kubectl scale deployment/tcp-server -n tcp-lab --replicas=10
```

See:

- `docs/failure-drills.md`
- `docs/visualization.md`
- `docs/eks.md`

## Design boundaries

This is an infrastructure template. The small gateway, tiny server, and local
database deployments are placeholders. The reusable pieces are the Services,
Deployments, probes, disruption budgets, topology rules, and kind/EKS overlays.
The credentials in the kind overlay are development-only.

Kind labels `tcp-lab-worker` (the first worker) as its database worker.
PostgreSQL and Redis use
hard affinity and tolerate its `NoSchedule` taint; gateways and servers cannot
schedule there. The kind PostgreSQL PVC is node-local and cannot follow its pod
to another node without a shared storage class. The EKS overlay deploys no
database pods and expects managed PostgreSQL and Redis-compatible services
outside the worker pool.
Changing the dedicated kind database worker requires recreating the cluster;
the startup scripts reject an in-place move that would strand the local PVC.

Kubernetes restores failed pods and nodes, but location recovery does not wait
for node eviction. Ready spare pods poll PostgreSQL-backed leases every 250ms;
after a 1.5-second lease expires, one spare atomically claims the location and
increments its fencing generation. These lab defaults are configurable through
`ASSIGNMENT_LEASE_DURATION` and `ASSIGNMENT_RENEW_INTERVAL`; production values
must be validated against database and network latency. The replacement
application remains responsible for resumable sessions and application-specific
durability semantics.

Startup probes give server and gateway containers up to 90 seconds to initialize
without liveness restarts. Once startup succeeds, their readiness and liveness
checks switch to aggressive runtime detection. Fresh kind clusters report node
status every second, allow five seconds without a heartbeat, and immediately
evict these application pods once the failed-node taint appears. Kind also
raises both node-eviction rates so simultaneous worker losses are processed
together instead of at the conservative default rate.

Gateway health is independent from server ownership. Kubernetes readiness and
liveness checks remove or restart an unhealthy gateway, and the EKS NLB checks
gateway targets directly. Gateways discover every active location owner and do
not claim, rebalance, or exclusively own servers. Hard hostname spreading keeps
the ten server pods and four gateways distributed across kind's four general
workers; spread counts the current rollout revision, and failed-node taints are
honored so replacement pods can consolidate on survivors. The five ready server
spares provide immediate location handoff, while Kubernetes promptly creates
replacement pods to replenish that pool.
Kubernetes does not automatically rebalance healthy pods when repaired workers
return; the failure drill documents the explicit rolling rebalance command.

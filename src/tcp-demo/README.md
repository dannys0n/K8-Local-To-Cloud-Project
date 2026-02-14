# tcp-demo: 4-player chatroom

Stateful TCP chatroom with a web UI. Up to 4 people per room. **Chat messages are sent to and displayed for the entire session** — everyone in the same room sees every message (including the sender).

## Flow

1. **Lobby** (`/`): Set your name → create a room or join with a room ID.
2. **Room** (`/room?room=...&name=...`): Session page. Type and send; messages appear for everyone in that room. Leave when done.

## Run

```bash
make up          # once: cluster + Redis + port-forward
make tcp-demo    # build, deploy, port-forward
```

Open **http://localhost:8081** in a browser. Create or join a room; messages in the room are broadcast to the whole session and show for all participants.

## Raw TCP (optional)

```bash
nc localhost 7654
CREATE_ROOM
# → ROOM abc12def
JOIN abc12def alice
# type to chat (everyone in room sees it); LEAVE to leave
```

## Databases: what gets stored where

**tcp-demo uses only Redis** (no Postgres).

| Where | What | Purpose |
|-------|------|--------|
| **Redis** | Key `chat:room:<room_id>`, value `{ "pod_id": "<pod>", "players": ["alice", "bob", ...] }`, with TTL (e.g. 1h) | Which pod owns the room (for REDIRECT), who’s in the room (max 4, no duplicate names). Created on CREATE_ROOM; updated on JOIN/LEAVE. |
| **In-memory (per pod)** | `_room_members`, `_conn_room`: which connections are in which room | Used only for broadcast (sending chat to the right clients). Not persisted; lost when the pod restarts. |

Chat message text is **not** stored anywhere — it’s sent to connected clients and then discarded. Room metadata (who’s in the room, which pod owns it) lives in Redis so multiple pods can coordinate; the actual conversation is in-memory only.

**In this repo:** Redis and Postgres run in the `databases` namespace (`make up`). **http-demo** uses both (Redis for key/value API, Postgres for a connectivity check). **tcp-demo** uses only Redis, as above.

## How it works

- **Sessions**: One room = one session. Messages are broadcast to every connection in that room (same pod, in-memory).
- **Replicas**: Multiple pods (default 3); each pod hosts many rooms. Optional HPA: `kubectl apply -f src/tcp-demo/hpa.yaml`.
- **Load balancing**: The Service distributes new connections across pods. With one port-forward, your traffic goes to one pod.

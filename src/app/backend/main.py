from collections import deque
from typing import Dict, List
import json
import logging
import os
import traceback
import time
import uuid

import psycopg2
import redis
from fastapi import FastAPI, HTTPException
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = FastAPI(title="Game Backend", version="0.1.0")


class MatchRequest(BaseModel):
    player_id: str


class MatchResponse(BaseModel):
    session_id: str
    players: List[str]
    connect_host: str = ""   # Set when matched; client uses this to connect to game server
    connect_port: int = 0   # NodePort when matched


class StartMatchRequest(BaseModel):
    session_id: str


class StartMatchResponse(BaseModel):
    session_id: str
    game_server_pod: str
    status: str
    connect_host: str = ""  # IP/host for clients to connect to
    connect_port: int = 0   # Port (e.g. NodePort)


# Queue moved to Redis for multi-pod support
_session_size = 12  # 6v6 sessions

_db_conn = None
_k8s_api = None
_redis_client = None


def _get_db_conn():
    global _db_conn
    if _db_conn is None:
        dsn = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@postgres.databases.svc.cluster.local:5432/app",
        )
        _db_conn = psycopg2.connect(dsn)
        _db_conn.autocommit = True
        _ensure_schema(_db_conn)
    return _db_conn


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        redis_host = os.getenv("REDIS_HOST", "redis.databases.svc.cluster.local")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        try:
            _redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            # Test connection
            _redis_client.ping()
            logger.info(f"Connected to Redis at {redis_host}:{redis_port}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis connection failed: {e}, continuing without Redis")
            _redis_client = None
    return _redis_client


def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        # Create matches table if it doesn't exist
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
              session_id text PRIMARY KEY,
              players_json text NOT NULL,
              backend_pod text,
              created_at timestamptz DEFAULT now()
            );
            """
        )
        # Add missing columns if they don't exist (for existing tables)
        # Check if column exists by querying information_schema
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='matches' AND column_name='game_server_pod';
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE matches ADD COLUMN game_server_pod text;")
        
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='matches' AND column_name='ended_at';
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE matches ADD COLUMN ended_at timestamptz;")
        
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS match_events (
              id bigserial PRIMARY KEY,
              session_id text,
              event_type text,
              player_id text,
              created_at timestamptz DEFAULT now()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS game_server_stats (
              session_id text PRIMARY KEY,
              state text NOT NULL,
              client_count int NOT NULL DEFAULT 0,
              ttl_seconds int NOT NULL DEFAULT 0,
              updated_at timestamptz DEFAULT now()
            );
            """
        )


def _get_k8s_api():
    global _k8s_api
    if _k8s_api is None:
        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001
            config.load_kube_config()
        _k8s_api = client.AppsV1Api()
    return _k8s_api


def _get_core_v1_api():
    """CoreV1Api for Services and Nodes."""
    if not hasattr(_get_core_v1_api, "_api"):
        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001
            config.load_kube_config()
        _get_core_v1_api._api = client.CoreV1Api()
    return _get_core_v1_api._api


def _wait_for_game_server_ready(session_id: str, timeout_seconds: float = 45.0) -> bool:
    """
    Wait until at least one game-server pod for this session is Running and Ready.
    This avoids handing out connect info before TCP is actually accepting.
    """
    core_api = _get_core_v1_api()
    namespace = os.getenv("NAMESPACE", "default")
    label_selector = f"app=game-server,session_id={session_id}"
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            pods = core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
            for pod in pods.items:
                status = pod.status
                if status is None or status.phase != "Running":
                    continue
                ready = False
                for cs in status.container_statuses or []:
                    if cs.ready:
                        ready = True
                        break
                if ready:
                    return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error while waiting for game server readiness ({session_id}): {e}")
        time.sleep(0.5)

    return False


def _create_game_server_pod(session_id: str, players: List[str]) -> tuple[str, str, int]:
    """Create a game server pod for a session."""
    k8s_apps_api = _get_k8s_api()
    
    pod_name = f"game-server-{session_id[:8]}"
    namespace = os.getenv("NAMESPACE", "default")
    
    logger.info(f"Creating game server pod {pod_name} in namespace {namespace}")
    
    # Create Deployment (simpler than Pod for lifecycle management)
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels={"app": "game-server", "session_id": session_id},
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": "game-server", "session_id": session_id}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "game-server", "session_id": session_id}
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="game-server",
                            image="game-server:local",
                            image_pull_policy="IfNotPresent",
                            ports=[client.V1ContainerPort(container_port=8080)],
                            env=[
                                client.V1EnvVar(name="SESSION_ID", value=session_id),
                                client.V1EnvVar(
                                    name="PLAYERS",
                                    value=json.dumps(players),
                                ),
                                client.V1EnvVar(name="PORT", value="8080"),
                                client.V1EnvVar(
                                    name="DATABASE_URL",
                                    value=os.getenv(
                                        "DATABASE_URL",
                                        "postgresql://postgres:postgres@postgres.databases.svc.cluster.local:5432/app",
                                    ),
                                ),
                            ],
                        )
                    ],
                    restart_policy="Always",  # Deployments require Always, not Never
                ),
            ),
        ),
    )
    
    try:
        k8s_apps_api.create_namespaced_deployment(namespace=namespace, body=deployment)
        logger.info(f"Successfully created deployment {pod_name}")
    except ApiException as e:
        logger.error(f"Failed to create deployment: status={e.status}, reason={e.reason}, body={e.body}")
        if e.status == 409:  # Already exists
            logger.info(f"Deployment {pod_name} already exists")
        else:
            raise

    # Create a NodePort Service so clients can connect to this game server
    core_api = _get_core_v1_api()
    svc_name = pod_name  # same name as deployment
    svc = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=svc_name,
            namespace=namespace,
            labels={"app": "game-server", "session_id": session_id},
        ),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"app": "game-server", "session_id": session_id},
            ports=[client.V1ServicePort(port=8080, target_port=8080, protocol="TCP")],
        ),
    )
    try:
        core_api.create_namespaced_service(namespace=namespace, body=svc)
        logger.info(f"Created Service {svc_name} (NodePort)")
    except ApiException as e:
        if e.status == 409:
            pass  # already exists
        else:
            logger.warning(f"Failed to create Service for {pod_name}: {e}")
            return pod_name, "", 0

    # Read back the assigned NodePort and a node IP
    import time
    time.sleep(0.5)  # give API a moment
    try:
        created = core_api.read_namespaced_service(name=svc_name, namespace=namespace)
        node_port = created.spec.ports[0].node_port if created.spec.ports else 0
    except Exception:  # noqa: BLE001
        node_port = 0

    # Prefer node IP from env (e.g. kind: use host). Else use first node's address.
    connect_host = os.getenv("GAME_SERVER_CONNECT_HOST", "")
    if not connect_host:
        try:
            nodes = core_api.list_node()
            for node in nodes.items:
                for addr in node.status.addresses or []:
                    if addr.type in ("ExternalIP", "InternalIP"):
                        connect_host = addr.address
                        break
                if connect_host:
                    break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to auto-detect node IP for game server connect_host: {e}")
    if not connect_host:
        connect_host = "localhost"  # fallback for port-forward setups
        logger.warning(
            "Falling back to localhost for game-server connect_host; this may be unreachable for NodePort clients"
        )

    # Wait for pod readiness before giving clients connect details.
    if not _wait_for_game_server_ready(session_id, timeout_seconds=45.0):
        logger.warning(
            f"Game server {pod_name} not ready within timeout; clients may need to retry connect"
        )

    return pod_name, connect_host, node_port or 0


def _delete_game_server_pod(session_id: str) -> None:
    """Delete the game server deployment and its Service for a session with retries."""
    k8s_apps_api = _get_k8s_api()
    core_api = _get_core_v1_api()
    namespace = os.getenv("NAMESPACE", "default")
    pod_name = f"game-server-{session_id[:8]}"

    # Delete Service first (no need to retry much)
    try:
        core_api.delete_namespaced_service(name=pod_name, namespace=namespace)
        logger.info(f"Deleted Service {pod_name}")
    except ApiException as e:
        if e.status != 404:
            logger.warning(f"Failed to delete Service {pod_name}: {e}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            k8s_apps_api.delete_namespaced_deployment(
                name=pod_name,
                namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )
            logger.info(f"Successfully deleted game server pod {pod_name}")
            return
        except ApiException as e:
            if e.status == 404:  # Already deleted, that's fine
                logger.info(f"Game server pod {pod_name} already deleted")
                return
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                logger.warning(f"Failed to delete {pod_name} (attempt {attempt + 1}/{max_retries}): {e}, retrying in {wait_time}s")
                import time
                time.sleep(wait_time)
            else:
                logger.error(f"Failed to delete {pod_name} after {max_retries} attempts: {e}")
                raise


@app.get("/health")
def health() -> dict:
    # touch DB so /health reflects DB availability
    try:
        conn = _get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception:  # noqa: BLE001
        return {"status": "degraded"}
    return {"status": "ok"}


@app.post("/match/join", response_model=MatchResponse)
def join_match(req: MatchRequest) -> MatchResponse:
    """
    Simple 6v6 matchmaking using Redis queue (shared across pods):
    - enqueue player in Redis
    - when we have 12 players, form a session (6v6)
    - write authoritative session + join events to Postgres
    """
    redis_client = _get_redis_client()
    
    # Use Redis list for queue (atomic operations)
    queue_key = "matchmaking_queue"
    
    if redis_client:
        try:
            # Add player to queue
            redis_client.rpush(queue_key, req.player_id)
            
            # Check queue length atomically
            queue_len = redis_client.llen(queue_key)
            
            if queue_len >= _session_size:
                # Pop 12 players atomically (use transaction for safety)
                pipe = redis_client.pipeline()
                for _ in range(_session_size):
                    pipe.lpop(queue_key)
                players_list = pipe.execute()
                players = [p for p in players_list if p]  # Filter None values
                
                if len(players) == _session_size:
                    session_id = str(uuid.uuid4())
                    
                    conn = _get_db_conn()
                    backend_pod = os.getenv("HOSTNAME", "unknown")
                    players_json = ",".join(players)
                    
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO matches (session_id, players_json, backend_pod) VALUES (%s, %s, %s)",
                            (session_id, players_json, backend_pod),
                        )
                        for p in players:
                            cur.execute(
                                "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
                                (session_id, "join", p),
                            )
                    
                    # Allocate game server (pod + NodePort Service); proxy gets address to tell clients
                    game_server_pod, connect_host, connect_port = _create_game_server_pod(session_id, players)
                    
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE matches SET game_server_pod = %s WHERE session_id = %s",
                            (game_server_pod, session_id),
                        )
                        cur.execute(
                            "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
                            (session_id, "start", ""),
                        )
                    
                    if redis_client:
                        try:
                            redis_client.sadd("active_sessions", session_id)
                            redis_client.set(f"session:{session_id}:pod", game_server_pod, ex=3600)
                            redis_client.set(f"session:{session_id}:host", connect_host, ex=3600)
                            redis_client.set(f"session:{session_id}:port", str(connect_port), ex=3600)
                        except Exception:  # noqa: BLE001
                            pass
                    
                    return MatchResponse(
                        session_id=session_id,
                        players=players,
                        connect_host=connect_host,
                        connect_port=connect_port,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis queue operation failed: {e}, falling back to in-memory")
    
    # Fallback to in-memory queue (single pod only)
    if not hasattr(join_match, '_local_queue'):
        join_match._local_queue = deque()
    
    join_match._local_queue.append(req.player_id)
    
    if len(join_match._local_queue) >= _session_size:
        players: List[str] = []
        for _ in range(_session_size):
            players.append(join_match._local_queue.popleft())
        
        session_id = str(uuid.uuid4())
        
        conn = _get_db_conn()
        backend_pod = os.getenv("HOSTNAME", "unknown")
        players_json = ",".join(players)
        
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO matches (session_id, players_json, backend_pod) VALUES (%s, %s, %s)",
                (session_id, players_json, backend_pod),
            )
            for p in players:
                cur.execute(
                    "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
                    (session_id, "join", p),
                )
        
        game_server_pod, connect_host, connect_port = _create_game_server_pod(session_id, players)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE matches SET game_server_pod = %s WHERE session_id = %s",
                (game_server_pod, session_id),
            )
            cur.execute(
                "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
                (session_id, "start", ""),
            )
        
        redis_client = _get_redis_client()
        if redis_client:
            try:
                redis_client.sadd("active_sessions", session_id)
                redis_client.set(f"session:{session_id}:pod", game_server_pod, ex=3600)
                redis_client.set(f"session:{session_id}:host", connect_host, ex=3600)
                redis_client.set(f"session:{session_id}:port", str(connect_port), ex=3600)
            except Exception:  # noqa: BLE001
                pass
        
        return MatchResponse(
            session_id=session_id,
            players=players,
            connect_host=connect_host,
            connect_port=connect_port,
        )
    
    # pending / solo case (not yet in a full 6v6)
    pending_id = f"pending:{req.player_id}"
    return MatchResponse(session_id=pending_id, players=[req.player_id])


@app.get("/match/status")
def match_status(player_id: str) -> dict:
    """
    Called by proxy so queued clients can poll until matched.
    Returns pending or matched with connect address for the game server.
    """
    conn = _get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.session_id, m.ended_at
            FROM match_events e
            JOIN matches m ON m.session_id = e.session_id
            WHERE e.player_id = %s AND e.event_type = 'join'
            ORDER BY e.id DESC LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
    
    if not row:
        return {"status": "pending"}
    
    session_id, ended_at = row
    if ended_at:
        return {"status": "ended", "session_id": session_id}
    
    # Session exists and not ended; get connect address from Redis
    redis_client = _get_redis_client()
    if redis_client:
        host = redis_client.get(f"session:{session_id}:host") or ""
        port_str = redis_client.get(f"session:{session_id}:port") or "0"
        port = int(port_str) if port_str.isdigit() else 0
        if host and port:
            return {
                "status": "matched",
                "session_id": session_id,
                "connect_host": host,
                "connect_port": port,
            }
    
    # Fallback: read from DB if we stored it (we don't currently store host/port in DB)
    return {"status": "pending"}


@app.post("/match/start", response_model=StartMatchResponse)
def start_match(req: StartMatchRequest) -> StartMatchResponse:
    """
    Start a match by creating a game server pod.
    Call this after matchmaking forms a session.
    """
    session_id = req.session_id
    
    # Check if session exists in DB (not just in-memory, since we have multiple replicas)
    conn = _get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT players_json FROM matches WHERE session_id = %s AND ended_at IS NULL",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found or already ended")
        
        players_json = row[0]
        players = players_json.split(",") if players_json else []
    
    # Create game server pod
    try:
        logger.info(f"Starting match for session {session_id}")
        
        game_server_pod, connect_host, connect_port = _create_game_server_pod(session_id, players)
        
        # Update DB with game server pod name
        conn = _get_db_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE matches SET game_server_pod = %s WHERE session_id = %s",
                (game_server_pod, session_id),
            )
            cur.execute(
                "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
                (session_id, "start", ""),
            )
        
        # Track active session in Redis
        redis_client = _get_redis_client()
        if redis_client:
            try:
                redis_client.sadd("active_sessions", session_id)
                redis_client.set(f"session:{session_id}:pod", game_server_pod, ex=3600)  # TTL 1 hour
                logger.info(f"Tracked active session {session_id} in Redis")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to track session in Redis: {e}")
        
        logger.info(f"Match started successfully: {session_id} -> {game_server_pod} ({connect_host}:{connect_port})")
        return StartMatchResponse(
            session_id=session_id,
            game_server_pod=game_server_pod,
            status="started",
            connect_host=connect_host,
            connect_port=connect_port,
        )
    except Exception as e:  # noqa: BLE001
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        logger.error(f"ERROR starting match {session_id}: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Failed to start match: {str(e)}")


@app.post("/match/{session_id}/end")
def end_match(session_id: str) -> dict:
    """
    End a match by deleting the game server pod and updating DB.
    """
    # Delete game server pod (don't fail the request if this fails)
    try:
        _delete_game_server_pod(session_id)
    except Exception as e:  # noqa: BLE001
        # Log but don't fail the request - cleanup can happen later
        logger.error(f"Failed to delete game server pod for {session_id}: {e}")
        # Still mark as ended in DB so we can clean up later
    
    # Update DB
    conn = _get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE matches SET ended_at = now() WHERE session_id = %s",
            (session_id,),
        )
        cur.execute(
            "INSERT INTO match_events (session_id, event_type, player_id) VALUES (%s, %s, %s)",
            (session_id, "end", ""),
        )
    
    # Remove from Redis active sessions
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_client.srem("active_sessions", session_id)
            redis_client.delete(f"session:{session_id}:pod")
            redis_client.delete(f"session:{session_id}:host")
            redis_client.delete(f"session:{session_id}:port")
            logger.info(f"Removed active session {session_id} from Redis")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to remove session from Redis: {e}")
    
    return {"status": "ended", "session_id": session_id}


@app.get("/server-stats")
def get_server_stats() -> dict:
    """
    Return game server stats from DB (TTL, client counts).
    Used by the print-server-data script without requiring psycopg2 on the host.
    """
    try:
        conn = _get_db_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, state, client_count, ttl_seconds, updated_at
                FROM game_server_stats
                ORDER BY updated_at DESC
                """
            )
            rows = cur.fetchall()
        return {
            "servers": [
                {
                    "session_id": row[0],
                    "state": row[1],
                    "client_count": row[2],
                    "ttl_seconds": row[3],
                    "updated_at": str(row[4]) if row[4] else None,
                }
                for row in rows
            ],
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to query server stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions/active")
def get_active_sessions() -> dict:
    """
    Get real-time count and list of active sessions (matches) from Redis.
    Each session represents one match with 12 players.
    Falls back to Postgres if Redis unavailable.
    
    Returns:
    - count: number of active sessions (matches), not players
    - sessions: list of session objects with session_id and game_server_pod
    """
    redis_client = _get_redis_client()
    
    if redis_client:
        try:
            session_ids = redis_client.smembers("active_sessions")
            count = len(session_ids)
            
            # Get pod info for each session
            sessions = []
            for sid in session_ids:
                pod = redis_client.get(f"session:{sid}:pod") or "unknown"
                sessions.append({"session_id": sid, "game_server_pod": pod})
            
            return {
                "count": count,
                "sessions": sessions,
                "source": "redis",
                "note": "Each session = 1 match with 12 players",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis query failed: {e}, falling back to Postgres")
    
    # Fallback to Postgres - only return recent matches (last 5 minutes) to avoid stale data
    try:
        conn = _get_db_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, game_server_pod, created_at 
                FROM matches 
                WHERE ended_at IS NULL 
                  AND created_at > now() - interval '5 minutes'
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
            sessions = [
                {
                    "session_id": row[0],
                    "game_server_pod": row[1] or "unknown",
                    "created_at": str(row[2]) if row[2] else None,
                }
                for row in rows
            ]
            return {
                "count": len(sessions),
                "sessions": sessions,
                "source": "postgres",
                "note": "Each session = 1 match with 12 players (showing last 5 minutes only)",
            }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to query active sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get active sessions: {e}")


@app.post("/cleanup/orphaned-servers")
def cleanup_orphaned_servers() -> dict:
    """
    Clean up game server deployments for matches that have ended.
    Useful for cleaning up pods that failed to delete during match end.
    """
    conn = _get_db_conn()
    k8s_apps_api = _get_k8s_api()
    namespace = os.getenv("NAMESPACE", "default")
    
    # Find all game server deployments
    try:
        deployments = k8s_apps_api.list_namespaced_deployment(
            namespace=namespace,
            label_selector="app=game-server",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list deployments: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list deployments: {e}")
    
    cleaned = 0
    with conn.cursor() as cur:
        for dep in deployments.items:
            # Extract session_id from deployment name (game-server-{first8chars})
            dep_name = dep.metadata.name
            if not dep_name.startswith("game-server-"):
                continue
            
            session_prefix = dep_name.replace("game-server-", "")
            
            # Check if match is ended
            cur.execute(
                "SELECT ended_at FROM matches WHERE session_id LIKE %s",
                (f"{session_prefix}%",),
            )
            row = cur.fetchone()
            
            if row and row[0]:  # Match has ended_at timestamp
                try:
                    core_api = _get_core_v1_api()
                    try:
                        core_api.delete_namespaced_service(name=dep_name, namespace=namespace)
                    except ApiException:  # noqa: BLE001
                        pass
                    k8s_apps_api.delete_namespaced_deployment(
                        name=dep_name,
                        namespace=namespace,
                        body=client.V1DeleteOptions(propagation_policy="Foreground"),
                    )
                    cleaned += 1
                    logger.info(f"Cleaned up orphaned deployment: {dep_name}")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"Failed to delete {dep_name}: {e}")
    
    return {"cleaned": cleaned, "message": f"Cleaned up {cleaned} orphaned game server deployments"}



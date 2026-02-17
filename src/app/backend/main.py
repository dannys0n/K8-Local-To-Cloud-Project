from typing import List
import logging
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from kubernetes import client
from kubernetes.client.rest import ApiException

from k8s_game_server import create_game_server_pod, delete_game_server_pod, get_core_v1_api, get_k8s_api
from models import MatchRequest, MatchResponse
from settings import FLUSH_WAIT_SECONDS, MIN_PARTIAL_SESSION_SIZE, NAMESPACE, SESSION_SIZE
from storage import (
    append_local_queue,
    dequeue_players,
    get_db_conn,
    get_player_queue_ts_key,
    get_redis_client,
    local_dequeue,
    local_oldest_wait_seconds,
    local_queue_len,
    track_session_in_redis,
    untrack_session_in_redis,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Game Backend", version="0.1.0")


def _create_match_session(players: List[str]) -> MatchResponse:
    session_id = str(uuid.uuid4())
    conn = get_db_conn()
    backend_pod = os.getenv("HOSTNAME", "unknown")
    players_json = ",".join(players)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO matches (session_id, players_json, backend_pod) VALUES (%s, %s, %s)",
            (session_id, players_json, backend_pod),
        )

    game_server_pod, connect_host, connect_port = create_game_server_pod(session_id, players)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE matches SET game_server_pod = %s WHERE session_id = %s",
            (game_server_pod, session_id),
        )
    track_session_in_redis(session_id, game_server_pod, connect_host, connect_port)
    return MatchResponse(
        session_id=session_id,
        players=players,
        connect_host=connect_host,
        connect_port=connect_port,
    )


@app.get("/health")
def health() -> dict:
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception:  # noqa: BLE001
        return {"status": "degraded"}
    return {"status": "ok"}


@app.post("/match/join", response_model=MatchResponse)
def join_match(req: MatchRequest) -> MatchResponse:
    redis_client = get_redis_client()
    queue_key = "matchmaking_queue"

    if redis_client:
        try:
            redis_client.rpush(queue_key, req.player_id)
            redis_client.set(get_player_queue_ts_key(req.player_id), str(time.time()), ex=3600)
            queue_len = redis_client.llen(queue_key)

            flush_count = 0
            if queue_len >= SESSION_SIZE:
                flush_count = SESSION_SIZE
            elif queue_len >= MIN_PARTIAL_SESSION_SIZE:
                oldest_player = redis_client.lindex(queue_key, 0)
                if oldest_player:
                    queued_at_raw = redis_client.get(get_player_queue_ts_key(oldest_player))
                    if queued_at_raw:
                        try:
                            oldest_wait = time.time() - float(queued_at_raw)
                            if oldest_wait >= FLUSH_WAIT_SECONDS:
                                flush_count = queue_len
                        except ValueError:
                            pass

            if flush_count > 0:
                players = dequeue_players(redis_client, queue_key, flush_count)
                if len(players) == flush_count:
                    return _create_match_session(players)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis queue operation failed: {e}, falling back to in-memory")

    append_local_queue(req.player_id)
    local_len = local_queue_len()
    local_flush_count = 0
    if local_len >= SESSION_SIZE:
        local_flush_count = SESSION_SIZE
    elif local_len >= MIN_PARTIAL_SESSION_SIZE:
        if local_oldest_wait_seconds() >= FLUSH_WAIT_SECONDS:
            local_flush_count = local_len

    if local_flush_count > 0:
        players = local_dequeue(local_flush_count)
        return _create_match_session(players)

    return MatchResponse(session_id=f"pending:{req.player_id}", players=[req.player_id])


@app.get("/match/status")
def match_status(player_id: str) -> dict:
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT session_id, ended_at
            FROM matches
            WHERE
              players_json = %s
              OR players_json LIKE %s
              OR players_json LIKE %s
              OR players_json LIKE %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                player_id,
                f"{player_id},%",
                f"%,{player_id},%",
                f"%,{player_id}",
            ),
        )
        row = cur.fetchone()

    if not row:
        return {"status": "pending"}

    session_id, ended_at = row
    if ended_at:
        return {"status": "ended", "session_id": session_id}

    redis_client = get_redis_client()
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
    return {"status": "pending"}


@app.post("/match/{session_id}/end")
def end_match(session_id: str) -> dict:
    try:
        delete_game_server_pod(session_id)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to delete game server pod for {session_id}: {e}")

    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE matches SET ended_at = now() WHERE session_id = %s",
            (session_id,),
        )
    untrack_session_in_redis(session_id)
    return {"status": "ended", "session_id": session_id}


@app.get("/sessions/active")
def get_active_sessions() -> dict:
    redis_client = get_redis_client()
    if redis_client:
        try:
            session_ids = redis_client.smembers("active_sessions")
            sessions = []
            for sid in session_ids:
                pod = redis_client.get(f"session:{sid}:pod") or "unknown"
                sessions.append({"session_id": sid, "game_server_pod": pod})
            return {
                "count": len(session_ids),
                "sessions": sessions,
                "source": "redis",
                "note": "Each session = 1 match with 12 players",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis query failed: {e}, falling back to Postgres")

    try:
        conn = get_db_conn()
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
    conn = get_db_conn()
    k8s_apps_api = get_k8s_api()
    try:
        deployments = k8s_apps_api.list_namespaced_deployment(
            namespace=NAMESPACE,
            label_selector="app=game-server",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list deployments: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list deployments: {e}")

    cleaned = 0
    with conn.cursor() as cur:
        for dep in deployments.items:
            dep_name = dep.metadata.name
            if not dep_name.startswith("game-server-"):
                continue
            session_prefix = dep_name.replace("game-server-", "")
            cur.execute(
                "SELECT ended_at FROM matches WHERE session_id LIKE %s",
                (f"{session_prefix}%",),
            )
            row = cur.fetchone()
            if row and row[0]:
                try:
                    core_api = get_core_v1_api()
                    try:
                        core_api.delete_namespaced_service(name=dep_name, namespace=NAMESPACE)
                    except ApiException:  # noqa: BLE001
                        pass
                    k8s_apps_api.delete_namespaced_deployment(
                        name=dep_name,
                        namespace=NAMESPACE,
                        body=client.V1DeleteOptions(propagation_policy="Foreground"),
                    )
                    cleaned += 1
                    logger.info(f"Cleaned up orphaned deployment: {dep_name}")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"Failed to delete {dep_name}: {e}")

    return {"cleaned": cleaned, "message": f"Cleaned up {cleaned} orphaned game server deployments"}



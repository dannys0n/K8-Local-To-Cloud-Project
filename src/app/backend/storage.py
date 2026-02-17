from collections import deque
import logging
import os
import time
from typing import List

import psycopg2
import redis

from settings import DATABASE_URL, REDIS_HOST, REDIS_PORT

logger = logging.getLogger(__name__)

_db_conn = None
_redis_client = None
_local_queue = deque()


def get_db_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = psycopg2.connect(DATABASE_URL)
        _db_conn.autocommit = True
        ensure_schema(_db_conn)
    return _db_conn


def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            _redis_client.ping()
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis connection failed: {e}, continuing without Redis")
            _redis_client = None
    return _redis_client


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
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
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='matches' AND column_name='game_server_pod';
            """
        )
        if not cur.fetchone():
            cur.execute("ALTER TABLE matches ADD COLUMN game_server_pod text;")

        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='matches' AND column_name='ended_at';
            """
        )
        if not cur.fetchone():
            cur.execute("ALTER TABLE matches ADD COLUMN ended_at timestamptz;")


def track_session_in_redis(session_id: str, game_server_pod: str, connect_host: str, connect_port: int) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    try:
        redis_client.sadd("active_sessions", session_id)
        redis_client.set(f"session:{session_id}:pod", game_server_pod, ex=3600)
        redis_client.set(f"session:{session_id}:host", connect_host, ex=3600)
        redis_client.set(f"session:{session_id}:port", str(connect_port), ex=3600)
    except Exception:  # noqa: BLE001
        pass


def untrack_session_in_redis(session_id: str) -> None:
    redis_client = get_redis_client()
    if not redis_client:
        return
    try:
        redis_client.srem("active_sessions", session_id)
        redis_client.delete(f"session:{session_id}:pod")
        redis_client.delete(f"session:{session_id}:host")
        redis_client.delete(f"session:{session_id}:port")
        logger.info(f"Removed active session {session_id} from Redis")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to remove session from Redis: {e}")


def get_player_queue_ts_key(player_id: str) -> str:
    return f"matchmaking:queued_at:{player_id}"


def dequeue_players(redis_client, queue_key: str, count: int) -> List[str]:
    pipe = redis_client.pipeline()
    for _ in range(count):
        pipe.lpop(queue_key)
    players_list = pipe.execute()
    players = [p for p in players_list if p]
    if players:
        try:
            cleanup = redis_client.pipeline()
            for p in players:
                cleanup.delete(get_player_queue_ts_key(p))
            cleanup.execute()
        except Exception:  # noqa: BLE001
            pass
    return players


def append_local_queue(player_id: str) -> None:
    _local_queue.append((player_id, time.time()))


def local_queue_len() -> int:
    return len(_local_queue)


def local_oldest_wait_seconds() -> float:
    if not _local_queue:
        return 0.0
    return time.time() - _local_queue[0][1]


def local_dequeue(count: int) -> List[str]:
    players: List[str] = []
    for _ in range(count):
        p, _queued_at = _local_queue.popleft()
        players.append(p)
    return players

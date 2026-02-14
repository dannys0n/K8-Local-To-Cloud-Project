#!/usr/bin/env python3
"""4-player chatroom: TCP + WebSocket; CREATE_ROOM / JOIN <room_id> <name>; up to 4/room."""
import json
import os
import random
import socket
import string
import threading
from collections import deque

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "7654"))
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
ROOM_TTL = int(os.environ.get("ROOM_TTL", "3600"))

POD_NAME = os.environ.get("HOSTNAME", "local")
SVC_DNS = os.environ.get("TCP_DEMO_SVC_DNS", "tcp-demo.tcp-demo.svc.cluster.local")

REDIS_ROOM_PREFIX = "chat:room:"

_redis = None
_conn_id_next = 0
_conn_id_lock = threading.Lock()

# conn_id -> (room_id, player_name)
_conn_room = {}
# room_id -> list of (conn_id, transport, name) — transport is socket or websocket
_room_members = {}
# room_id -> deque of recent messages (incl. join/leave) for session history
_room_history = {}
_rooms_lock = threading.Lock()
HISTORY_MAX = 100


def next_conn_id():
    global _conn_id_next
    with _conn_id_lock:
        _conn_id_next += 1
        return _conn_id_next


def get_redis():
    global _redis
    if _redis is None:
        try:
            import redis
            _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            _redis.ping()
        except Exception as e:
            print(f"[redis] not available: {e}")
            _redis = False
    return _redis if _redis else None


def random_room_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def room_key(room_id: str):
    return f"{REDIS_ROOM_PREFIX}{room_id}"


def send_to_transport(transport, msg: str):
    """Send text to either a socket (TCP) or WebSocket."""
    try:
        if hasattr(transport, "sendall"):
            transport.sendall((msg + "\n").encode("utf-8"))
        else:
            transport.send(msg + "\n")
    except (BrokenPipeError, OSError, Exception):
        pass


def _append_history(room_id: str, line: str):
    with _rooms_lock:
        _room_history.setdefault(room_id, deque(maxlen=HISTORY_MAX)).append(line)


def _get_room_members(room_id: str):
    """Return copy of (conn_id, transport, name) list for room."""
    with _rooms_lock:
        return list(_room_members.get(room_id, []))


def _broadcast_raw(room_id: str, text: str, exclude_conn_id: int = None):
    _append_history(room_id, text)
    for cid, transport, _ in _get_room_members(room_id):
        if exclude_conn_id is not None and cid == exclude_conn_id:
            continue
        send_to_transport(transport, text)


def broadcast(room_id: str, sender_name: str, message: str, exclude_conn_id: int = None):
    _broadcast_raw(room_id, f"{sender_name}: {message}", exclude_conn_id)


def broadcast_system(room_id: str, message: str):
    _broadcast_raw(room_id, message)


def remove_from_room(conn_id: int, r):
    """Remove connection from in-memory room and optionally update Redis."""
    room_still_has_members = False
    name_still_present = False
    room_id = None
    name = None
    with _rooms_lock:
        if conn_id not in _conn_room:
            return
        room_id, name = _conn_room.pop(conn_id)
        members = _room_members.get(room_id, [])
        _room_members[room_id] = [(c, tr, n) for c, tr, n in members if c != conn_id]
        if not _room_members[room_id]:
            del _room_members[room_id]
            _room_history.pop(room_id, None)
        else:
            room_still_has_members = True
            # Only announce "left" if no other connection for this name remains (avoid duplicate when same user reconnects)
            name_still_present = any(n == name for _, _, n in _room_members[room_id])
            if r:
                key = room_key(room_id)
                raw = r.get(key)
                if raw:
                    data = json.loads(raw)
                    data["players"] = [p for p in data["players"] if p != name]
                    r.setex(key, ROOM_TTL, json.dumps(data))
    # No join/leave or player-list UI; only update Redis and in-memory state


def process_command(r, line: str, conn_id: int, transport):
    parts = line.split(maxsplit=2)
    cmd = (parts[0].upper() if parts else "").strip()

    with _rooms_lock:
        in_room = conn_id in _conn_room
        room_id, my_name = _conn_room.get(conn_id, (None, None))
    if in_room and (not cmd or cmd not in ("JOIN", "LEAVE", "CREATE_ROOM", "HELP")):
        broadcast(room_id, my_name, line)  # entire session sees the message (including sender)
        return None

    if not cmd:
        return "ERR type a message or use HELP"

    if cmd == "HELP":
        return "OK CREATE_ROOM | JOIN <room_id> <name> | LEAVE | type to chat"

    if cmd == "CREATE_ROOM":
        if not r:
            return "ERR redis unavailable"
        room_id = random_room_id()
        key = room_key(room_id)
        value = json.dumps({"pod_id": POD_NAME, "players": []})
        if r.set(key, value, nx=True, ex=ROOM_TTL):
            return f"ROOM {room_id}"
        return "ERR create failed (retry)"

    if cmd == "JOIN":
        if len(parts) < 3:
            return "ERR JOIN <room_id> <name>"
        room_id = parts[1].strip()
        name = parts[2].strip()
        if not room_id or not name:
            return "ERR bad args"
        if not r:
            return "ERR redis unavailable"
        key = room_key(room_id)
        raw = r.get(key)
        if not raw:
            return "ERR Room not found. Check the ID."
        data = json.loads(raw)
        if data["pod_id"] != POD_NAME:
            pod_dns = f"{data['pod_id']}.{SVC_DNS}"
            return f"REDIRECT {pod_dns}:{LISTEN_PORT}"
        players = data["players"]
        if name in players:
            with _rooms_lock:
                _conn_room[conn_id] = (room_id, name)
                _room_members.setdefault(room_id, []).append((conn_id, transport, name))
            return f"JOINED {room_id} (already in)"
        if len(players) >= 4:
            return "ERR Room is full (4/4)."
        players.append(name)
        r.setex(key, ROOM_TTL, json.dumps(data))
        with _rooms_lock:
            _conn_room[conn_id] = (room_id, name)
            _room_members.setdefault(room_id, []).append((conn_id, transport, name))
        return f"JOINED {room_id} ({len(players)}/4) — type to chat"

    if cmd == "LEAVE":
        remove_from_room(conn_id, r)
        return "LEFT"

    return "ERR unknown (try HELP)"


def handle_tcp_client(conn: socket.socket, addr, conn_id: int):
    r = get_redis()
    buf = b""
    try:
        conn.settimeout(None)
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip(b"\r\n").decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                reply = process_command(r, line, conn_id, conn)
                if reply:
                    send_to_transport(conn, reply)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        remove_from_room(conn_id, r)
        conn.close()


def run_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", LISTEN_PORT))
    server.listen(64)
    while True:
        conn, addr = server.accept()
        cid = next_conn_id()
        t = threading.Thread(target=handle_tcp_client, args=(conn, addr, cid), daemon=True)
        t.start()


def run_web_app():
    from flask import Flask, send_from_directory
    from flask_sock import Sock

    app = Flask(__name__, static_folder="web", static_url_path="")
    sock = Sock(app)

    @app.route("/")
    def index():
        return send_from_directory("web", "index.html")

    @app.route("/room")
    def room():
        return send_from_directory("web", "room.html")

    @sock.route("/ws")
    def ws_handler(ws):
        r = get_redis()
        cid = next_conn_id()
        try:
            while True:
                line = ws.receive()
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                reply = process_command(r, line, cid, ws)
                if reply:
                    ws.send(reply)
        except Exception:
            pass
        finally:
            remove_from_room(cid, r)

    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False)


def main():
    print(
        f"[tcp-demo] pod={POD_NAME} tcp=0.0.0.0:{LISTEN_PORT} web=0.0.0.0:{WEB_PORT} redis={REDIS_HOST}:{REDIS_PORT}"
    )
    web_thread = threading.Thread(target=run_web_app, daemon=True)
    web_thread.start()
    run_tcp_server()


if __name__ == "__main__":
    main()

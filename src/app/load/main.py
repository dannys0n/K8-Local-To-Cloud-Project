import argparse
import os
import socket
import time
import uuid

import requests


class GameClient:
    """Simulates a realistic game client: join queue → get server address → play → end."""

    def __init__(self, base_url: str, player_id: str):
        self.base_url = base_url
        self.player_id = player_id
        self.game_server_host_override = os.getenv("GAME_SERVER_HOST_OVERRIDE", "").strip()
        self.session_id = None
        self.connect_host = None
        self.connect_port = None

    def _log(self, msg: str) -> None:
        print(f"  {self.player_id[:8]} {msg}")

    def join_matchmaking(self) -> dict | None:
        """Join matchmaking queue. When 12 are ready, backend allocates game server; response may include connect address."""
        self._log("state=joining_matchmaking")
        try:
            resp = requests.post(
                f"{self.base_url}/api/match/join",
                json={"player_id": self.player_id},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            self.session_id = data.get("session_id")
            self.connect_host = self.game_server_host_override or data.get("connect_host") or None
            self.connect_port = data.get("connect_port") or 0
            if self.connect_host and self.connect_port:
                self._log(
                    f"state=matched session={self.session_id} server={self.connect_host}:{self.connect_port}"
                )
            else:
                self._log(f"state=queued session={self.session_id}")
            return data
        except Exception as e:  # noqa: BLE001
            self._log(f"state=join_error error={e}")
            return None

    def poll_status(self) -> dict | None:
        """Poll until matched; proxy returns game server address when backend has allocated."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/match/status",
                params={"player_id": self.player_id},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "matched":
                self.session_id = data.get("session_id")
                self.connect_host = self.game_server_host_override or data.get("connect_host")
                self.connect_port = data.get("connect_port", 0)
            return data
        except Exception as e:  # noqa: BLE001
            self._log(f"state=status_error error={e}")
            return None

    def end_match(self) -> bool:
        """End the match."""
        if not self.session_id or self.session_id.startswith("pending"):
            return False
        
        try:
            resp = requests.post(
                f"{self.base_url}/api/match/{self.session_id}/end",
                timeout=5.0,
            )
            resp.raise_for_status()
            self._log(f"state=session_ended session={self.session_id}")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"state=end_error session={self.session_id} error={e}")
            return False

    def connect_and_wait_for_stop(
        self,
        *,
        match_duration_seconds: int = 30,
        recv_timeout: float = 60.0,
        connect_retries: int = 60,
        connect_retry_delay: float = 1.0,
    ) -> float | None:
        """
        Connect to the game server over TCP, request a custom match of match_duration_seconds
        (client-decided config), then block until the server sends STATE stop and closes.
        Retries the initial connection so the game server pod has time to start.
        Returns running length in seconds from server ack, or None.
        """
        if not self.connect_host or not self.connect_port:
            return None
        running_length = None
        sock = None
        last_server_state = None
        self._log(f"state=tcp_connecting target={self.connect_host}:{self.connect_port}")
        for attempt in range(connect_retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(recv_timeout)
                sock.connect((self.connect_host, self.connect_port))
                self._log(
                    f"state=tcp_connected target={self.connect_host}:{self.connect_port} attempts={attempt + 1}"
                )
                break
            except (ConnectionRefusedError, OSError) as e:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:  # noqa: BLE001
                        pass
                    sock = None
                if attempt < connect_retries - 1:
                    time.sleep(connect_retry_delay)
                else:
                    self._log(
                        f"state=tcp_connect_failed attempts={connect_retries} error={e}"
                    )
                    return None
        if sock is None:
            return None
        try:
            # Client decides config: request a 30s (or custom) game
            sock.sendall(f"REQUEST_MATCH {match_duration_seconds}\n".encode())
            first_payload = sock.recv(512).decode()
            for line in first_payload.splitlines():
                line = line.strip()
                if line.startswith("RUNNING_LENGTH "):
                    # Keep only the numeric token even if other messages are in same TCP frame.
                    token = line.split(maxsplit=1)[1].split()[0]
                    running_length = float(token)
                    self._log(f"state=running_length value={running_length}")
                if line.startswith("STATE "):
                    server_state = line.split(maxsplit=1)[1].strip().lower()
                    if server_state != last_server_state:
                        last_server_state = server_state
                        self._log(f"state=server_{server_state}")
            # Wait for server to send STATE stop and close the connection
            while True:
                data = sock.recv(256).decode()
                if not data:
                    self._log("state=tcp_disconnected")
                    break
                for line in data.splitlines():
                    line = line.strip()
                    if line.startswith("STATE "):
                        server_state = line.split(maxsplit=1)[1].strip().lower()
                        if server_state != last_server_state:
                            last_server_state = server_state
                            self._log(f"state=server_{server_state}")
                        if server_state == "stop":
                            return running_length
        except (socket.timeout, ConnectionResetError, BrokenPipeError, OSError) as e:
            if "STATE STOP" not in str(e).upper():
                self._log(f"state=tcp_error error={e}")
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
        return running_length


def simulate_client_lifecycle(base_url: str, match_duration: float) -> None:
    """
    Flow: join queue at proxy → backend allocates game server when 12 ready →
    proxy returns address (in join response or via status poll) → play → end.
    """
    player_id = str(uuid.uuid4())
    client = GameClient(base_url, player_id)
    
    # 1. Join matchmaking (proxy forwards to backend; backend queues, may form match and allocate server)
    result = client.join_matchmaking()
    if not result:
        return
    
    session_id = result.get("session_id", "")
    # If we got connect address in the response, we're matched (12th player or batch)
    if result.get("connect_host") and result.get("connect_port"):
        pass  # already have address
    elif session_id.startswith("pending"):
        # Queued; poll until backend has allocated and proxy can tell us the server address
        max_wait = 60.0
        wait_start = time.time()
        last_status = None
        while time.time() - wait_start < max_wait:
            status = client.poll_status()
            if status:
                current = status.get("status")
                if current != last_status:
                    last_status = current
                    client._log(f"state=match_status_{current}")
            if status and status.get("status") == "matched":
                client._log(
                    f"state=matched session={client.session_id} server={client.connect_host}:{client.connect_port}"
                )
                break
            if status and status.get("status") == "ended":
                client._log("state=session_ended_before_connect")
                return
            time.sleep(0.5)
        if not client.session_id or not client.connect_host:
            client._log("state=match_timeout_waiting_for_server")
            return
    else:
        # Session but no address (shouldn't happen if join allocates)
        if not client.connect_host:
            return
    
    # 2. Connect to game server over TCP; client requests 30s custom match; wait for server to send STATE stop (authoritative)
    client.connect_and_wait_for_stop(match_duration_seconds=30, recv_timeout=max(60.0, 30 + 10))

    # 3. End match (proxy forwards to backend; backend deletes game server)
    client._log(f"state=ending_session session={client.session_id}")
    client.end_match()


def main() -> None:
    """
    Realistic game client load simulator.

    Usage:
      python main.py 100

    Simulates clients that:
    - Join matchmaking
    - Start matches (creates game server pods)
    - Play for a duration (with reconnection checks)
    - End matches (deletes game server pods)
    """
    default_url = os.getenv("TARGET_URL", "http://localhost:8080")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "clients",
        type=int,
        help="number of concurrent client lifecycles",
    )
    parser.add_argument(
        "--url",
        default=default_url,
        help="base URL (defaults to localhost on port 8080)",
    )
    parser.add_argument(
        "--match-duration",
        type=float,
        default=float(os.getenv("MATCH_DURATION", "30.0")),
        help="seconds each match lasts",
    )
    parser.add_argument(
        "--spawn-rate",
        type=float,
        default=float(os.getenv("SPAWN_RATE", "0")),  # 0 = spawn all immediately
        help="clients spawned per second (0 = spawn all immediately)",
    )
    args = parser.parse_args()

    gs_host_override = os.getenv("GAME_SERVER_HOST_OVERRIDE", "").strip()
    if gs_host_override:
        print(f"Overriding game server host with: {gs_host_override}")
    if args.spawn_rate > 0:
        print(
            f"Spawning {args.clients} clients at {args.spawn_rate}/s "
            f"(match duration: {args.match_duration}s, Ctrl+C to stop)"
        )
    else:
        print(
            f"Spawning all {args.clients} clients simultaneously "
            f"(match duration: {args.match_duration}s, Ctrl+C to stop)"
        )

    import threading

    active_threads = []
    
    try:
        # Spawn all clients
        for i in range(args.clients):
            thread = threading.Thread(
                target=simulate_client_lifecycle,
                args=(args.url, args.match_duration),
                daemon=True,
            )
            thread.start()
            active_threads.append(thread)
            
            # Spawn rate limiting (if specified)
            if args.spawn_rate > 0 and i < args.clients - 1:
                time.sleep(1.0 / args.spawn_rate)
        
        # Wait for all threads
        for thread in active_threads:
            thread.join(timeout=args.match_duration + 30)
        
    except KeyboardInterrupt:
        print("\nStopping clients...")


if __name__ == "__main__":
    main()

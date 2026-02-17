import os
import socket
import time

import requests


class GameClient:
    """Simulates a realistic game client: join queue -> get server address -> play -> end."""

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
            sock.sendall(f"REQUEST_MATCH {match_duration_seconds}\n".encode())
            first_payload = sock.recv(512).decode()
            for line in first_payload.splitlines():
                line = line.strip()
                if line.startswith("RUNNING_LENGTH "):
                    token = line.split(maxsplit=1)[1].split()[0]
                    running_length = float(token)
                    self._log(f"state=running_length value={running_length}")
                if line.startswith("STATE "):
                    server_state = line.split(maxsplit=1)[1].strip().lower()
                    if server_state != last_server_state:
                        last_server_state = server_state
                        self._log(f"state=server_{server_state}")
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

import time
import uuid

from client import GameClient


def simulate_client_lifecycle(base_url: str, match_duration: float) -> None:
    """
    Flow: join queue at proxy -> backend allocates game server when ready ->
    proxy returns address (in join response or via status poll) -> play -> end.
    """
    player_id = str(uuid.uuid4())
    client = GameClient(base_url, player_id)

    result = client.join_matchmaking()
    if not result:
        return

    session_id = result.get("session_id", "")
    if result.get("connect_host") and result.get("connect_port"):
        pass
    elif session_id.startswith("pending"):
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
        if not client.connect_host:
            return

    client.connect_and_wait_for_stop(match_duration_seconds=30, recv_timeout=max(60.0, 30 + 10))
    client._log(f"state=ending_session session={client.session_id}")
    client.end_match()

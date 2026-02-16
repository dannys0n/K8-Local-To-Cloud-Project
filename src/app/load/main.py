import argparse
import os
import random
import socket
import time
import uuid

import requests


def _detect_lan_ip() -> str:
    """Best-effort LAN IP detection (no external call, uses routing table)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:  # noqa: BLE001
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class GameClient:
    """Simulates a realistic game client with join → play → end cycle."""

    def __init__(self, base_url: str, player_id: str):
        self.base_url = base_url
        self.player_id = player_id
        self.session_id = None
        self.game_server_pod = None

    def join_matchmaking(self) -> dict | None:
        """Join matchmaking queue."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/match/join",
                json={"player_id": self.player_id},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            self.session_id = data.get("session_id")
            return data
        except Exception as e:  # noqa: BLE001
            print(f"  {self.player_id[:8]} join error: {e}")
            return None

    def start_match(self) -> bool:
        """Start the match (create game server pod)."""
        if not self.session_id or self.session_id.startswith("pending"):
            return False
        
        try:
            resp = requests.post(
                f"{self.base_url}/api/match/start",
                json={"session_id": self.session_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            self.game_server_pod = data.get("game_server_pod")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  {self.player_id[:8]} start error: {e}")
            return False

    def check_status(self) -> dict | None:
        """Check match status (simulates reconnection check)."""
        if not self.session_id:
            return None
        
        try:
            # In real game, this would hit the game server pod directly
            # For now, just verify session exists
            return {"status": "active"}
        except Exception as e:  # noqa: BLE001
            print(f"  {self.player_id[:8]} status error: {e}")
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
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  {self.player_id[:8]} end error: {e}")
            return False


def simulate_client_lifecycle(base_url: str, match_duration: float) -> None:
    """Simulate one client's full lifecycle: join → start → play → end."""
    player_id = str(uuid.uuid4())
    client = GameClient(base_url, player_id)
    
    # Join matchmaking - keep retrying until matched (not pending)
    max_wait_time = 60.0  # Max 60 seconds to find a match
    wait_start = time.time()
    session_id = None
    
    while time.time() - wait_start < max_wait_time:
        result = client.join_matchmaking()
        if not result:
            time.sleep(1.0)  # Retry after error
            continue
        
        session_id = result.get("session_id", "")
        if not session_id.startswith("pending"):
            # Got a real match!
            break
        
        # Still pending, wait a bit and check again (simulate staying in queue)
        time.sleep(0.5)
    
    if not session_id or session_id.startswith("pending"):
        # Never got matched, give up
        return
    
    # Wait a bit (simulate waiting for other players)
    time.sleep(0.5)
    
    # Start match (create game server pod)
    if not client.start_match():
        return
    
    # Simulate playing (check status periodically, simulate reconnection)
    play_time = 0.0
    check_interval = 2.0
    
    while play_time < match_duration:
        time.sleep(min(check_interval, match_duration - play_time))
        play_time += check_interval
        
        # Randomly simulate reconnection check (10% chance)
        if random.random() < 0.1:
            client.check_status()
    
    # End match
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
    lan_ip = _detect_lan_ip()
    default_url = os.getenv(
        "TARGET_URL", f"http://{lan_ip}:8080"
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "clients",
        type=int,
        help="number of concurrent client lifecycles",
    )
    parser.add_argument(
        "--url",
        default=default_url,
        help="base URL (defaults to LAN IP on port 8080)",
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

    print(f"LAN IP detected as {lan_ip}")
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

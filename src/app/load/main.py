import argparse
import os
import time

from lifecycle import simulate_client_lifecycle


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

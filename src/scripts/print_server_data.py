#!/usr/bin/env python3
"""
Print game server stats (session_id, state, client_count, TTL) from the API.
No database driver needed on the host: uses the proxy/backend HTTP endpoint.
"""
import os
import sys

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

# Proxy URL when using make proxy-port-forward-local (default localhost:8080)
BASE_URL = os.getenv("PROXY_URL", os.getenv("BASE_URL", "http://localhost:8080"))


def main() -> int:
    url = f"{BASE_URL.rstrip('/')}/api/server-stats"
    try:
        resp = requests.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"Failed to get server stats: {e}", file=sys.stderr)
        print("Ensure proxy is reachable (e.g. make proxy-port-forward-local)", file=sys.stderr)
        return 1

    servers = data.get("servers") or []
    if not servers:
        print("No game server stats.")
        return 0

    col_names = ("session_id", "state", "client_count", "ttl_seconds", "updated_at")
    widths = [max(len(c), 12) for c in col_names]
    for s in servers:
        for i, k in enumerate(col_names):
            v = s.get(k, "")
            widths[i] = max(widths[i], len(str(v)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*col_names))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for s in servers:
        row = [str(s.get(k, "")) for k in col_names]
        print(fmt.format(*row))
    print(f"\nTotal: {len(servers)} server(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

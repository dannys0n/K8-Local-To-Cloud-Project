#!/usr/bin/env python3
import argparse
import json
import socket


def main() -> int:
    parser = argparse.ArgumentParser(description="TCP lab smoke check")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--messages", type=int, default=5)
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        stream = sock.makefile("rwb", buffering=0)
        stream.write(b"@location any\n")
        if not stream.readline():
            raise RuntimeError("connection closed during handshake")
        for index in range(1, args.messages + 1):
            stream.write(f"smoke-{index}\n".encode())
            response = stream.readline()
            if not response:
                raise RuntimeError("connection closed without a response")
            print(response.decode().rstrip())
        stream.write(b"@location new-york\n")
        routed = json.loads(stream.readline())
        if routed.get("location") != "new-york":
            raise RuntimeError(f"runtime route change failed: {routed}")
        stream.write(b"smoke-routed\n")
        response = json.loads(stream.readline())
        if response.get("location") != "new-york":
            raise RuntimeError(f"message used wrong route: {response}")
        print(json.dumps(response, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

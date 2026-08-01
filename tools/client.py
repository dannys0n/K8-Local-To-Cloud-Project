#!/usr/bin/env python3
import argparse
import socket
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive TCP lab client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        stream = sock.makefile("rwb", buffering=0)
        print(f"Connected to {args.host}:{args.port}. Enter messages; Ctrl+C exits.")
        for line in sys.stdin:
            message = line.strip()
            if not message:
                continue
            stream.write(message.encode("utf-8") + b"\n")
            response = stream.readline()
            if not response:
                print("Connection closed by server.")
                return 1
            print(response.decode("utf-8").rstrip())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)

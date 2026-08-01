#!/usr/bin/env python3
import argparse
import json
import socket
import sys
import time


LOCATIONS = ("any", "los-angeles", "new-york", "london", "singapore", "frankfurt")


def connect(host: str, port: int, location: str):
    while True:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=10)
            sock.settimeout(None)
            stream = sock.makefile("rwb", buffering=0)
            stream.write(f"@location {location}\n".encode())
            response = stream.readline()
            hello = json.loads(response)
            if location != "any" and hello.get("location") != location:
                raise ConnectionError(f"location handshake failed: {hello}")
            print(f"Connected to {hello['server']} ({hello['location']}) via {host}:{port}.")
            return sock, stream
        except (OSError, ValueError) as error:
            if sock:
                sock.close()
            print(f"Connect failed ({error}); retrying in 1 second.", file=sys.stderr)
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive reconnecting TCP lab client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--location", choices=LOCATIONS, default="any")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    print(f"Location: {args.location}. Enter messages; Ctrl+C exits.")
    sock = stream = None
    try:
        for line in sys.stdin:
            message = line.strip()
            if not message:
                continue
            while True:
                try:
                    if stream is None:
                        sock, stream = connect(args.host, args.port, args.location)
                    stream.write(message.encode("utf-8") + b"\n")
                    response = stream.readline()
                    if not response:
                        raise ConnectionError("connection closed")
                    print(response.decode("utf-8").rstrip())
                    break
                except (OSError, ConnectionError) as error:
                    print(f"Disconnected ({error}); reconnecting.", file=sys.stderr)
                    if stream:
                        stream.close()
                    if sock:
                        sock.close()
                    sock = stream = None
                    time.sleep(1)
    finally:
        if stream:
            stream.close()
        if sock:
            sock.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)

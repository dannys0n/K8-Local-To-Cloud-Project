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
            if hello.get("error"):
                raise ConnectionError(hello["error"])
            if location != "any" and hello.get("location") != location:
                raise ConnectionError(f"location handshake failed: {hello}")
            print(f"Connected to {hello['server']} ({hello['location']}) via {host}:{port}.")
            return sock, stream, hello
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

    print("Commands: /location NAME, /reconnect, /status, /locations, /help")
    print(f"Selected location: {args.location}. Enter messages; Ctrl+C exits.")
    sock = stream = hello = None
    try:
        sock, stream, hello = connect(args.host, args.port, args.location)
        for line in sys.stdin:
            message = line.strip()
            if not message:
                continue
            if message == "/help":
                print("/location NAME  switch location and reconnect")
                print("/reconnect      replace the current TCP connection")
                print("/status         show the selected and connected identity")
                print("/locations      list valid locations")
                continue
            if message == "/locations":
                print(", ".join(LOCATIONS))
                continue
            if message == "/status":
                connected = (
                    f"{hello['server']} ({hello['location']})" if hello else "disconnected"
                )
                print(f"Selected: {args.location}; connected: {connected}")
                continue
            if message == "/reconnect" or message.startswith("/location "):
                if message.startswith("/location "):
                    requested = message.removeprefix("/location ").strip()
                    if requested not in LOCATIONS:
                        print(f"Unknown location: {requested}. Use /locations.")
                        continue
                    args.location = requested
                if stream:
                    stream.close()
                if sock:
                    sock.close()
                sock = stream = hello = None
                sock, stream, hello = connect(args.host, args.port, args.location)
                continue
            if message.startswith("/"):
                print("Unknown command. Use /help.")
                continue
            while True:
                try:
                    if stream is None:
                        sock, stream, hello = connect(args.host, args.port, args.location)
                    stream.write(message.encode("utf-8") + b"\n")
                    response = stream.readline()
                    if not response:
                        raise ConnectionError("connection closed")
                    body = json.loads(response)
                    if body.get("error"):
                        raise ConnectionError(body["error"])
                    print(response.decode("utf-8").rstrip())
                    break
                except (OSError, ValueError, ConnectionError) as error:
                    print(f"Disconnected ({error}); reconnecting.", file=sys.stderr)
                    if stream:
                        stream.close()
                    if sock:
                        sock.close()
                    sock = stream = None
                    hello = None
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

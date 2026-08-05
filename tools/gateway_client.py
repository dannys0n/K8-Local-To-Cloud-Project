"""Persistent TCP client shared by interactive and headless clients."""

import json
import socket
import threading


class GatewayResponseError(Exception):
    pass


class GatewayClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.sock = None
        self.stream = None
        self.connection = "disconnected"
        self.gateway = None
        self.route = None
        self.latitude = None
        self.longitude = None

    def _close(self):
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()
        self.stream = self.sock = None
        with self.state_lock:
            self.connection = "disconnected"
            self.gateway = None
            self.route = None

    def _connect(self):
        self._close()
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(10)
        self.stream = self.sock.makefile("rwb", buffering=0)
        with self.state_lock:
            self.connection = "gateway"
        try:
            route = self._exchange("@location any")
        except GatewayResponseError:
            route = None
        self.sock.settimeout(None)
        with self.state_lock:
            self.route = route
            if route:
                self.gateway = route.get("gateway")
                self.connection = "ready"

    def _exchange(self, command: str):
        self.stream.write(command.encode("utf-8") + b"\n")
        response = self.stream.readline()
        if not response:
            raise ConnectionError("gateway closed the connection")
        body = json.loads(response)
        if body.get("error"):
            raise GatewayResponseError(body["error"])
        return body

    def exchange(self, command: str):
        with self.lock:
            try:
                if self.stream is None:
                    self._connect()
                body = self._exchange(command)
            except GatewayResponseError:
                with self.state_lock:
                    self.connection = "gateway"
                    self.route = None
                raise
            except (OSError, ValueError, ConnectionError):
                self._close()
                try:
                    self._connect()
                    body = self._exchange(command)
                except (OSError, ValueError, ConnectionError):
                    self._close()
                    raise
            with self.state_lock:
                if body.get("gateway"):
                    self.gateway = body["gateway"]
                if body.get("server"):
                    self.route = body
                    self.connection = "ready"
            return body

    def move(self, client_uid: str, sequence: int, latitude: float, longitude: float):
        self._validate_uid(client_uid)
        if sequence < 0:
            raise ValueError("input sequence is invalid")
        body = self.exchange(f"@teleport {client_uid} {sequence} {latitude:.8f} {longitude:.8f}")
        with self.state_lock:
            self.latitude, self.longitude, self.route = latitude, longitude, body
            self.gateway = body.get("gateway")
            self.connection = "ready"
        return body

    def send_input(self, client_uid: str, sequence: int, x: float, y: float):
        self._validate_uid(client_uid)
        if sequence < 0 or not (-1 <= x <= 1 and -1 <= y <= 1):
            raise ValueError("input intent is invalid")
        body = self.exchange(f"@input {client_uid} {sequence} {x:.3f} {y:.3f}")
        with self.state_lock:
            self.latitude = body["client_latitude"]
            self.longitude = body["client_longitude"]
            self.route = body
        return body

    def increment(self, client_uid: str, operation_id: str):
        self._validate_uid(client_uid)
        self._validate_identifier(operation_id, "operation identifier")
        return self.exchange(f"@increment {client_uid} {operation_id}")

    def reconnect(self):
        with self.lock:
            self._connect()
            if self.latitude is not None:
                try:
                    route = self._exchange(f"@position {self.latitude:.8f} {self.longitude:.8f}")
                except GatewayResponseError:
                    route = None
                with self.state_lock:
                    self.route = route
                    if route:
                        self.gateway = route.get("gateway")
                        self.connection = "ready"
            with self.state_lock:
                return self.route

    def snapshot(self):
        with self.state_lock:
            return {
                "connection": self.connection,
                "connected": self.connection != "disconnected",
                "gateway": self.gateway,
                "latitude": self.latitude,
                "longitude": self.longitude,
                "route": self.route,
            }

    def close(self):
        with self.lock:
            self._close()

    @classmethod
    def _validate_uid(cls, client_uid: str):
        cls._validate_identifier(client_uid, "client identifier")

    @staticmethod
    def _validate_identifier(value: str, label: str):
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
        if not value or len(value) > 128 or set(value) - allowed:
            raise ValueError(f"{label} is invalid")

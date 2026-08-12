"""Persistent TCP client shared by interactive and headless clients."""

import json
import socket
import threading
import time


IO_TIMEOUT_SECONDS = 1.0


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
        self.latency_ms = None

    def _close(self):
        stream, sock = self.stream, self.sock
        self.stream = self.sock = None
        if stream:
            try:
                stream.close()
            except OSError:
                pass
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        with self.state_lock:
            self.connection = "disconnected"
            self.gateway = None
            self.route = None
            self.latency_ms = None

    def _connect(self):
        self._close()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=IO_TIMEOUT_SECONDS)
            self.sock.settimeout(IO_TIMEOUT_SECONDS)
            self.stream = self.sock.makefile("rwb", buffering=0)
            with self.state_lock:
                self.connection = "gateway"
            try:
                if self.latitude is None:
                    route = self._exchange("@location any")
                else:
                    route = self._exchange(f"@position {self.latitude:.8f} {self.longitude:.8f}")
            except GatewayResponseError:
                route = None
        except (OSError, ValueError, ConnectionError):
            self._close()
            raise
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
                elif self.gateway:
                    body["gateway"] = self.gateway
                if body.get("server"):
                    self.route = body
                    self.connection = "ready"
            return body

    def move(self, client_uid: str, sequence: int, latitude: float, longitude: float):
        self._validate_uid(client_uid)
        if sequence < 0:
            raise ValueError("input sequence is invalid")
        started = time.perf_counter()
        body = self.exchange(f"@teleport {client_uid} {sequence} {latitude:.8f} {longitude:.8f}")
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        with self.state_lock:
            self.latitude, self.longitude, self.route = latitude, longitude, body
            self.gateway = body.get("gateway")
            self.connection = "ready"
            self.latency_ms = latency_ms
            body["latency_ms"] = latency_ms
        return body

    def send_input(
        self, client_uid: str, sequence: int, x: float, y: float, zoom: float,
        server_relevance: bool = True, spatial_relevance: bool = True,
        cross_server_relevance: bool = True,
    ):
        self._validate_uid(client_uid)
        if sequence < 0 or not (-1 <= x <= 1 and -1 <= y <= 1 and 2 <= zoom <= 18):
            raise ValueError("input intent is invalid")
        server_relevance = bool(server_relevance)
        spatial_relevance = bool(spatial_relevance)
        cross_server_relevance = bool(cross_server_relevance)
        started = time.perf_counter()
        body = self.exchange(
            f"@input {client_uid} {sequence} {x:.3f} {y:.3f} {zoom:.2f} "
            f"{str(server_relevance).lower()} {str(spatial_relevance).lower()} "
            f"{str(cross_server_relevance).lower()}"
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        with self.state_lock:
            self.latitude = body["client_latitude"]
            self.longitude = body["client_longitude"]
            self.route = body
            self.latency_ms = latency_ms
            body["latency_ms"] = latency_ms
        return body

    def reconnect(self):
        with self.lock:
            try:
                started = time.perf_counter()
                self._connect()
                with self.state_lock:
                    self.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                    if self.route:
                        self.route["latency_ms"] = self.latency_ms
                    return self.route
            except (OSError, ValueError, ConnectionError):
                self._close()
                raise

    def snapshot(self):
        with self.state_lock:
            return {
                "connection": self.connection,
                "connected": self.connection != "disconnected",
                "gateway": self.gateway,
                "latitude": self.latitude,
                "longitude": self.longitude,
                "latency_ms": self.latency_ms,
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

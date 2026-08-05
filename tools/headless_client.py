"""Autonomous headless client using the same TCP session as the browser client."""

import math
import random
import time
import uuid

try:
    from gateway_client import GatewayClient, GatewayResponseError
except ModuleNotFoundError:
    from tools.gateway_client import GatewayClient, GatewayResponseError


INPUT_INTERVAL = 0.025
CONNECTION_STATES = {"disconnected": 0, "gateway": 1, "ready": 2}


class HeadlessClient:
    def __init__(self, host: str, port: int, stopped, shared_status=None):
        self.uid = f"bot:{uuid.uuid4()}"
        self.client = GatewayClient(host, port)
        self.stopped = stopped
        self.shared_status = shared_status
        self.last_status = None

    def connection_state(self) -> str:
        state = self.client.snapshot()["connection"]
        if self.shared_status is not None and state != self.last_status:
            self.shared_status.value = CONNECTION_STATES.get(state, 0)
            self.last_status = state
        return state

    def run(self):
        sequence = 0
        rng = random.Random(self.uid)
        angle = rng.random() * math.tau
        axis_x, axis_y = math.cos(angle), math.sin(angle)
        started_at = time.monotonic()
        next_direction = started_at + rng.random() * 3
        next_counter = started_at + rng.random()
        next_reconnect = started_at
        pending_operation = None
        self.stopped.wait(rng.random() * INPUT_INTERVAL)
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                if self.connection_state() != "ready":
                    if started >= next_reconnect:
                        try:
                            self.client.reconnect()
                        except (GatewayResponseError, OSError, ValueError, ConnectionError):
                            pass
                        next_reconnect = time.monotonic() + 0.75 + rng.random() * 0.5
                    self.stopped.wait(INPUT_INTERVAL)
                    continue
                if started >= next_direction:
                    angle = rng.random() * math.tau
                    axis_x, axis_y = math.cos(angle), math.sin(angle)
                    next_direction = started + 3
                if started >= next_counter:
                    if pending_operation is None:
                        pending_operation = f"bot-op:{uuid.uuid4()}"
                    try:
                        self.client.increment(self.uid, pending_operation)
                        pending_operation = None
                    except (GatewayResponseError, OSError, ValueError, ConnectionError):
                        pass
                    next_counter = time.monotonic() + 1
                if self.connection_state() != "ready":
                    continue
                sequence += 1
                try:
                    self.client.send_input(self.uid, sequence, axis_x, axis_y)
                except (GatewayResponseError, OSError, ValueError, ConnectionError):
                    pass
                self.stopped.wait(max(0, INPUT_INTERVAL - (time.monotonic() - started)))
        finally:
            self.client.close()
            if self.shared_status is not None:
                self.shared_status.value = CONNECTION_STATES["disconnected"]


def run_headless(host: str, port: int, stopped, shared_status) -> None:
    HeadlessClient(host, port, stopped, shared_status).run()

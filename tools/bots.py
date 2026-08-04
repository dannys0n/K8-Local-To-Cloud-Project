"""Dashboard-owned deterministic dummy clients."""

import math
import random
import socket
import threading
import time
import uuid

try:
    from client import GatewayClient, GatewayResponseError
except ModuleNotFoundError:
    from tools.client import GatewayClient, GatewayResponseError


class Bot:
    def __init__(self, host: str, port: int):
        self.uid = f"bot:{uuid.uuid4()}"
        self.client = GatewayClient(host, port)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name=self.uid, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        sock = self.client.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        sequence = 0
        axis_x = axis_y = 0.0
        rng = random.Random(self.uid)
        next_direction = time.monotonic()
        next_counter = next_direction + rng.random()
        next_reconnect = next_direction
        pending_operation = None
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                if self.client.snapshot()["connection"] != "ready":
                    if started >= next_reconnect:
                        try:
                            self.client.reconnect()
                        except (GatewayResponseError, OSError, ValueError, ConnectionError):
                            pass
                        next_reconnect = time.monotonic() + 0.75 + rng.random() * 0.5
                    self.stopped.wait(0.05)
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
                if self.client.snapshot()["connection"] != "ready":
                    continue
                sequence += 1
                try:
                    self.client.send_input(self.uid, sequence, axis_x, axis_y)
                except (GatewayResponseError, OSError, ValueError, ConnectionError):
                    pass
                self.stopped.wait(max(0, 0.05 - (time.monotonic() - started)))
        finally:
            self.client.close()


class BotManager:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.next_batch = 1
        self.batches: dict[int, list[Bot]] = {}

    def spawn(self, count: int) -> dict:
        if count < 1 or count > 500:
            raise ValueError("batch size must be between 1 and 500")
        bots = [Bot(self.host, self.port) for _ in range(count)]
        with self.lock:
            batch_id = self.next_batch
            self.next_batch += 1
            self.batches[batch_id] = bots
        for bot in bots:
            bot.start()
        return self.snapshot()

    def despawn(self, batch_id: int) -> dict:
        with self.lock:
            bots = self.batches.pop(batch_id, None)
        if bots is None:
            raise ValueError("batch not found")
        for bot in bots:
            bot.stop()
        return self.snapshot()

    def despawn_all(self) -> dict:
        with self.lock:
            batches = list(self.batches.values())
            self.batches.clear()
        for bots in batches:
            for bot in bots:
                bot.stop()
        return self.snapshot()

    def snapshot(self) -> dict:
        with self.lock:
            batches = [{"id": batch_id, "count": len(bots)} for batch_id, bots in self.batches.items()]
            bots = [bot for batch in self.batches.values() for bot in batch]
        states = {"ready": 0, "gateway": 0, "disconnected": 0}
        for bot in bots:
            connection = bot.client.snapshot()["connection"]
            states[connection if connection in states else "disconnected"] += 1
        return {"total": len(bots), "states": states, "batches": batches}

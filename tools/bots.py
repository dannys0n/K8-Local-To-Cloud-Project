"""Lifecycle manager for dashboard-created headless clients."""

import multiprocessing
import threading
import time

try:
    from headless_client import run_headless
except ModuleNotFoundError:
    from tools.headless_client import run_headless


CONNECTION_NAMES = {0: "disconnected", 1: "gateway", 2: "ready"}


class HeadlessProcess:
    def __init__(self, context, host: str, port: int):
        self.stopped = context.Event()
        self.status = context.Value("b", 0)
        self.process = context.Process(
            target=run_headless,
            args=(host, port, self.stopped, self.status),
            daemon=True,
        )

    def start(self) -> None:
        self.process.start()

    def request_stop(self) -> None:
        self.stopped.set()

    def connection_state(self) -> str:
        if not self.process.is_alive():
            return "disconnected"
        return CONNECTION_NAMES.get(self.status.value, "disconnected")


class BotManager:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.context = multiprocessing.get_context("spawn")
        self.lock = threading.Lock()
        self.next_batch = 1
        self.batches: dict[int, list[HeadlessProcess]] = {}

    def spawn(self, count: int) -> dict:
        if count < 1 or count > 500:
            raise ValueError("batch size must be between 1 and 500")
        bots = [HeadlessProcess(self.context, self.host, self.port) for _ in range(count)]
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
        self.stop_all(bots)
        return self.snapshot()

    def despawn_all(self) -> dict:
        with self.lock:
            batches = list(self.batches.values())
            self.batches.clear()
        self.stop_all([bot for bots in batches for bot in bots])
        return self.snapshot()

    @staticmethod
    def stop_all(bots: list[HeadlessProcess]) -> None:
        for bot in bots:
            bot.request_stop()
        deadline = time.monotonic() + 1
        for bot in bots:
            bot.process.join(timeout=max(0, deadline - time.monotonic()))
        for bot in bots:
            if bot.process.is_alive():
                bot.process.terminate()
        for bot in bots:
            bot.process.join(timeout=0.2)

    def snapshot(self) -> dict:
        with self.lock:
            batches = [{"id": batch_id, "count": len(bots)} for batch_id, bots in self.batches.items()]
            bots = [bot for batch in self.batches.values() for bot in batch]
        states = {"ready": 0, "gateway": 0, "disconnected": 0}
        for bot in bots:
            connection = bot.connection_state()
            states[connection if connection in states else "disconnected"] += 1
        return {"total": len(bots), "states": states, "batches": batches}

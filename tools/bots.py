"""Lifecycle manager for containerized Go headless-client batches."""

import json
import os
import subprocess
import threading


IMAGE = "tcp-loadgen:dev"


class HeadlessBatch:
    def __init__(self, batch_id: int, count: int, host: str, port: int):
        self.id = batch_id
        self.count = count
        self.name = f"tcp-loadgen-{os.getpid()}-{batch_id}"
        self.states = {"ready": 0, "gateway": 0, "disconnected": count}
        self.lock = threading.Lock()
        target = "host.docker.internal" if host in {"127.0.0.1", "localhost"} else host
        command = [
            "docker", "run", "--rm", "--name", self.name,
            "--add-host", "host.docker.internal:host-gateway", IMAGE,
            "--host", target, "--port", str(port), "--clients", str(count),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=flags,
        )
        threading.Thread(target=self._read_status, daemon=True).start()

    def _read_status(self) -> None:
        for line in self.process.stdout or ():
            try:
                report = json.loads(line)
                states = report.get("states", {})
                if all(name in states for name in self.states):
                    with self.lock:
                        self.states = {name: int(states[name]) for name in self.states}
            except (ValueError, TypeError):
                continue
        with self.lock:
            self.states = {"ready": 0, "gateway": 0, "disconnected": self.count}

    def stop(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.run(
            ["docker", "stop", "--timeout", "2", self.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, timeout=5, check=False,
        )
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()

    def snapshot(self) -> dict:
        with self.lock:
            states = dict(self.states)
        if self.process.poll() is not None:
            states = {"ready": 0, "gateway": 0, "disconnected": self.count}
        return {"id": self.id, "count": self.count, "states": states}


class BotManager:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.next_batch = 1
        self.batches: dict[int, HeadlessBatch] = {}

    def spawn(self, count: int) -> dict:
        if count < 1 or count > 500:
            raise ValueError("batch size must be between 1 and 500")
        if subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode:
            raise ValueError(f"{IMAGE} is missing; run the cluster up script first")
        with self.lock:
            batch_id = self.next_batch
            self.next_batch += 1
        try:
            batch = HeadlessBatch(batch_id, count, self.host, self.port)
        except OSError as error:
            raise ValueError(f"could not start load generator: {error}") from error
        with self.lock:
            self.batches[batch_id] = batch
        return self.snapshot()

    def despawn(self, batch_id: int) -> dict:
        with self.lock:
            batch = self.batches.pop(batch_id, None)
        if batch is None:
            raise ValueError("batch not found")
        batch.stop()
        return self.snapshot()

    def despawn_all(self) -> dict:
        with self.lock:
            batches = list(self.batches.values())
            self.batches.clear()
        for batch in batches:
            batch.stop()
        return self.snapshot()

    def snapshot(self) -> dict:
        with self.lock:
            batches = [batch.snapshot() for batch in self.batches.values()]
        states = {"ready": 0, "gateway": 0, "disconnected": 0}
        for batch in batches:
            for name in states:
                states[name] += batch["states"][name]
        return {
            "total": sum(batch["count"] for batch in batches),
            "states": states,
            "batches": [{"id": batch["id"], "count": batch["count"]} for batch in batches],
        }

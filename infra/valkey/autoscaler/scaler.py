#!/usr/bin/env python3
"""Kind-only CPU policy for an operator-managed ValkeyCluster."""

import json
import math
import os
import socket
import ssl
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class ShardScaler:
    def __init__(self) -> None:
        self.namespace = os.getenv("POD_NAMESPACE", "tcp-lab")
        self.cluster = os.getenv("VALKEY_CLUSTER", "tcp-lab")
        self.valkey_address = os.getenv("VALKEY_ADDRESS", "valkey:6379")
        self.valkey_container = os.getenv("VALKEY_CONTAINER", "server")
        self.prometheus = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
        self.cpu_request = float(os.getenv("CPU_REQUEST_MILLICORES", "50"))
        self.scale_up = float(os.getenv("SCALE_UP_PERCENT", "70"))
        self.scale_down = float(os.getenv("SCALE_DOWN_PERCENT", "20"))
        self.required_breaches = int(os.getenv("REQUIRED_BREACHES", "2"))
        self.interval = float(os.getenv("EVALUATION_INTERVAL_SECONDS", "5"))
        self.cooldown = float(os.getenv("SCALE_COOLDOWN_SECONDS", "60"))
        self.min_shards = int(os.getenv("MIN_SHARDS", "3"))
        self.max_shards = int(os.getenv("MAX_SHARDS", "100"))
        self.minimum_scale_percent = float(os.getenv("MINIMUM_SCALE_OUT_PERCENT", "20"))
        self.up_breaches = 0
        self.down_breaches = 0
        self.cooldown_until = 0.0
        self.last_shards = None

        token = open(TOKEN_PATH, encoding="utf-8").read().strip()
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.api = f"https://{host}:{port}"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"}
        self.ssl_context = ssl.create_default_context(cafile=CA_PATH)

    def api_request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = Request(self.api + path, data=json.dumps(body).encode() if body is not None else None,
                          method=method, headers=self.headers)
        with urlopen(request, context=self.ssl_context, timeout=5) as response:
            return json.load(response)

    def valkey_cluster(self) -> dict:
        path = f"/apis/valkey.io/v1alpha1/namespaces/{self.namespace}/valkeyclusters/{self.cluster}"
        return self.api_request("GET", path)

    @staticmethod
    def is_ready(cluster: dict) -> bool:
        return any(condition.get("type") == "Ready" and condition.get("status") == "True"
                   for condition in cluster.get("status", {}).get("conditions", []))

    def set_shards(self, shards: int) -> None:
        path = f"/apis/valkey.io/v1alpha1/namespaces/{self.namespace}/valkeyclusters/{self.cluster}"
        self.api_request("PATCH", path, {"spec": {"shards": shards}})

    def primary_pods(self) -> set[str]:
        selector = urlencode({"labelSelector": f"valkey.io/cluster={self.cluster}"})
        path = f"/api/v1/namespaces/{self.namespace}/pods?{selector}"
        pods = {item.get("status", {}).get("podIP"): item["metadata"]["name"]
                for item in self.api_request("GET", path).get("items", [])
                if item.get("status", {}).get("podIP") and not item["metadata"].get("deletionTimestamp")}
        host, port = self.valkey_address.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=3) as connection:
            connection.sendall(b"*2\r\n$7\r\nCLUSTER\r\n$5\r\nNODES\r\n")
            response = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                response += chunk
                if len(response) >= 3 and response.startswith(b"$"):
                    header, _, body = response.partition(b"\r\n")
                    if len(body) >= int(header[1:]) + 2:
                        break
        _, _, body = response.partition(b"\r\n")
        primary_ips = set()
        for line in body.decode(errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 3 and "master" in fields[2].split(","):
                primary_ips.add(fields[1].split("@", 1)[0].rsplit(":", 1)[0])
        return {pods[ip] for ip in primary_ips if ip in pods}

    def primary_cpu(self, pod_names: set[str]) -> list[float]:
        query = ('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="' + self.namespace
                 + '",container="' + self.valkey_container + '"}[4s])) * 1000')
        url = f"{self.prometheus}/api/v1/query?{urlencode({'query': query})}"
        with urlopen(url, timeout=5) as response:
            payload = json.load(response)
        values = {sample["metric"].get("pod"): float(sample["value"][1]) / self.cpu_request * 100
                  for sample in payload.get("data", {}).get("result", [])}
        if not pod_names or any(name not in values for name in pod_names):
            raise RuntimeError("primary CPU metrics are incomplete")
        return [min(100.0, values[name]) for name in pod_names]

    def evaluate(self) -> None:
        cluster = self.valkey_cluster()
        shards = int(cluster["spec"]["shards"])
        if self.last_shards != shards:
            self.last_shards = shards
            self.up_breaches = self.down_breaches = 0
            self.cooldown_until = time.monotonic() + self.cooldown
        if not self.is_ready(cluster):
            raise RuntimeError("Valkey operator is reconciling; automatic scaling paused")
        values = self.primary_cpu(self.primary_pods())
        if len(values) != shards:
            raise RuntimeError(f"expected {shards} primary metrics, received {len(values)}")
        average = sum(values) / len(values)
        self.up_breaches = self.up_breaches + 1 if average >= self.scale_up else 0
        self.down_breaches = self.down_breaches + 1 if average < self.scale_down else 0
        print(json.dumps({"shards": shards, "primary_cpu_average": round(average, 2),
                          "up_breaches": self.up_breaches, "down_breaches": self.down_breaches,
                          "cooldown": max(0, round(self.cooldown_until - time.monotonic()))}), flush=True)
        if time.monotonic() < self.cooldown_until:
            return
        desired = shards
        if self.up_breaches >= self.required_breaches and shards < self.max_shards:
            proportional = math.ceil(shards * average / self.scale_up) - shards
            minimum = math.ceil(shards * self.minimum_scale_percent / 100)
            desired = min(self.max_shards, shards + max(minimum, proportional))
        elif self.down_breaches >= self.required_breaches and shards > self.min_shards:
            desired = shards - 1
        if desired != shards:
            self.set_shards(desired)
            self.up_breaches = self.down_breaches = 0
            self.cooldown_until = time.monotonic() + self.cooldown
            print(json.dumps({"event": "desired_shards_changed", "from": shards, "to": desired}), flush=True)

    def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.evaluate()
            except Exception as error:
                self.up_breaches = self.down_breaches = 0
                print(json.dumps({"error": str(error)}), flush=True)
            time.sleep(max(0.5, self.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    ShardScaler().run()

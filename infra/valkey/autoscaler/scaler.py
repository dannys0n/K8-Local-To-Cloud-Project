#!/usr/bin/env python3
"""Kind-only Valkey primary scale-out controller."""

import json
import math
import os
import ssl
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class PrimaryScaler:
    def __init__(self) -> None:
        self.namespace = os.getenv("POD_NAMESPACE", "tcp-lab")
        self.statefulset = os.getenv("STATEFULSET", "valkey")
        self.headless = os.getenv("HEADLESS_SERVICE", "valkey-headless")
        self.prometheus = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
        self.cpu_request = float(os.getenv("CPU_REQUEST_MILLICORES", "50"))
        self.threshold = float(os.getenv("CPU_THRESHOLD_PERCENT", "70"))
        self.required_breaches = int(os.getenv("REQUIRED_BREACHES", "3"))
        self.interval = float(os.getenv("EVALUATION_INTERVAL_SECONDS", "5"))
        self.cooldown = float(os.getenv("SCALE_OUT_COOLDOWN_SECONDS", "60"))
        self.max_primaries = int(os.getenv("MAX_PRIMARIES", "6"))
        self.minimum_scale_percent = float(os.getenv("MINIMUM_SCALE_OUT_PERCENT", "20"))
        self.seed = f"valkey-0.{self.headless}.{self.namespace}.svc.cluster.local:6379"
        self.password = os.getenv("VALKEY_PASSWORD", "")
        self.breaches = 0
        self.cooldown_until = 0.0
        self.last_node_count = None

        token = open(TOKEN_PATH, encoding="utf-8").read().strip()
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.api = f"https://{host}:{port}"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"}
        self.ssl_context = ssl.create_default_context(cafile=CA_PATH)

    def api_request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = Request(
            self.api + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers=self.headers,
        )
        with urlopen(request, context=self.ssl_context, timeout=5) as response:
            return json.load(response)

    def statefulset_replicas(self) -> int:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/statefulsets/{self.statefulset}/scale"
        return int(self.api_request("GET", path)["spec"]["replicas"])

    def set_statefulset_replicas(self, replicas: int) -> None:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/statefulsets/{self.statefulset}/scale"
        self.api_request("PATCH", path, {"spec": {"replicas": replicas}})

    def pods_by_ip(self) -> dict[str, str]:
        path = f"/api/v1/namespaces/{self.namespace}/pods?labelSelector=app%3Dvalkey"
        result = {}
        for pod in self.api_request("GET", path).get("items", []):
            ip = pod.get("status", {}).get("podIP")
            name = pod.get("metadata", {}).get("name")
            if ip and name:
                result[ip] = name
        return result

    def cli(self, *arguments: str, timeout: int = 180) -> str:
        command = ["valkey-cli"]
        if self.password:
            command.extend(["--no-auth-warning", "-a", self.password])
        command.extend(arguments)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "valkey-cli failed")
        return completed.stdout.strip()

    def cluster_nodes(self) -> list[dict]:
        output = self.cli("-h", self.seed.rsplit(":", 1)[0], "cluster", "nodes", timeout=10)
        nodes = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 8:
                continue
            host = fields[1].split("@", 1)[0].rsplit(":", 1)[0]
            flags = set(fields[2].split(","))
            nodes.append({"id": fields[0], "host": host, "flags": flags, "master": fields[3]})
        return nodes

    def primary_cpu(self, primary_pods: set[str]) -> list[float]:
        query = (
            'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="'
            + self.namespace + '",pod=~"valkey-[0-9]+",container="valkey"}[4s])) * 1000'
        )
        url = f"{self.prometheus}/api/v1/query?{urlencode({'query': query})}"
        with urlopen(url, timeout=5) as response:
            payload = json.load(response)
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus CPU query failed")
        return [
            float(sample["value"][1]) / self.cpu_request * 100
            for sample in payload["data"]["result"]
            if sample["metric"].get("pod") in primary_pods
        ]

    def wait_for_node(self, ordinal: int) -> str:
        host = f"{self.statefulset}-{ordinal}.{self.headless}.{self.namespace}.svc.cluster.local"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if self.cli("-h", host, "ping", timeout=3) == "PONG":
                    return host
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(f"{host} did not become ready")

    def wait_for_cluster_agreement(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                output = self.cli("--cluster", "check", self.seed, timeout=10)
                if "[OK] All 16384 slots covered" in output and "[ERR]" not in output:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("Valkey nodes did not agree on cluster membership")

    def scale_out(self, current_nodes: int, amount: int) -> None:
        self.set_statefulset_replicas(current_nodes + 2 * amount)
        added = []
        for offset in range(amount):
            primary_host = self.wait_for_node(current_nodes + 2 * offset)
            replica_host = self.wait_for_node(current_nodes + 2 * offset + 1)
            self.cli("--cluster", "add-node", f"{primary_host}:6379", self.seed, "--cluster-yes")
            primary_id = self.cli("-h", primary_host, "cluster", "myid", timeout=10)
            self.wait_for_cluster_agreement()
            self.cli(
                "--cluster", "add-node", f"{replica_host}:6379", self.seed,
                "--cluster-slave", "--cluster-master-id", primary_id, "--cluster-yes",
            )
            self.wait_for_cluster_agreement()
            added.append({"primary": primary_host, "replica": replica_host})
        self.cli("--cluster", "rebalance", self.seed, "--cluster-use-empty-masters", "--cluster-yes")

        info = self.cli("-h", self.seed.rsplit(":", 1)[0], "cluster", "info", timeout=10)
        if "cluster_state:ok" not in info or "cluster_slots_assigned:16384" not in info:
            raise RuntimeError("Valkey cluster validation failed after scale-out")
        self.cooldown_until = time.monotonic() + self.cooldown
        print(json.dumps({"event": "primaries_scaled", "amount": amount, "nodes": added}), flush=True)

    def evaluate(self) -> None:
        nodes = self.cluster_nodes()
        if self.last_node_count != len(nodes):
            self.last_node_count = len(nodes)
            self.breaches = 0
            self.cooldown_until = time.monotonic() + self.cooldown
        primaries = [node for node in nodes if "master" in node["flags"] and "fail" not in node["flags"]]
        total_pods = self.statefulset_replicas()
        if len(nodes) != total_pods or total_pods != len(primaries) * 2:
            self.breaches = 0
            raise RuntimeError("cluster membership is not one replica per primary; automatic scaling paused")
        pod_names = self.pods_by_ip()
        primary_pods = {pod_names[node["host"]] for node in primaries if node["host"] in pod_names}
        values = self.primary_cpu(primary_pods)
        if len(values) != len(primaries):
            self.breaches = 0
            raise RuntimeError("primary CPU metrics are incomplete")

        normalized = [min(100.0, value) for value in values]
        average = sum(normalized) / len(normalized)
        maximum = max(normalized, default=0)
        self.breaches = self.breaches + 1 if average >= self.threshold else 0
        print(json.dumps({
            "primaries": len(primaries), "primary_cpu_average": round(average, 2),
            "primary_cpu_max": round(maximum, 2),
            "breaches": self.breaches, "cooldown": max(0, round(self.cooldown_until - time.monotonic())),
        }), flush=True)
        if (
            self.breaches >= self.required_breaches
            and len(primaries) < self.max_primaries
            and time.monotonic() >= self.cooldown_until
        ):
            self.breaches = 0
            proportional = math.ceil(len(primaries) * average / self.threshold) - len(primaries)
            minimum = math.ceil(len(primaries) * self.minimum_scale_percent / 100)
            amount = min(self.max_primaries - len(primaries), max(minimum, proportional))
            self.scale_out(total_pods, amount)

    def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.evaluate()
            except Exception as error:
                print(json.dumps({"error": str(error)}), flush=True)
            time.sleep(max(0.5, self.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    PrimaryScaler().run()

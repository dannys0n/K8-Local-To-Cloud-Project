#!/usr/bin/env python3
"""Small Prometheus CPU scaler managed as a replaceable Kubernetes Deployment."""

import json
import math
import os
import ssl
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MIN_REPLICAS = 1
MAX_REPLICAS = 500
SCALE_UP_CPU_THRESHOLD = 80.0
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


def env_int(name: str, default: int) -> int:
    return max(1, int(os.getenv(name, str(default))))


class Scaler:
    def __init__(self) -> None:
        self.namespace = os.environ["POD_NAMESPACE"]
        self.target = os.environ["SCALE_TARGET"]
        self.pod_pattern = os.environ["TARGET_POD_PATTERN"]
        self.container = os.environ["TARGET_CONTAINER"]
        self.prometheus = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
        self.cpu_request = float(os.environ["CPU_REQUEST_MILLICORES"])
        self.cpu_window = os.getenv("CPU_RATE_WINDOW", "30s")
        self.interval = float(os.getenv("EVALUATION_INTERVAL_SECONDS", "15"))
        self.down_threshold = float(os.getenv("SCALE_DOWN_CPU_THRESHOLD", "20"))
        self.down_evaluations = env_int("SCALE_DOWN_STABILIZATION_EVALUATIONS", 20)
        self.max_change_pods = env_int("MAX_SCALE_CHANGE_PODS", 1)
        self.max_change_percent = env_int("MAX_SCALE_CHANGE_PERCENT", 25)
        self.manage_locations = os.getenv("MANAGE_LOCATIONS") == "true"
        self.low_cpu_count = 0
        self.token = open(TOKEN_PATH, encoding="utf-8").read().strip()
        self.api = os.getenv("KUBERNETES_SERVICE_HOST")
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.api = f"https://{self.api}:{port}"
        self.ssl_context = ssl.create_default_context(cafile=CA_PATH)

    def api_request(self, method: str, path: str, body: dict | None = None) -> dict:
        encoded = json.dumps(body).encode() if body is not None else None
        request = Request(
            self.api + path, data=encoded, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/merge-patch+json"},
        )
        with urlopen(request, context=self.ssl_context, timeout=2) as response:
            return json.load(response)

    def replicas(self) -> int:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{self.target}/scale"
        return int(self.api_request("GET", path)["spec"]["replicas"])

    def set_replicas(self, replicas: int) -> None:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{self.target}/scale"
        self.api_request("PATCH", path, {"spec": {"replicas": replicas}})

    def utilizations(self) -> list[float]:
        query = (
            "sum by (pod) (rate(container_cpu_usage_seconds_total{"
            f'namespace="{self.namespace}",pod=~"{self.pod_pattern}",container="{self.container}"'
            f"}}[{self.cpu_window}])) * 1000"
        )
        url = f"{self.prometheus}/api/v1/query?{urlencode({'query': query})}"
        with urlopen(url, timeout=2) as response:
            payload = json.load(response)
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus CPU query failed")
        return [float(sample["value"][1]) / self.cpu_request * 100 for sample in payload["data"]["result"]]

    def maximum_change(self, current: int) -> int:
        return max(self.max_change_pods, math.ceil(current * self.max_change_percent / 100))

    def desired_replicas(self, current: int, values: list[float]) -> int:
        if len(values) != current:
            self.low_cpu_count = 0
            return current
        required = max(MIN_REPLICAS, math.ceil(sum(values) / SCALE_UP_CPU_THRESHOLD))
        average = sum(values) / len(values)
        self.low_cpu_count = self.low_cpu_count + 1 if average <= self.down_threshold else 0
        change = self.maximum_change(current)
        if required > current:
            self.low_cpu_count = 0
            return min(MAX_REPLICAS, current + change, required)
        if current > MIN_REPLICAS and self.low_cpu_count >= self.down_evaluations:
            self.low_cpu_count = 0
            return max(MIN_REPLICAS, current - change, required)
        return current

    def reconcile_locations(self, replicas: int) -> None:
        if not self.manage_locations:
            return
        dsn = os.environ["POSTGRES_DSN"]
        count = int(subprocess.check_output(
            ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-Atc", "SELECT COUNT(*) FROM tcp_server_state WHERE enabled"],
            text=True,
        ).strip())
        difference = replicas - count
        if difference == 0:
            return
        function = "tcp_create_location" if difference > 0 else "tcp_retire_location"
        subprocess.run(
            ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-Atc", f"SELECT {function}() FROM generate_series(1, {abs(difference)})"],
            check=True,
        )

    def run(self) -> None:
        needs_reconcile = self.manage_locations
        while True:
            started = time.monotonic()
            try:
                current = self.replicas()
                if needs_reconcile:
                    self.reconcile_locations(current)
                    needs_reconcile = False
                values = self.utilizations()
                desired = self.desired_replicas(current, values)
                if desired != current:
                    self.set_replicas(desired)
                    needs_reconcile = self.manage_locations
                    self.reconcile_locations(desired)
                    needs_reconcile = False
                    print(json.dumps({"target": self.target, "current": current, "desired": desired}), flush=True)
            except Exception as error:
                print(json.dumps({"target": self.target, "error": str(error)}), flush=True)
            time.sleep(max(0.1, self.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    Scaler().run()

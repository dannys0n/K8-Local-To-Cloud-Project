#!/usr/bin/env python3
"""Small Prometheus CPU scaler managed as a replaceable Kubernetes Deployment."""

import json
import math
import os
import random
import ssl
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from redis.cluster import RedisCluster
from redis.exceptions import RedisError


MIN_REPLICAS = 1
MAX_REPLICAS = 500
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
        self.up_threshold = float(os.getenv("SCALE_UP_CPU_THRESHOLD", "80"))
        self.down_threshold = float(os.getenv("SCALE_DOWN_CPU_THRESHOLD", "20"))
        self.down_evaluations = env_int("SCALE_DOWN_STABILIZATION_EVALUATIONS", 20)
        self.max_change_pods = env_int("MAX_SCALE_CHANGE_PODS", 1)
        self.max_change_percent = env_int("MAX_SCALE_CHANGE_PERCENT", 25)
        self.manage_locations = os.getenv("MANAGE_LOCATIONS") == "true"
        self.valkey = self.connect_valkey() if self.manage_locations else None
        self.low_cpu_count = 0
        self.incomplete_cpu_count = 0
        self.last_replica_count = None
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
        if current != self.last_replica_count:
            self.low_cpu_count = 0
            self.incomplete_cpu_count = 0
            self.last_replica_count = current

        if not values:
            self.incomplete_cpu_count += 1
            if self.incomplete_cpu_count >= self.down_evaluations:
                self.low_cpu_count = 0
            return current

        required = max(MIN_REPLICAS, math.ceil(sum(values) / self.up_threshold))
        change = self.maximum_change(current)
        if len(values) <= current and required > current:
            self.low_cpu_count = 0
            self.incomplete_cpu_count = 0
            return min(MAX_REPLICAS, current + change, required)

        if len(values) != current:
            self.incomplete_cpu_count += 1
            if self.incomplete_cpu_count >= self.down_evaluations:
                self.low_cpu_count = 0
            return current

        self.incomplete_cpu_count = 0
        average = sum(values) / len(values)
        self.low_cpu_count = self.low_cpu_count + 1 if average <= self.down_threshold else 0
        if current > MIN_REPLICAS and self.low_cpu_count >= self.down_evaluations:
            self.low_cpu_count = 0
            return max(MIN_REPLICAS, current - change, required)
        return current

    def connect_valkey(self) -> RedisCluster:
        address = os.environ["VALKEY_ADDRS"].split(",", 1)[0].strip()
        host, port = address.rsplit(":", 1)
        deadline = time.monotonic() + 90
        while True:
            client = None
            try:
                client = RedisCluster(
                    host=host, port=int(port), decode_responses=True,
                    username=os.getenv("VALKEY_USERNAME") or None,
                    password=os.getenv("VALKEY_PASSWORD") or None,
                    ssl=os.getenv("VALKEY_TLS", "false").lower() == "true",
                    socket_connect_timeout=2, socket_timeout=2,
                )
                if client.cluster_info().get("cluster_state") == "ok":
                    return client
                client.close()
            except Exception as error:  # Startup may precede cluster slot assignment.
                if client is not None:
                    client.close()
                print(f"waiting for valkey cluster: {error}", flush=True)
            if time.monotonic() >= deadline:
                raise RuntimeError("valkey cluster did not become ready within 90 seconds")
            time.sleep(1)

    def reconnect_valkey(self) -> None:
        if self.valkey is not None:
            self.valkey.close()
        self.valkey = self.connect_valkey()

    @staticmethod
    def location_key(location_id: int) -> str:
        return f"tcp-lab:location:{{{location_id}}}"

    def publish_topology_change(self, action: str, location_id: int) -> None:
        revision = self.valkey.incr("tcp-lab:topology:revision")
        self.valkey.publish("tcp-lab:topology:changed", json.dumps({
            "revision": revision, "action": action, "location_id": location_id,
        }))

    def create_location(self) -> None:
        location_id = int(self.valkey.incr("tcp-lab:location:counter"))
        server_id = "tcp-server-0" if location_id == 1 else f"tcp-server-location-{location_id}"
        self.valkey.hset(self.location_key(location_id), mapping={
            "server_id": server_id,
            "location_id": location_id,
            "latitude": random.uniform(-85.05112878, 85.05112878),
            "longitude": random.uniform(-180, 180),
            "enabled": 1, "owner": "", "generation": 0, "lease_until": 0,
        })
        self.valkey.zadd("tcp-lab:locations", {str(location_id): location_id})
        self.publish_topology_change("created", location_id)

    def retire_location(self) -> None:
        retired = self.valkey.zpopmax("tcp-lab:locations", 1)
        if not retired:
            return
        location_id = int(retired[0][0])
        if location_id == 1:
            self.valkey.zadd("tcp-lab:locations", {"1": 1})
            return
        self.valkey.hset(self.location_key(location_id), mapping={
            "enabled": 0, "owner": "", "lease_until": 0,
        })
        self.valkey.delete(f"tcp-lab:route:{{{location_id}}}")
        self.publish_topology_change("retired", location_id)

    def reconcile_locations(self, replicas: int) -> None:
        if not self.manage_locations:
            return
        count = int(self.valkey.zcard("tcp-lab:locations"))
        difference = replicas - count
        for _ in range(max(0, difference)):
            self.create_location()
        for _ in range(max(0, -difference)):
            self.retire_location()
        if difference == 0:
            self.publish_topology_change("reconciled", 0)

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
            except RedisError as error:
                needs_reconcile = self.manage_locations
                print(json.dumps({"target": self.target, "error": str(error),
                                  "action": "reconnecting valkey"}), flush=True)
                try:
                    self.reconnect_valkey()
                except Exception as reconnect_error:
                    print(json.dumps({"target": self.target,
                                      "error": f"valkey reconnect failed: {reconnect_error}"}), flush=True)
            except Exception as error:
                print(json.dumps({"target": self.target, "error": str(error)}), flush=True)
            time.sleep(max(0.1, self.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    Scaler().run()

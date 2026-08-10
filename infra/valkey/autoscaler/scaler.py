#!/usr/bin/env python3
"""Kind-only Valkey primary-shard autoscaler."""

import json
import math
import os
import ssl
import subprocess
import time
from collections import Counter
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class ClusterScaler:
    def __init__(self) -> None:
        self.namespace = os.getenv("POD_NAMESPACE", "tcp-lab")
        self.statefulset = os.getenv("STATEFULSET", "valkey")
        self.headless = os.getenv("HEADLESS_SERVICE", "valkey-headless")
        self.prometheus = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
        self.cpu_request = float(os.getenv("CPU_REQUEST_MILLICORES", "50"))
        self.primary_up = float(os.getenv("PRIMARY_SCALE_UP_PERCENT", "70"))
        self.primary_down = float(os.getenv("PRIMARY_SCALE_DOWN_PERCENT", "20"))
        self.required_breaches = int(os.getenv("REQUIRED_BREACHES", "2"))
        self.interval = float(os.getenv("EVALUATION_INTERVAL_SECONDS", "5"))
        self.cooldown = float(os.getenv("SCALE_COOLDOWN_SECONDS", "60"))
        self.min_primaries = int(os.getenv("MIN_PRIMARIES", "3"))
        self.max_primaries = int(os.getenv("MAX_PRIMARIES", "6"))
        self.minimum_scale_percent = float(os.getenv("MINIMUM_SCALE_OUT_PERCENT", "20"))
        self.seed_host = f"valkey-0.{self.headless}.{self.namespace}.svc.cluster.local"
        self.password = os.getenv("VALKEY_PASSWORD", "")
        self.breaches = Counter()
        self.cooldown_until = 0.0

        token = open(TOKEN_PATH, encoding="utf-8").read().strip()
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.api = f"https://{host}:{port}"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"}
        self.ssl_context = ssl.create_default_context(cafile=CA_PATH)

    def api_request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = Request(self.api + path, data=json.dumps(body).encode() if body else None,
                          method=method, headers=self.headers)
        with urlopen(request, context=self.ssl_context, timeout=5) as response:
            return json.load(response)

    def statefulset_replicas(self) -> int:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/statefulsets/{self.statefulset}/scale"
        return int(self.api_request("GET", path)["spec"]["replicas"])

    def set_statefulset_replicas(self, replicas: int) -> None:
        path = f"/apis/apps/v1/namespaces/{self.namespace}/statefulsets/{self.statefulset}/scale"
        self.api_request("PATCH", path, {"spec": {"replicas": replicas}})

    def pods(self) -> list[dict]:
        path = f"/api/v1/namespaces/{self.namespace}/pods?labelSelector=app%3Dvalkey"
        pods = []
        for pod in self.api_request("GET", path).get("items", []):
            metadata, status, spec = pod["metadata"], pod.get("status", {}), pod.get("spec", {})
            name, ip, node = metadata["name"], status.get("podIP"), spec.get("nodeName")
            if not (name.startswith(f"{self.statefulset}-") and ip and node):
                continue
            ready = any(item["type"] == "Ready" and item["status"] == "True"
                        for item in status.get("conditions", []))
            pods.append({"name": name, "ordinal": int(name.rsplit("-", 1)[1]), "ip": ip,
                         "node": node, "ready": ready,
                         "host": f"{name}.{self.headless}.{self.namespace}.svc.cluster.local"})
        return sorted(pods, key=lambda item: item["ordinal"])

    def cli(self, *arguments: str, timeout: int = 180) -> str:
        command = ["valkey-cli"]
        if self.password:
            command.extend(["--no-auth-warning", "-a", self.password])
        command.extend(arguments)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "valkey-cli failed")
        return completed.stdout.strip()

    @staticmethod
    def slot_count(tokens: list[str]) -> int:
        total = 0
        for token in tokens:
            if token.startswith("["):
                continue
            if "-" in token:
                first, last = token.split("-", 1)
                if first.isdigit() and last.isdigit():
                    total += int(last) - int(first) + 1
            elif token.isdigit():
                total += 1
        return total

    def cluster_nodes(self) -> list[dict]:
        output = self.cli("-h", self.seed_host, "cluster", "nodes", timeout=10)
        nodes = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 8:
                continue
            host = fields[1].split("@", 1)[0].rsplit(":", 1)[0]
            nodes.append({"id": fields[0], "host": host, "flags": set(fields[2].split(",")),
                          "master": fields[3], "slots": self.slot_count(fields[8:])})
        return nodes

    def wait_for_pods(self, expected: int) -> list[dict]:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pods = self.pods()
            if len(pods) == expected and all(pod["ready"] for pod in pods):
                return pods
            time.sleep(1)
        raise RuntimeError(f"only {len(self.pods())}/{expected} Valkey pods became ready")

    def wait_for_cluster(self) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                info = self.cli("-h", self.seed_host, "cluster", "info", timeout=10)
                if "cluster_state:ok" in info and "cluster_slots_assigned:16384" in info:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("Valkey cluster did not become healthy")

    @staticmethod
    def safe_pairs(candidates: list[dict]) -> list[tuple[dict, dict]]:
        ordered = sorted(candidates, key=lambda pod: (pod["node"], pod["ordinal"]))
        half = len(ordered) // 2
        if len(ordered) % 2 or not half or max(Counter(pod["node"] for pod in ordered).values()) > half:
            raise RuntimeError("pods cannot be paired across database workers")
        pairs = list(zip(ordered[:half], ordered[half:]))
        if any(primary["node"] == replica["node"] for primary, replica in pairs):
            raise RuntimeError("scheduler did not provide failure-domain-safe replica candidates")
        return pairs

    def initialize(self, pods: list[dict]) -> None:
        if len(pods) < self.min_primaries * 2:
            return
        pairs = self.safe_pairs(pods[:self.min_primaries * 2])
        primaries = " ".join(f"{primary['host']}:6379" for primary, _ in pairs)
        self.cli("--cluster", "create", *primaries.split(), "--cluster-replicas", "0", "--cluster-yes")
        self.wait_for_cluster()
        for primary, replica in pairs:
            primary_id = self.cli("-h", primary["host"], "cluster", "myid", timeout=10)
            self.cli("--cluster", "add-node", f"{replica['host']}:6379", f"{self.seed_host}:6379",
                     "--cluster-slave", "--cluster-master-id", primary_id, "--cluster-yes")
        self.wait_for_cluster()
        print(json.dumps({"event": "cluster_initialized", "primaries": len(pairs)}), flush=True)

    def members_with_pods(self, nodes: list[dict], pods: list[dict]) -> list[dict]:
        by_ip = {pod["ip"]: pod for pod in pods}
        result = []
        for member in nodes:
            pod = by_ip.get(member["host"])
            if pod:
                result.append({**member, **{"pod": pod}})
        return result

    def verify_replica_domains(self, members: list[dict]) -> None:
        primaries = {member["id"]: member for member in members if "master" in member["flags"]}
        counts = Counter()
        for replica in (member for member in members if "slave" in member["flags"]):
            primary = primaries.get(replica["master"])
            if not primary:
                raise RuntimeError(f"replica {replica['pod']['name']} has no known primary")
            if replica["pod"]["node"] == primary["pod"]["node"]:
                raise RuntimeError(
                    f"unsafe replica placement: {primary['pod']['name']} and {replica['pod']['name']} "
                    f"are both on {primary['pod']['node']}"
                )
            counts[primary["id"]] += 1
        invalid = [primary["pod"]["name"] for primary in primaries.values()
                   if counts[primary["id"]] != 1]
        if invalid:
            raise RuntimeError(f"primaries must have exactly one replica: {', '.join(invalid)}")

    def cpu(self, pod_names: set[str]) -> list[float]:
        query = ('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="' + self.namespace
                 + '",pod=~"valkey-[0-9]+",container="valkey"}[4s])) * 1000')
        url = f"{self.prometheus}/api/v1/query?{urlencode({'query': query})}"
        with urlopen(url, timeout=5) as response:
            payload = json.load(response)
        values = {sample["metric"].get("pod"): float(sample["value"][1]) / self.cpu_request * 100
                  for sample in payload.get("data", {}).get("result", [])}
        if any(name not in values for name in pod_names):
            raise RuntimeError("Valkey CPU metrics are incomplete")
        return [min(100.0, values[name]) for name in pod_names]

    def add_primary_pairs(self, amount: int, pods: list[dict]) -> None:
        old_count = len(pods)
        self.set_statefulset_replicas(old_count + 2 * amount)
        all_pods = self.wait_for_pods(old_count + 2 * amount)
        pairs = self.safe_pairs(all_pods[old_count:])
        added = []
        for primary, replica in pairs:
            self.cli("--cluster", "add-node", f"{primary['host']}:6379", f"{self.seed_host}:6379", "--cluster-yes")
            primary_id = self.cli("-h", primary["host"], "cluster", "myid", timeout=10)
            self.cli("--cluster", "add-node", f"{replica['host']}:6379", f"{self.seed_host}:6379",
                     "--cluster-slave", "--cluster-master-id", primary_id, "--cluster-yes")
            added.append({"primary": primary["name"], "replica": replica["name"]})
        self.cli("--cluster", "rebalance", f"{self.seed_host}:6379", "--cluster-use-empty-masters", "--cluster-yes")
        self.wait_for_cluster()
        self.scaled("primaries_scaled_up", amount, added)

    def wait_for_role(self, node_id: str, role: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            match = next((node for node in self.cluster_nodes() if node["id"] == node_id), None)
            if match and role in match["flags"]:
                return
            time.sleep(0.5)
        raise RuntimeError(f"node {node_id} did not become {role}")

    def promote(self, replica: dict) -> None:
        self.cli("-h", replica["pod"]["host"], "cluster", "failover", "takeover", timeout=10)
        self.wait_for_role(replica["id"], "master")

    def remove_highest(self, as_primary: bool) -> None:
        pods = self.wait_for_pods(self.statefulset_replicas())
        members = self.members_with_pods(self.cluster_nodes(), pods)
        target = next((member for member in members if member["pod"]["ordinal"] == pods[-1]["ordinal"]), None)
        if not target:
            raise RuntimeError("highest ordinal pod is not a cluster member")
        is_primary = "master" in target["flags"]
        if as_primary and not is_primary:
            self.promote(target)
            target = next(node for node in self.members_with_pods(self.cluster_nodes(), pods)
                          if node["id"] == target["id"])
        elif not as_primary and is_primary:
            replicas = [member for member in members if member["master"] == target["id"]
                        and member["pod"]["ordinal"] != target["pod"]["ordinal"]]
            if not replicas:
                raise RuntimeError(f"cannot remove {target['pod']['name']}: no replica can be promoted")
            self.promote(replicas[0])
            target = next(node for node in self.members_with_pods(self.cluster_nodes(), pods)
                          if node["id"] == target["id"])
        if as_primary:
            current = self.members_with_pods(self.cluster_nodes(), pods)
            primaries = [member for member in current
                         if "master" in member["flags"] and member["id"] != target["id"]]
            counts = Counter(member["master"] for member in current if "slave" in member["flags"])
            for replica in [member for member in current
                            if "slave" in member["flags"] and member["master"] == target["id"]]:
                choices = [primary for primary in primaries
                           if primary["pod"]["node"] != replica["pod"]["node"]]
                if not choices:
                    raise RuntimeError(f"no safe primary can adopt {replica['pod']['name']}")
                destination = min(choices, key=lambda primary: counts[primary["id"]])
                self.cli("-h", replica["pod"]["host"], "cluster", "replicate", destination["id"], timeout=10)
                counts[destination["id"]] += 1
            destination = min(primaries, key=lambda member: member["slots"])
            if target["slots"]:
                self.cli("--cluster", "reshard", f"{self.seed_host}:6379", "--cluster-from", target["id"],
                         "--cluster-to", destination["id"], "--cluster-slots", str(target["slots"]), "--cluster-yes")
        self.cli("--cluster", "del-node", f"{self.seed_host}:6379", target["id"], "--cluster-yes")
        self.set_statefulset_replicas(len(pods) - 1)
        self.wait_for_pods(len(pods) - 1)
        self.wait_for_cluster()

    def scale_down_primary_pair(self) -> None:
        self.remove_highest(as_primary=False)
        self.remove_highest(as_primary=True)
        self.cli("--cluster", "rebalance", f"{self.seed_host}:6379", "--cluster-yes")
        self.wait_for_cluster()
        self.scaled("primaries_scaled_down", 1)

    def scaled(self, event: str, amount: int, nodes=None) -> None:
        self.breaches.clear()
        self.cooldown_until = time.monotonic() + self.cooldown
        print(json.dumps({"event": event, "amount": amount, "nodes": nodes or []}), flush=True)

    def breach(self, key: str, condition: bool) -> bool:
        self.breaches[key] = self.breaches[key] + 1 if condition else 0
        return self.breaches[key] >= self.required_breaches

    def evaluate(self) -> None:
        pods = self.wait_for_pods(self.statefulset_replicas())
        nodes = self.cluster_nodes()
        if len(nodes) == 1 and nodes[0]["slots"] == 0:
            self.initialize(pods)
            return
        if len(nodes) != len(pods) or any("fail" in node["flags"] or "fail?" in node["flags"] for node in nodes):
            raise RuntimeError("cluster membership is incomplete or failed; automatic scaling paused")
        members = self.members_with_pods(nodes, pods)
        if len(members) != len(nodes):
            raise RuntimeError("cluster members do not match running pods")
        self.verify_replica_domains(members)
        primaries = [member for member in members if "master" in member["flags"]]
        replicas = [member for member in members if "slave" in member["flags"]]
        primary_values = self.cpu({member["pod"]["name"] for member in primaries})
        primary_average = sum(primary_values) / len(primary_values)
        print(json.dumps({"primaries": len(primaries), "replicas": len(replicas),
                          "primary_cpu_average": round(primary_average, 2),
                          "cooldown": max(0, round(self.cooldown_until - time.monotonic()))}), flush=True)
        if time.monotonic() < self.cooldown_until:
            return
        if self.breach("primary_up", primary_average >= self.primary_up) and len(primaries) < self.max_primaries:
            proportional = math.ceil(len(primaries) * primary_average / self.primary_up) - len(primaries)
            minimum = math.ceil(len(primaries) * self.minimum_scale_percent / 100)
            self.add_primary_pairs(min(self.max_primaries - len(primaries), max(minimum, proportional)), pods)
        elif self.breach("primary_down", primary_average < self.primary_down) and len(primaries) > self.min_primaries:
            self.scale_down_primary_pair()

    def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.evaluate()
            except Exception as error:
                self.breaches.clear()
                print(json.dumps({"error": str(error)}), flush=True)
            time.sleep(max(0.5, self.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    ClusterScaler().run()

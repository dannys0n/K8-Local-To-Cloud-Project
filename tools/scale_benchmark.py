#!/usr/bin/env python3
"""Repeatable pod, HPA, application-autoscaler, and node-capacity benchmarks."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".generated" / "scale-benchmarks"
NAMESPACE = "tcp-lab-benchmark"
DEPLOYMENT = "capacity-probe"
SELECTOR = "app.kubernetes.io/name=capacity-probe"
BASE = ROOT / "infra" / "benchmarks" / "capacity-probe" / "base"
AUTOSCALE = ROOT / "infra" / "benchmarks" / "capacity-probe" / "autoscale"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def duration(start: str | None, end: str | None) -> float | None:
    left, right = parse_time(start), parse_time(end)
    if left is None or right is None:
        return None
    return round(max(0.0, right - left), 3)


def command(args: list[str], *, stdin: str | None = None, check: bool = True,
            quiet: bool = False, timeout: float | None = None) -> str:
    result = subprocess.run(
        args, cwd=ROOT, input=stdin, text=True, capture_output=True,
        timeout=timeout, check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    if not quiet and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.stdout


def kubectl(*args: str, check: bool = True, timeout: float | None = None) -> str:
    return command(["kubectl", *args], check=check, quiet=True, timeout=timeout)


def kubectl_json(*args: str) -> dict[str, Any]:
    output = kubectl(*args, "-o", "json")
    return json.loads(output)


def context_environment(requested: str) -> tuple[str, str]:
    context = kubectl("config", "current-context").strip()
    detected = "kind" if context.startswith("kind-") else "eks" if "eks" in context.lower() or context.startswith("arn:aws:eks:") else "unknown"
    if requested != "auto" and requested != detected:
        raise RuntimeError(f"current context {context!r} looks like {detected}, not {requested}")
    return context, detected if requested == "auto" else requested


def condition_time(item: dict[str, Any], condition_type: str, status: str = "True") -> str | None:
    for condition in item.get("status", {}).get("conditions", []):
        if condition.get("type") == condition_type and condition.get("status") == status:
            return condition.get("lastTransitionTime")
    return None


def deployment_state(namespace: str, name: str) -> dict[str, int]:
    item = kubectl_json("get", "deployment", name, "-n", namespace)
    spec, status = item.get("spec", {}), item.get("status", {})
    return {
        "desired": int(spec.get("replicas", 0)),
        "current": int(status.get("replicas", 0)),
        "ready": int(status.get("readyReplicas", 0)),
        "available": int(status.get("availableReplicas", 0)),
    }


class Recorder:
    def __init__(self, mode: str, environment: str, context: str, parameters: dict[str, Any]):
        self.run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{mode}-{uuid.uuid4().hex[:6]}"
        self.data: dict[str, Any] = {
            "schema": 1,
            "run_id": self.run_id,
            "mode": mode,
            "environment": environment,
            "context": context,
            "started_at": utc_now(),
            "parameters": parameters,
            "events": [],
            "pods": {},
            "nodes": {},
            "deployment_samples": [],
            "hpa_samples": [],
            "result": "running",
        }
        self._seen: set[str] = set()
        self._last_deployment: tuple[int, ...] | None = None
        self._last_hpa: tuple[Any, ...] | None = None

    def event(self, event_type: str, **detail: Any) -> None:
        item = {"at": utc_now(), "type": event_type, **detail}
        self.data["events"].append(item)
        print(f"[{item['at']}] {event_type}" + (f" {detail}" if detail else ""), flush=True)

    def once(self, key: str, event_type: str, **detail: Any) -> None:
        if key not in self._seen:
            self._seen.add(key)
            self.event(event_type, **detail)

    def sample_cluster(self, namespace: str = NAMESPACE, deployment: str = DEPLOYMENT,
                       selector: str = SELECTOR, include_hpa: bool = True) -> tuple[int, int]:
        observed = utc_now()
        pods = kubectl_json("get", "pods", "-n", namespace, "-l", selector).get("items", [])
        live_names: set[str] = set()
        ready_count = 0
        for pod in pods:
            meta, spec, status = pod["metadata"], pod.get("spec", {}), pod.get("status", {})
            name, uid = meta["name"], meta["uid"]
            live_names.add(name)
            record = self.data["pods"].setdefault(name, {
                "uid": uid,
                "created_at": meta.get("creationTimestamp"),
                "node": spec.get("nodeName"),
                "scheduled_at": None,
                "started_at": None,
                "ready_at": None,
                "deletion_requested_at": meta.get("deletionTimestamp"),
                "removed_at": None,
                "restarts": 0,
            })
            record["node"] = spec.get("nodeName") or record.get("node")
            record["scheduled_at"] = condition_time(pod, "PodScheduled") or record.get("scheduled_at")
            statuses = status.get("containerStatuses", [])
            if statuses:
                running = statuses[0].get("state", {}).get("running", {})
                record["started_at"] = running.get("startedAt") or record.get("started_at")
                record["restarts"] = int(statuses[0].get("restartCount", 0))
            pod_ready = condition_time(pod, "Ready")
            if pod_ready:
                record["ready_at"] = pod_ready
                ready_count += 1
            if meta.get("deletionTimestamp"):
                record["deletion_requested_at"] = meta["deletionTimestamp"]
            for phase, value in (("created", record["created_at"]), ("scheduled", record["scheduled_at"]),
                                 ("started", record["started_at"]), ("ready", record["ready_at"]),
                                 ("deleting", record["deletion_requested_at"])):
                if value:
                    self.once(f"pod:{uid}:{phase}", f"pod_{phase}", pod=name, at_source=value,
                              node=record.get("node"))
        for name, record in self.data["pods"].items():
            if record["uid"] and name not in live_names and record.get("removed_at") is None:
                record["removed_at"] = observed
                self.once(f"pod:{record['uid']}:removed", "pod_removed", pod=name)

        nodes = kubectl_json("get", "nodes").get("items", [])
        for node in nodes:
            meta, status = node["metadata"], node.get("status", {})
            name, uid = meta["name"], meta["uid"]
            labels = meta.get("labels", {})
            ready_at = condition_time(node, "Ready")
            ready = ready_at is not None
            record = self.data["nodes"].setdefault(name, {
                "uid": uid,
                "created_at": meta.get("creationTimestamp"),
                "ready_at": ready_at,
                "first_observed_at": observed,
                "removed_at": None,
                "instance_type": labels.get("node.kubernetes.io/instance-type", ""),
                "zone": labels.get("topology.kubernetes.io/zone", ""),
                "capacity": status.get("capacity", {}),
                "allocatable": status.get("allocatable", {}),
            })
            if ready_at and not record.get("ready_at"):
                record["ready_at"] = ready_at
            self.once(f"node:{uid}:observed", "node_observed", node=name,
                      instance_type=record["instance_type"], ready=ready)
            if ready:
                self.once(f"node:{uid}:ready", "node_ready", node=name, at_source=ready_at)
        live_nodes = {node["metadata"]["name"] for node in nodes}
        for name, record in self.data["nodes"].items():
            if name not in live_nodes and record.get("removed_at") is None:
                record["removed_at"] = observed
                self.once(f"node:{record['uid']}:removed", "node_removed", node=name)

        state = deployment_state(namespace, deployment)
        key = (state["desired"], state["current"], state["ready"], state["available"])
        if key != self._last_deployment:
            self._last_deployment = key
            sample = {"at": observed, **state}
            self.data["deployment_samples"].append(sample)
            self.event("deployment_state", **state)

        if include_hpa:
            raw = kubectl("get", "hpa", deployment, "-n", namespace, "-o", "json", check=False)
            if raw.strip():
                hpa = json.loads(raw)
                status = hpa.get("status", {})
                metrics = status.get("currentMetrics", [])
                utilization = None
                if metrics:
                    utilization = metrics[0].get("resource", {}).get("current", {}).get("averageUtilization")
                hpa_key = (status.get("currentReplicas"), status.get("desiredReplicas"), utilization)
                if hpa_key != self._last_hpa:
                    self._last_hpa = hpa_key
                    sample = {"at": observed, "current": hpa_key[0], "desired": hpa_key[1],
                              "cpu_utilization": utilization}
                    self.data["hpa_samples"].append(sample)
                    self.event("hpa_state", **sample)
        return len(pods), ready_count

    def finish(self, result: str, error: str | None = None) -> Path:
        event_namespace = "tcp-lab" if self.data["mode"] == "application-autoscaler" else NAMESPACE
        raw_events = kubectl("get", "events", "-n", event_namespace, "-o", "json", check=False)
        if raw_events.strip():
            self.data["kubernetes_events"] = [self._compact_kubernetes_event(item)
                                                for item in json.loads(raw_events).get("items", [])]
        warning_events = kubectl("get", "events", "-A", "--field-selector", "type=Warning",
                                 "-o", "json", check=False)
        if warning_events.strip():
            self.data["cluster_warnings"] = [self._compact_kubernetes_event(item)
                                              for item in json.loads(warning_events).get("items", [])]
        self.data["finished_at"] = utc_now()
        self.data["result"] = result
        if error:
            self.data["error"] = error
        self.summarize()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        path = OUTPUT / f"{self.run_id}.json"
        path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        events = OUTPUT / f"{self.run_id}-events.jsonl"
        events.write_text("".join(json.dumps(item) + "\n" for item in self.data["events"]), encoding="utf-8")
        print(f"Saved {path}")
        return path

    @staticmethod
    def _compact_kubernetes_event(item: dict[str, Any]) -> dict[str, Any]:
        meta, involved = item.get("metadata", {}), item.get("involvedObject", {})
        return {
            "namespace": meta.get("namespace"), "type": item.get("type"),
            "reason": item.get("reason"), "message": item.get("message"),
            "count": item.get("count") or item.get("series", {}).get("count", 1),
            "first_at": item.get("eventTime") or item.get("firstTimestamp"),
            "last_at": item.get("series", {}).get("lastObservedTime") or item.get("lastTimestamp"),
            "object_kind": involved.get("kind"), "object_name": involved.get("name"),
            "object_uid": involved.get("uid"), "source": item.get("reportingController") or
            item.get("source", {}).get("component"),
        }

    def summarize(self) -> None:
        request_at = next((item["at"] for item in self.data["events"] if item["type"] in
                           {"scale_requested", "load_applied"}), self.data["started_at"])
        removal_at = next((item["at"] for item in self.data["events"] if item["type"] in
                           {"scale_down_requested", "load_removed"}), None)
        pods = []
        for name, item in self.data["pods"].items():
            pods.append({
                "name": name,
                "node": item.get("node"),
                "request_to_ready_seconds": duration(request_at, item.get("ready_at")),
                "created_to_scheduled_seconds": duration(item.get("created_at"), item.get("scheduled_at")),
                "scheduled_to_started_seconds": duration(item.get("scheduled_at"), item.get("started_at")),
                "started_to_ready_seconds": duration(item.get("started_at"), item.get("ready_at")),
                "delete_to_removed_seconds": duration(removal_at, item.get("removed_at")),
                "restarts": item.get("restarts", 0),
            })
        new_nodes = []
        baseline = set(self.data.get("baseline_nodes", []))
        for name, item in self.data["nodes"].items():
            if name not in baseline:
                new_nodes.append({
                    "name": name,
                    "instance_type": item.get("instance_type"),
                    "first_observed_to_ready_seconds": duration(item.get("first_observed_at"), item.get("ready_at")),
                })
        def event_at(event_type: str) -> str | None:
            return next((item["at"] for item in self.data["events"] if item["type"] == event_type), None)

        milestones: dict[str, Any] = {
            "scale_request_to_ready_seconds": duration(event_at("scale_requested"), event_at("scale_target_ready")),
            "hpa_to_target_ready_seconds": duration(event_at("autoscaler_enabled"), event_at("autoscale_target_ready")),
            "hpa_scale_down_seconds": duration(event_at("load_removed"), event_at("autoscale_minimum_ready")),
            "worker_loss_recovery_seconds": duration(event_at("worker_stop_requested"), event_at("worker_loss_recovered")),
            "worker_restore_seconds": duration(event_at("worker_start_requested"), event_at("worker_ready_after_restore")),
            "application_scale_down_request_seconds": duration(event_at("load_removed"), event_at("application_minimum_requested")),
        }
        enabled_at = event_at("autoscaler_enabled")
        first_hpa_scale = next((item.get("at") for item in self.data.get("hpa_samples", [])
                                if int(item.get("desired") or 0) > 1), None)
        milestones["hpa_first_decision_seconds"] = duration(enabled_at, first_hpa_scale)

        application = self.data.get("application_samples", [])
        if application:
            load_removed_at = event_at("load_removed")
            active_application = [item for item in application
                                  if not load_removed_at or item["at"] < load_removed_at]
            initial_gateway = application[0]["gateway"]["desired"]
            initial_server = application[0]["server"]["desired"]
            first_gateway = next((item["at"] for item in application
                                  if item["gateway"]["desired"] > initial_gateway), None)
            first_server = next((item["at"] for item in application
                                 if item["server"]["desired"] > initial_server), None)
            clients = int(self.data["parameters"].get("clients", 0))
            all_ready = next((item["at"] for item in application
                              if int(item["bots"].get("ready", 0)) == clients), None)
            after_initial_connect = active_application
            if all_ready:
                connected_index = next(index for index, item in enumerate(active_application)
                                       if item["at"] == all_ready)
                after_initial_connect = active_application[connected_index:]
            milestones.update({
                "application_gateway_first_scale_seconds": duration(event_at("load_applied"), first_gateway),
                "application_server_first_scale_seconds": duration(event_at("load_applied"), first_server),
                "application_all_clients_ready_seconds": duration(event_at("load_applied"), all_ready),
                "application_max_gateway_replicas": max(item["gateway"]["desired"] for item in application),
                "application_max_server_replicas": max(item["server"]["desired"] for item in application),
                "application_max_nodes": max(int(item.get("nodes", 0)) for item in application),
                "application_min_ready_after_initial_connect": min(
                    int(item["bots"].get("ready", 0)) for item in after_initial_connect),
                "application_final_ready_clients": int(active_application[-1]["bots"].get("ready", 0)),
            })
        self.data["summary"] = {"pods": pods, "new_nodes": new_nodes, "milestones": milestones}


def patch_probe(run_id: str, cpu: str, memory: str, cpu_load: bool, pull_policy: str,
                startup_delay: int) -> None:
    body = {
        "spec": {"replicas": 0, "template": {
            "metadata": {"labels": {"benchmark.tcp-lab.io/run": run_id}},
            "spec": {"containers": [{
                "name": "probe", "imagePullPolicy": pull_policy,
                "env": [
                    {"name": "CPU_LOAD", "value": str(cpu_load).lower()},
                    {"name": "STARTUP_DELAY_SECONDS", "value": str(startup_delay)},
                ],
                "resources": {"requests": {"cpu": cpu, "memory": memory},
                              "limits": {"cpu": cpu, "memory": memory}},
            }]},
        }}
    }
    kubectl("patch", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--type", "strategic",
            "-p", json.dumps(body))


def wait_for(recorder: Recorder, predicate, timeout_seconds: int, label: str,
             interval: float, include_hpa: bool = True) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        count, ready = recorder.sample_cluster(include_hpa=include_hpa)
        if predicate(count, ready):
            return
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout_seconds}s waiting for {label}")


def prepare_base() -> None:
    # A fresh namespace prevents a just-deleted HPA reconciliation from racing
    # the next run and also keeps old ReplicaSets out of the measurements.
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=2m")
    kubectl("apply", "-k", str(BASE))


def run_direct(args: argparse.Namespace, context: str, environment: str, replicas: int,
               mode: str = "pod") -> Path:
    parameters = {"replicas": replicas, "cpu": args.cpu, "memory": args.memory,
                  "pull_policy": args.pull_policy, "startup_delay": args.startup_delay}
    recorder = Recorder(mode, environment, context, parameters)
    try:
        prepare_base()
        wait_for(recorder, lambda count, ready: count == 0, args.timeout, "an empty probe", args.poll, False)
        recorder.data["baseline_nodes"] = sorted(recorder.data["nodes"])
        patch_probe(recorder.run_id, args.cpu, args.memory, False, args.pull_policy, args.startup_delay)
        recorder.event("scale_requested", replicas=replicas)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, f"--replicas={replicas}")
        wait_for(recorder, lambda count, ready: count == replicas and ready == replicas,
                 args.timeout, f"{replicas} ready pods", args.poll, False)
        recorder.event("scale_target_ready", replicas=replicas)
        if args.hold:
            time.sleep(args.hold)
        recorder.event("scale_down_requested", replicas=0)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0")
        wait_for(recorder, lambda count, ready: count == 0, args.timeout, "pod deletion", args.poll, False)
        if mode == "node-capacity":
            baseline = set(recorder.data.get("baseline_nodes", []))
            created = set(recorder.data["nodes"]) - baseline
            if created:
                recorder.event("node_scale_down_wait_started", nodes=sorted(created))
                deadline = time.monotonic() + args.node_scale_down_timeout
                while time.monotonic() < deadline:
                    recorder.sample_cluster(include_hpa=False)
                    live = {item["metadata"]["name"] for item in kubectl_json("get", "nodes").get("items", [])}
                    if not (created & live):
                        recorder.event("node_scale_down_complete", nodes=sorted(created))
                        break
                    time.sleep(max(1.0, args.poll))
                else:
                    recorder.event("node_scale_down_timeout", nodes=sorted(created & live))
        return recorder.finish("passed")
    except Exception as error:
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0", check=False)
        recorder.event("run_failed", error=str(error))
        recorder.finish("failed", str(error))
        raise


def node_is_ready(name: str) -> bool:
    raw = kubectl("get", "node", name, "-o", "json", check=False)
    if not raw.strip():
        return False
    return condition_time(json.loads(raw), "Ready") is not None


def run_kind_worker_loss(args: argparse.Namespace, context: str, environment: str) -> Path:
    if environment != "kind":
        raise RuntimeError("worker-loss directly stops a kind node container and is kind-only")
    parameters = {"replicas": args.replicas, "cpu": args.cpu, "memory": args.memory}
    recorder = Recorder("worker-loss", environment, context, parameters)
    stopped_node: str | None = None
    try:
        prepare_base()
        recorder.sample_cluster(include_hpa=False)
        recorder.data["baseline_nodes"] = sorted(recorder.data["nodes"])
        patch_probe(recorder.run_id, args.cpu, args.memory, False, args.pull_policy, args.startup_delay)
        recorder.event("scale_requested", replicas=args.replicas)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, f"--replicas={args.replicas}")
        wait_for(recorder, lambda count, ready: count == args.replicas and ready == args.replicas,
                 args.timeout, "initial probe placement", args.poll, False)
        pods = kubectl_json("get", "pods", "-n", NAMESPACE, "-l", SELECTOR).get("items", [])
        placement: dict[str, list[str]] = {}
        for pod in pods:
            placement.setdefault(pod.get("spec", {}).get("nodeName", ""), []).append(pod["metadata"]["uid"])
        candidates = {
            node: uids for node, uids in placement.items()
            if node and node != "tcp-lab-control-plane" and not node_is_database(node)
        }
        if not candidates:
            raise RuntimeError("no probe pod was scheduled on a general kind worker")
        stopped_node = max(candidates, key=lambda node: len(candidates[node]))
        original_uids = {pod["metadata"]["uid"] for pod in pods}
        recorder.event("worker_stop_requested", node=stopped_node,
                       affected_probe_pods=len(candidates[stopped_node]))
        command(["docker", "stop", stopped_node], quiet=True, timeout=30)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            recorder.sample_cluster(include_hpa=False)
            state = deployment_state(NAMESPACE, DEPLOYMENT)
            current = kubectl_json("get", "pods", "-n", NAMESPACE, "-l", SELECTOR).get("items", [])
            replacement_ready = any(
                pod["metadata"]["uid"] not in original_uids and condition_time(pod, "Ready")
                for pod in current
            )
            if state["ready"] >= args.replicas and replacement_ready:
                recorder.event("worker_loss_recovered", node=stopped_node, ready=state["ready"])
                break
            time.sleep(args.poll)
        else:
            raise TimeoutError(f"workload did not recover after stopping {stopped_node}")

        recorder.event("worker_start_requested", node=stopped_node)
        command(["docker", "start", stopped_node], quiet=True, timeout=30)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            recorder.sample_cluster(include_hpa=False)
            if node_is_ready(stopped_node):
                recorder.event("worker_ready_after_restore", node=stopped_node)
                break
            time.sleep(args.poll)
        else:
            raise TimeoutError(f"{stopped_node} did not return Ready")
        stopped_node = None
        recorder.event("scale_down_requested", replicas=0)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0")
        wait_for(recorder, lambda count, ready: count == 0, args.timeout, "cleanup", args.poll, False)
        return recorder.finish("passed")
    except Exception as error:
        recorder.event("run_failed", error=str(error))
        recorder.finish("failed", str(error))
        raise
    finally:
        if stopped_node:
            command(["docker", "start", stopped_node], check=False, quiet=True, timeout=30)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0", check=False)


def node_is_database(name: str) -> bool:
    raw = kubectl("get", "node", name, "-o", "json", check=False)
    if not raw.strip():
        return False
    labels = json.loads(raw).get("metadata", {}).get("labels", {})
    return labels.get("tcp-lab.io/database") == "true"


def run_hpa(args: argparse.Namespace, context: str, environment: str) -> Path:
    parameters = {"max_replicas": args.replicas, "cpu": args.cpu, "memory": args.memory,
                  "target_cpu": args.target_cpu, "pull_policy": args.pull_policy}
    recorder = Recorder("hpa", environment, context, parameters)
    try:
        prepare_base()
        patch_probe(recorder.run_id, args.cpu, args.memory, True, args.pull_policy, args.startup_delay)
        recorder.event("load_applied", target_cpu=args.target_cpu, max_replicas=args.replicas)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=1")
        wait_for(recorder, lambda count, ready: count == 1 and ready == 1,
                 args.timeout, "the loaded seed pod", args.poll, False)
        recorder.data["baseline_nodes"] = sorted(recorder.data["nodes"])
        kubectl("apply", "-f", str(AUTOSCALE / "hpa.yaml"))
        kubectl("patch", "hpa", DEPLOYMENT, "-n", NAMESPACE, "--type", "merge", "-p",
                json.dumps({"spec": {"maxReplicas": args.replicas, "metrics": [{
                    "type": "Resource", "resource": {"name": "cpu", "target": {
                        "type": "Utilization", "averageUtilization": args.target_cpu}}}]}}))
        recorder.event("autoscaler_enabled")
        wait_for(recorder, lambda count, ready: count == args.replicas and ready == args.replicas,
                 args.timeout, "HPA scale-up", args.poll)
        recorder.event("autoscale_target_ready", replicas=args.replicas)
        pods = kubectl_json("get", "pods", "-n", NAMESPACE, "-l", SELECTOR).get("items", [])
        recorder.event("load_removed", pods=len(pods))
        for pod in pods:
            kubectl("exec", "-n", NAMESPACE, pod["metadata"]["name"], "--", "touch", "/tmp/stop-load", check=False)
        wait_for(recorder, lambda count, ready: count == 1 and ready == 1,
                 args.scale_down_timeout, "HPA scale-down", args.poll)
        recorder.event("autoscale_minimum_ready", replicas=1)
        kubectl("delete", "hpa", DEPLOYMENT, "-n", NAMESPACE, "--ignore-not-found=true")
        recorder.event("scale_down_requested", replicas=0)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0")
        wait_for(recorder, lambda count, ready: count == 0, args.timeout, "cleanup", args.poll, False)
        return recorder.finish("passed")
    except Exception as error:
        kubectl("delete", "hpa", DEPLOYMENT, "-n", NAMESPACE, "--ignore-not-found=true", check=False)
        kubectl("scale", "deployment", DEPLOYMENT, "-n", NAMESPACE, "--replicas=0", check=False)
        recorder.event("run_failed", error=str(error))
        recorder.finish("failed", str(error))
        raise


def gateway_address(environment: str) -> tuple[str, int]:
    if environment == "kind":
        return "127.0.0.1", 9000
    service = kubectl_json("get", "service", "gateway", "-n", "tcp-lab")
    ingress = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
    if not ingress:
        raise RuntimeError("gateway LoadBalancer has no address")
    return ingress[0].get("hostname") or ingress[0]["ip"], 9000


def run_application(args: argparse.Namespace, context: str, environment: str) -> Path:
    sys.path.insert(0, str(ROOT / "tools"))
    from bots import BotManager  # pylint: disable=import-outside-toplevel

    parameters = {"clients": args.clients, "load_seconds": args.load_seconds,
                  "settle_seconds": args.settle_seconds}
    recorder = Recorder("application-autoscaler", environment, context, parameters)
    manager = None
    try:
        host, port = gateway_address(environment)
        manager = BotManager(host, port)
        baseline_nodes = kubectl_json("get", "nodes").get("items", [])
        recorder.data["baseline_nodes"] = sorted(node["metadata"]["name"] for node in baseline_nodes)
        recorder.event("load_applied", clients=args.clients, host=host, port=port)
        remaining = args.clients
        while remaining:
            batch = min(500, remaining)
            manager.spawn(batch, server_relevance=True, spatial_relevance=True,
                          cross_server_relevance=True)
            remaining -= batch
        deadline = time.monotonic() + args.load_seconds
        last: tuple[Any, ...] | None = None
        while time.monotonic() < deadline:
            bots = manager.snapshot()
            gateways = deployment_state("tcp-lab", "gateway")
            servers = deployment_state("tcp-lab", "tcp-server")
            nodes = kubectl_json("get", "nodes").get("items", [])
            sample = {"at": utc_now(), "bots": bots["states"], "gateway": gateways,
                      "server": servers, "nodes": len(nodes)}
            key = (tuple(bots["states"].values()), *gateways.values(), *servers.values(), len(nodes))
            if key != last:
                last = key
                recorder.data.setdefault("application_samples", []).append(sample)
                recorder.event("application_state", **sample)
            time.sleep(max(1.0, args.poll))
        recorder.event("load_removed")
        manager.despawn_all()
        settle_deadline = time.monotonic() + args.settle_seconds
        while time.monotonic() < settle_deadline:
            gateways = deployment_state("tcp-lab", "gateway")
            servers = deployment_state("tcp-lab", "tcp-server")
            nodes = kubectl_json("get", "nodes").get("items", [])
            sample = {"at": utc_now(), "bots": {"ready": 0, "gateway": 0, "disconnected": 0},
                      "gateway": gateways, "server": servers, "nodes": len(nodes)}
            recorder.data.setdefault("application_samples", []).append(sample)
            if gateways["desired"] == 1 and servers["desired"] == 1:
                recorder.event("application_minimum_requested")
                break
            time.sleep(max(1.0, args.poll))
        return recorder.finish("passed")
    except Exception as error:
        if manager is not None:
            manager.despawn_all()
        recorder.event("run_failed", error=str(error))
        recorder.finish("failed", str(error))
        raise


def cleanup() -> None:
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=5m")
    print(f"Removed namespace {NAMESPACE}.")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def generate_report() -> tuple[Path, Path]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(OUTPUT.glob("*.json")):
        if path.name.endswith("-events.json"):
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if "run_id" in item:
                runs.append(item)
        except (ValueError, OSError):
            continue
    rows = []
    for run in runs:
        pod_times = [pod["request_to_ready_seconds"] for pod in run.get("summary", {}).get("pods", [])
                     if pod.get("request_to_ready_seconds") is not None]
        delete_times = [pod["delete_to_removed_seconds"] for pod in run.get("summary", {}).get("pods", [])
                        if pod.get("delete_to_removed_seconds") is not None]
        milestones = dict(run.get("summary", {}).get("milestones", {}))
        events = run.get("events", [])
        event_at = lambda kind: next((item["at"] for item in events if item.get("type") == kind), None)
        hpa_samples = run.get("hpa_samples", [])
        application = run.get("application_samples", [])
        if not milestones:
            first_hpa = next((item.get("at") for item in hpa_samples if int(item.get("desired") or 0) > 1), None)
            milestones = {
                "scale_request_to_ready_seconds": duration(event_at("scale_requested"), event_at("scale_target_ready")),
                "hpa_first_decision_seconds": duration(event_at("autoscaler_enabled"), first_hpa),
                "hpa_to_target_ready_seconds": duration(event_at("autoscaler_enabled"), event_at("autoscale_target_ready")),
                "hpa_scale_down_seconds": duration(event_at("load_removed"), event_at("autoscale_minimum_ready")),
                "worker_loss_recovery_seconds": duration(event_at("worker_stop_requested"), event_at("worker_loss_recovered")),
                "application_scale_down_request_seconds": duration(event_at("load_removed"), event_at("application_minimum_requested")),
            }
            if application:
                base_gateway, base_server = application[0]["gateway"]["desired"], application[0]["server"]["desired"]
                first_gateway = next((item["at"] for item in application if item["gateway"]["desired"] > base_gateway), None)
                first_server = next((item["at"] for item in application if item["server"]["desired"] > base_server), None)
                clients = int(run.get("parameters", {}).get("clients", 0))
                all_ready = next((item["at"] for item in application if int(item["bots"].get("ready", 0)) == clients), None)
                milestones.update({
                    "application_gateway_first_scale_seconds": duration(event_at("load_applied"), first_gateway),
                    "application_server_first_scale_seconds": duration(event_at("load_applied"), first_server),
                    "application_all_clients_ready_seconds": duration(event_at("load_applied"), all_ready),
                })
        if application:
            clients = int(run.get("parameters", {}).get("clients", 0))
            load_removed_at = event_at("load_removed")
            active_application = [item for item in application
                                  if not load_removed_at or item["at"] < load_removed_at]
            connected_index = next((index for index, item in enumerate(active_application)
                                    if int(item["bots"].get("ready", 0)) == clients), 0)
            after_initial_connect = active_application[connected_index:]
            milestones.update({
                "application_max_gateway_replicas": max(item["gateway"]["desired"] for item in application),
                "application_max_server_replicas": max(item["server"]["desired"] for item in application),
                "application_max_nodes": max(int(item.get("nodes", 0)) for item in application),
                "application_min_ready_after_initial_connect": min(
                    int(item["bots"].get("ready", 0)) for item in after_initial_connect),
                "application_final_ready_clients": int(active_application[-1]["bots"].get("ready", 0)),
            })
        rows.append({
            "run_id": run["run_id"], "environment": run.get("environment"), "mode": run.get("mode"),
            "result": run.get("result"), "replicas": run.get("parameters", {}).get("replicas") or
            run.get("parameters", {}).get("max_replicas") or "",
            "scale_ready_seconds": milestones.get("scale_request_to_ready_seconds") or "",
            "autoscaler_first_decision_seconds": milestones.get("hpa_first_decision_seconds") or
            milestones.get("application_gateway_first_scale_seconds") or "",
            "autoscaler_target_ready_seconds": milestones.get("hpa_to_target_ready_seconds") or
            milestones.get("application_all_clients_ready_seconds") or "",
            "scale_down_seconds": milestones.get("hpa_scale_down_seconds") or
            milestones.get("application_scale_down_request_seconds") or "",
            "worker_recovery_seconds": milestones.get("worker_loss_recovery_seconds") or "",
            "pod_ready_average_seconds": round(statistics.mean(pod_times), 3) if pod_times else "",
            "pod_ready_p50_seconds": percentile(pod_times, .50) or "",
            "pod_ready_p95_seconds": percentile(pod_times, .95) or "",
            "pod_ready_max_seconds": round(max(pod_times), 3) if pod_times else "",
            "pod_delete_average_seconds": round(statistics.mean(delete_times), 3) if delete_times else "",
            "new_nodes": len(run.get("summary", {}).get("new_nodes", [])),
            "application_peak_gateways": milestones.get("application_max_gateway_replicas") or "",
            "application_peak_servers": milestones.get("application_max_server_replicas") or "",
            "application_peak_nodes": milestones.get("application_max_nodes") or "",
            "application_min_ready_after_connect": milestones.get("application_min_ready_after_initial_connect") or "",
            "application_final_ready_clients": milestones.get("application_final_ready_clients") or "",
            "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
        })
    csv_path = OUTPUT / "summary.csv"
    fields = list(rows[0]) if rows else ["run_id", "environment", "mode", "result"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    numeric_fields = [
        "scale_ready_seconds", "autoscaler_first_decision_seconds",
        "autoscaler_target_ready_seconds", "scale_down_seconds",
        "worker_recovery_seconds", "pod_ready_average_seconds",
        "pod_ready_p95_seconds", "pod_delete_average_seconds", "new_nodes",
        "application_peak_gateways", "application_peak_servers", "application_peak_nodes",
        "application_min_ready_after_connect", "application_final_ready_clients",
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["environment"]), str(row["mode"])), []).append(row)
    aggregates = []
    for (environment, mode), items in sorted(grouped.items()):
        aggregate: dict[str, Any] = {
            "environment": environment, "mode": mode, "runs": len(items),
            "passed": sum(item["result"] == "passed" for item in items),
        }
        for field in numeric_fields:
            values = [float(item[field]) for item in items if item.get(field) != ""]
            aggregate[f"{field}_average"] = round(statistics.mean(values), 3) if values else ""
            aggregate[f"{field}_p95"] = percentile(values, .95) or ""
        aggregates.append(aggregate)
    aggregate_path = OUTPUT / "aggregates.csv"
    aggregate_fields = list(aggregates[0]) if aggregates else ["environment", "mode", "runs", "passed"]
    with aggregate_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=aggregate_fields)
        writer.writeheader()
        writer.writerows(aggregates)

    body = "\n".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(field, '')))}</td>" for field in fields) + "</tr>" for row in rows)
    aggregate_body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(field, '')))}</td>" for field in aggregate_fields) + "</tr>"
        for row in aggregates
    )
    report_runs = [{
        "run_id": run.get("run_id"), "environment": run.get("environment"),
        "mode": run.get("mode"), "result": run.get("result"),
        "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
        "parameters": run.get("parameters", {}), "events": run.get("events", []),
        "deployment_samples": run.get("deployment_samples", []),
        "hpa_samples": run.get("hpa_samples", []),
        "application_samples": run.get("application_samples", []),
        "summary": run.get("summary", {}),
    } for run in runs]
    payload = json.dumps({"runs": report_runs, "rows": rows}, separators=(",", ":")).replace("<", "\\u003c")
    interactive_script = r"""
<script>
const DATA=JSON.parse(document.getElementById('benchmark-data').textContent);
const NS='http://www.w3.org/2000/svg';
const COLORS=['#58a6ff','#3fb950','#f0883e','#f85149','#bc8cff','#39c5cf'];
const IMPORTANT=new Set(['scale_requested','autoscaler_enabled','autoscale_target_ready','load_removed','autoscale_minimum_ready','worker_stop_requested','worker_loss_recovered','worker_start_requested','worker_ready_after_restore','node_scale_down_wait_started','node_scale_down_complete']);
const $=id=>document.getElementById(id);
const seconds=(value,origin)=>(Date.parse(value)-origin)/1000;
function svgNode(name,attrs={}){const node=document.createElementNS(NS,name);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);return node}
function textNode(svg,x,y,value,anchor='start',cls='axis-label'){const node=svgNode('text',{x,y,'text-anchor':anchor,class:cls});node.textContent=value;svg.appendChild(node)}
function lineChart(target,title,series,events=[]){
  target.innerHTML='';const heading=document.createElement('h3');heading.textContent=title;target.appendChild(heading);
  const populated=series.map((item,index)=>({...item,color:item.color||COLORS[index%COLORS.length],points:item.points.filter(point=>point[0]&&Number.isFinite(Number(point[1])))})).filter(item=>item.points.length);
  if(!populated.length){target.insertAdjacentHTML('beforeend','<p class="empty">No samples captured for this run.</p>');return}
  const times=populated.flatMap(item=>item.points.map(point=>Date.parse(point[0]))).concat(events.map(item=>Date.parse(item.at))).filter(Number.isFinite);
  const origin=Math.min(...times),end=Math.max(...times),span=Math.max(1,(end-origin)/1000);
  const values=populated.flatMap(item=>item.points.map(point=>Number(point[1])));const max=Math.max(1,...values);const top=max*1.08;
  const W=1000,H=285,L=55,R=18,T=18,B=38,x=value=>L+value/span*(W-L-R),y=value=>T+(top-value)/top*(H-T-B);
  const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':title});svg.classList.add('plot');target.appendChild(svg);
  for(let tick=0;tick<=4;tick++){const value=top*tick/4,py=y(value);svg.appendChild(svgNode('line',{x1:L,x2:W-R,y1:py,y2:py,class:'grid'}));textNode(svg,L-8,py+4,value>=10?Math.round(value):value.toFixed(1),'end')}
  svg.appendChild(svgNode('line',{x1:L,x2:W-R,y1:H-B,y2:H-B,class:'axis'}));
  textNode(svg,L,H-12,'0s','middle');textNode(svg,W-R,H-12,`${span.toFixed(1)}s`,'middle');
  for(const event of events.filter(item=>IMPORTANT.has(item.type))){const px=x(seconds(event.at,origin));const line=svgNode('line',{x1:px,x2:px,y1:T,y2:H-B,class:'event-line'});const tip=svgNode('title');tip.textContent=`${event.type}: ${seconds(event.at,origin).toFixed(2)}s`;line.appendChild(tip);svg.appendChild(line)}
  for(const item of populated){const points=item.points.map(point=>`${x(seconds(point[0],origin))},${y(Number(point[1]))}`).join(' ');svg.appendChild(svgNode('polyline',{points,fill:'none',stroke:item.color,'stroke-width':2.5,'stroke-linejoin':'round'}));if(item.points.length<70)for(const point of item.points){const dot=svgNode('circle',{cx:x(seconds(point[0],origin)),cy:y(Number(point[1])),r:2.8,fill:item.color});const tip=svgNode('title');tip.textContent=`${item.name}: ${point[1]} at ${seconds(point[0],origin).toFixed(2)}s`;dot.appendChild(tip);svg.appendChild(dot)}}
  const legend=document.createElement('div');legend.className='legend';for(const item of populated)legend.insertAdjacentHTML('beforeend',`<span><i style="background:${item.color}"></i>${item.name}</span>`);if(events.some(item=>IMPORTANT.has(item.type)))legend.insertAdjacentHTML('beforeend','<span><i class="event-key"></i>milestone</span>');target.appendChild(legend);
}
function lifecycle(target,run){
  target.innerHTML='<h3>Pod lifecycle phases</h3>';const pods=(run.summary?.pods||[]).filter(pod=>pod.created_to_scheduled_seconds!=null||pod.scheduled_to_started_seconds!=null||pod.started_to_ready_seconds!=null);
  if(!pods.length){target.insertAdjacentHTML('beforeend','<p class="empty">No pod lifecycle samples captured.</p>');return}
  const shown=pods.slice(0,32),total=pod=>['created_to_scheduled_seconds','scheduled_to_started_seconds','started_to_ready_seconds'].reduce((sum,key)=>sum+Number(pod[key]||0),0),max=Math.max(1,...shown.map(total));
  const legend=document.createElement('div');legend.className='legend';legend.innerHTML='<span><i style="background:#58a6ff"></i>create → scheduled</span><span><i style="background:#f0883e"></i>scheduled → started</span><span><i style="background:#3fb950"></i>started → ready</span>';target.appendChild(legend);
  for(const pod of shown){const row=document.createElement('div');row.className='phase-row';row.innerHTML=`<span title="${pod.name}">${pod.name}</span><div class="phase-bar"></div><b>${total(pod).toFixed(2)}s</b>`;const bar=row.querySelector('.phase-bar');[['created_to_scheduled_seconds','#58a6ff'],['scheduled_to_started_seconds','#f0883e'],['started_to_ready_seconds','#3fb950']].forEach(([key,color])=>{const segment=document.createElement('i');segment.style.cssText=`width:${Number(pod[key]||0)/max*100}%;background:${color}`;segment.title=`${key.replaceAll('_',' ')}: ${Number(pod[key]||0).toFixed(3)}s`;bar.appendChild(segment)});target.appendChild(row)}
  if(pods.length>shown.length)target.insertAdjacentHTML('beforeend',`<p class="empty">Showing the first ${shown.length} of ${pods.length} pods.</p>`);
}
function eventTable(target,run){
  const events=run.events.filter(item=>IMPORTANT.has(item.type));target.innerHTML='<h3>Milestone timeline</h3>';if(!events.length){target.insertAdjacentHTML('beforeend','<p class="empty">No major milestone events captured.</p>');return}
  const origin=Date.parse(run.started_at||events[0].at);const table=document.createElement('table');table.className='compact';table.innerHTML='<thead><tr><th>Elapsed</th><th>Event</th><th>Details</th></tr></thead><tbody></tbody>';for(const event of events){const details=Object.entries(event).filter(([key])=>!['at','type'].includes(key)).map(([key,value])=>`${key}=${Array.isArray(value)?value.join(','):typeof value==='object'?JSON.stringify(value):value}`).join(' · ');table.tBodies[0].insertAdjacentHTML('beforeend',`<tr><td>${seconds(event.at,origin).toFixed(2)}s</td><td>${event.type}</td><td>${details}</td></tr>`)}target.appendChild(table);
}
function renderRun(){
  const run=DATA.runs.find(item=>item.run_id===$('runSelect').value)||DATA.runs[0];if(!run)return;
  $('runTitle').textContent=`${run.environment} / ${run.mode}`;$('runMeta').textContent=`${run.run_id} · ${run.result} · ${JSON.stringify(run.parameters)}`;
  const cards=[['Result',run.result],['Pods observed',(run.summary?.pods||[]).length],['New workers',(run.summary?.new_nodes||[]).length],['Duration',`${seconds(run.finished_at,Date.parse(run.started_at)).toFixed(1)}s`]];$('runCards').innerHTML=cards.map(([label,value])=>`<div><span>${label}</span><b>${value}</b></div>`).join('');
  const events=run.events||[],deployment=run.deployment_samples||[],hpa=run.hpa_samples||[],app=run.application_samples||[];
  const charts=$('runCharts');charts.innerHTML='';const add=()=>{const section=document.createElement('section');charts.appendChild(section);return section};
  if(app.length){lineChart(add(),'Client connection states',[{name:'fully ready',points:app.map(x=>[x.at,x.bots.ready])},{name:'gateway only',points:app.map(x=>[x.at,x.bots.gateway])},{name:'disconnected',points:app.map(x=>[x.at,x.bots.disconnected])}],events);lineChart(add(),'Application capacity',[{name:'gateway desired',points:app.map(x=>[x.at,x.gateway.desired])},{name:'gateway ready',points:app.map(x=>[x.at,x.gateway.ready])},{name:'server desired',points:app.map(x=>[x.at,x.server.desired])},{name:'server ready',points:app.map(x=>[x.at,x.server.ready])},{name:'workers',points:app.map(x=>[x.at,x.nodes])}],events)}
  else lineChart(add(),'Deployment replicas',[{name:'desired',points:deployment.map(x=>[x.at,x.desired])},{name:'current',points:deployment.map(x=>[x.at,x.current])},{name:'ready',points:deployment.map(x=>[x.at,x.ready])}],events);
  if(hpa.length){lineChart(add(),'HPA replicas',[{name:'current',points:hpa.map(x=>[x.at,x.current])},{name:'desired',points:hpa.map(x=>[x.at,x.desired])}],events);lineChart(add(),'Measured CPU utilization (%)',[{name:'CPU',points:hpa.map(x=>[x.at,x.cpu_utilization])}],events)}
  const life=add();lifecycle(life,run);const milestones=add();eventTable(milestones,run);
}
function renderComparison(){
  const metric=$('metricSelect').value,items=DATA.rows.filter(row=>row[metric]!==''&&Number.isFinite(Number(row[metric]))),groups=Object.groupBy?Object.groupBy(items,row=>`${row.environment} / ${row.mode}`):items.reduce((all,row)=>((all[`${row.environment} / ${row.mode}`]??=[]).push(row),all),{});
  const target=$('comparisonChart');target.innerHTML='';const names=Object.keys(groups);if(!names.length){target.textContent='No data for this metric.';return}const max=Math.max(...items.map(row=>Number(row[metric])),1),W=1000,H=70+names.length*44,L=205,R=70,x=value=>L+value/max*(W-L-R);const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`});svg.classList.add('plot');target.appendChild(svg);
  names.forEach((name,index)=>{const y=45+index*44,values=groups[name].map(row=>Number(row[metric])),mean=values.reduce((a,b)=>a+b,0)/values.length,p95=[...values].sort((a,b)=>a-b)[Math.ceil(values.length*.95)-1];svg.appendChild(svgNode('line',{x1:L,x2:W-R,y1:y,y2:y,class:'grid'}));textNode(svg,L-12,y+4,name,'end');values.forEach((value,point)=>{const dot=svgNode('circle',{cx:x(value),cy:y+(point%3-1)*6,r:5,fill:'#58a6ff'});const tip=svgNode('title');tip.textContent=`${value.toFixed(3)}s`;dot.appendChild(tip);svg.appendChild(dot)});svg.appendChild(svgNode('line',{x1:x(mean),x2:x(mean),y1:y-13,y2:y+13,stroke:'#3fb950','stroke-width':4}));const diamond=svgNode('path',{d:`M ${x(p95)} ${y-8} l 8 8 l -8 8 l -8 -8 z`,fill:'#f0883e'});svg.appendChild(diamond);textNode(svg,W-R+8,y+4,`avg ${mean.toFixed(2)}s`)});textNode(svg,L,H-12,'0s','middle');textNode(svg,W-R,H-12,`${max.toFixed(1)}s`,'middle');$('comparisonLegend').innerHTML='<span><i style="background:#58a6ff;border-radius:50%"></i>individual run</span><span><i style="background:#3fb950"></i>average</span><span><i style="background:#f0883e;transform:rotate(45deg)"></i>p95</span>';
}
const runSelect=$('runSelect');for(const run of [...DATA.runs].reverse()){const option=document.createElement('option');option.value=run.run_id;option.textContent=`${run.environment} · ${run.mode} · ${run.run_id.slice(0,15)}`;runSelect.appendChild(option)}runSelect.addEventListener('change',renderRun);$('metricSelect').addEventListener('change',renderComparison);renderComparison();renderRun();
</script>
"""
    report_path = OUTPUT / "report.html"
    report_path.write_text(f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>TCP lab capacity benchmarks</title>
<style>:root{{color-scheme:dark;font:14px system-ui}}body{{margin:2rem auto;max-width:1500px;padding:0 1.2rem;background:#0d1117;color:#e6edf3}}h1{{font-size:1.6rem}}h2{{margin-top:2rem}}h3{{margin:.1rem 0 1rem}}select{{margin-left:.5rem;padding:.5rem;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px}}.toolbar{{display:flex;gap:1.5rem;flex-wrap:wrap;align-items:center;padding:1rem;background:#161b22;border:1px solid #30363d;border-radius:6px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.55rem;border:1px solid #30363d;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{background:#161b22}}tr:nth-child(even){{background:#161b22}}.compact td:nth-child(2),.compact td:nth-child(3){{text-align:left}}.note,.empty{{color:#8b949e}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:1rem}}section{{min-width:0;padding:1rem;background:#161b22;border:1px solid #30363d;border-radius:6px}}.plot{{display:block;width:100%;min-height:230px}}.axis{{stroke:#8b949e}}.grid{{stroke:#30363d;stroke-width:1}}.axis-label{{fill:#8b949e;font-size:12px}}.event-line{{stroke:#d29922;stroke-width:1;stroke-dasharray:4 4;opacity:.8}}.legend{{display:flex;gap:1rem;flex-wrap:wrap;color:#8b949e;margin:.5rem 0}}.legend span{{display:flex;align-items:center;gap:.35rem}}.legend i{{display:inline-block;width:.75rem;height:.75rem}}.event-key{{border-left:2px dashed #d29922;width:1px!important}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem;margin:1rem 0}}.cards div{{padding:.8rem;background:#161b22;border:1px solid #30363d;border-radius:6px}}.cards span{{display:block;color:#8b949e}}.cards b{{display:block;font-size:1.35rem;margin-top:.25rem}}.phase-row{{display:grid;grid-template-columns:155px 1fr 65px;gap:.6rem;align-items:center;margin:.45rem 0}}.phase-row>span{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.phase-row b{{text-align:right}}.phase-bar{{display:flex;height:.8rem;background:#21262d;border-radius:3px;overflow:hidden}}.phase-bar i{{display:block;min-width:1px}}details{{margin-top:1.5rem}}summary{{cursor:pointer;font-size:1.1rem;font-weight:600;padding:.75rem;background:#161b22;border:1px solid #30363d}}@media(max-width:700px){{.charts{{grid-template-columns:1fr}}.phase-row{{grid-template-columns:100px 1fr 55px}}}}</style></head><body><h1>TCP lab capacity benchmarks</h1><p class=\"note\">Generated {html.escape(utc_now())}. All graphs are rendered locally from the retained raw JSON; no server or external JavaScript is required.</p><h2>Environment comparison</h2><div class='toolbar'><label>Metric<select id='metricSelect'><option value='scale_ready_seconds'>Scale request → ready</option><option value='autoscaler_first_decision_seconds'>Autoscaler first decision</option><option value='autoscaler_target_ready_seconds'>Autoscaler → target ready</option><option value='scale_down_seconds'>Scale down</option><option value='worker_recovery_seconds'>Worker-loss recovery</option><option value='pod_ready_average_seconds'>Average pod ready</option><option value='pod_ready_p95_seconds'>Pod ready p95</option></select></label><div id='comparisonLegend' class='legend'></div></div><section id='comparisonChart'></section><h2 id='runTitle'>Run timeline</h2><p id='runMeta' class='note'></p><div class='toolbar'><label>Run<select id='runSelect'></select></label></div><div id='runCards' class='cards'></div><div id='runCharts' class='charts'></div><details><summary>Aggregate values</summary><div class='scroll'><table><thead><tr>{''.join(f'<th>{html.escape(field)}</th>' for field in aggregate_fields)}</tr></thead><tbody>{aggregate_body}</tbody></table></div></details><details><summary>Individual run values</summary><div class='scroll'><table><thead><tr>{''.join(f'<th>{html.escape(field)}</th>' for field in fields)}</tr></thead><tbody>{body}</tbody></table></div></details><script type='application/json' id='benchmark-data'>{payload}</script>{interactive_script}</body></html>""", encoding="utf-8")
    print(f"Wrote {csv_path}\nWrote {aggregate_path}\nWrote {report_path}")
    return csv_path, report_path


def run_suite(args: argparse.Namespace, context: str, environment: str) -> None:
    for _ in range(args.repetitions):
        run_direct(args, context, environment, 1, "pod-single")
        run_direct(args, context, environment, args.replicas, "pod-multi")
        run_hpa(args, context, environment)
    if environment == "kind":
        run_kind_worker_loss(args, context, environment)
    else:
        run_direct(args, context, environment, args.replicas, "node-capacity")
    if args.application:
        run_application(args, context, environment)
    generate_report()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run one benchmark or a repeated suite")
    run.add_argument("--environment", choices=("auto", "kind", "eks"), default="auto")
    run.add_argument("--mode", choices=("pod-single", "pod-multi", "hpa", "node-capacity",
                                        "worker-loss", "application", "full"), default="full")
    run.add_argument("--replicas", type=int, default=8)
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--cpu", default="250m")
    run.add_argument("--memory", default="64Mi")
    run.add_argument("--target-cpu", type=int, default=60)
    run.add_argument("--startup-delay", type=int, default=0)
    run.add_argument("--pull-policy", choices=("IfNotPresent", "Always"), default="IfNotPresent")
    run.add_argument("--poll", type=float, default=.5)
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--scale-down-timeout", type=int, default=600)
    run.add_argument("--node-scale-down-timeout", type=int, default=900)
    run.add_argument("--hold", type=int, default=0)
    run.add_argument("--clients", type=int, default=500)
    run.add_argument("--load-seconds", type=int, default=180)
    run.add_argument("--settle-seconds", type=int, default=180)
    run.add_argument("--application", action="store_true", help="include real dummy-client autoscaler test in full suite")
    sub.add_parser("report", help="rebuild HTML and CSV summaries from raw runs")
    sub.add_parser("cleanup", help="remove only the isolated benchmark namespace")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.action == "report":
        generate_report()
        return 0
    if args.action == "cleanup":
        cleanup()
        return 0
    if args.replicas < 2 and args.mode in {"pod-multi", "hpa", "full"}:
        raise ValueError("--replicas must be at least 2 for multi-pod and HPA tests")
    context, environment = context_environment(args.environment)
    print(f"Context: {context} ({environment})")
    if args.mode == "pod-single":
        run_direct(args, context, environment, 1, "pod-single")
    elif args.mode == "pod-multi":
        run_direct(args, context, environment, args.replicas, "pod-multi")
    elif args.mode == "hpa":
        run_hpa(args, context, environment)
    elif args.mode == "node-capacity":
        run_direct(args, context, environment, args.replicas, "node-capacity")
    elif args.mode == "worker-loss":
        run_kind_worker_loss(args, context, environment)
    elif args.mode == "application":
        run_application(args, context, environment)
    else:
        run_suite(args, context, environment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; run the cleanup command if benchmark resources remain.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1)

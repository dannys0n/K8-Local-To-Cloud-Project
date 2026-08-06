#!/usr/bin/env python3
"""Scale server replicas from independent per-pod CPU pressure."""

import json
import math
import os
from pathlib import Path
import sys


SCALE_UP_CPU_THRESHOLD = 80
SCALE_DOWN_CPU_THRESHOLD = float(os.getenv("SCALE_DOWN_CPU_THRESHOLD", "20"))
MIN_REPLICAS = 1
MAX_REPLICAS = 50
SCALE_MARKER = Path("/tmp/tcp-server-scale")
LOW_CPU_COUNTER = Path("/tmp/tcp-server-low-cpu-count")


def low_cpu_count() -> int:
    try:
        return int(LOW_CPU_COUNTER.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return 0


def maximum_change(current: int) -> int:
    pods = max(1, int(os.getenv("MAX_SCALE_CHANGE_PODS", "1")))
    percent = max(1, int(os.getenv("MAX_SCALE_CHANGE_PERCENT", "25")))
    return max(pods, math.ceil(current * percent / 100))


def main() -> None:
    spec = json.load(sys.stdin)
    if len(spec["metrics"]) != 1:
        raise ValueError("expected exactly one CPU metric")
    metric = json.loads(spec["metrics"][0]["value"])
    current = int(metric["current_replicas"])
    utilizations = [float(value) for value in metric["utilizations"]]
    if not utilizations:
        raise ValueError("no server CPU samples are available")
    pending_location = SCALE_MARKER.exists()
    down_evaluations = max(1, int(os.getenv("SCALE_DOWN_STABILIZATION_EVALUATIONS", "20")))
    complete_metrics = len(utilizations) == current
    all_low = complete_metrics and all(value <= SCALE_DOWN_CPU_THRESHOLD for value in utilizations)
    low_count = low_cpu_count() + 1 if not pending_location and all_low else 0
    LOW_CPU_COUNTER.write_text(str(low_count), encoding="utf-8")
    requested_additions = sum(
        max(0, math.ceil(value / SCALE_UP_CPU_THRESHOLD) - 1)
        for value in utilizations
    )
    scale_up = not pending_location and current < MAX_REPLICAS and requested_additions > 0
    scale_down = not pending_location and not scale_up and current > MIN_REPLICAS and low_count >= down_evaluations
    if scale_up:
        target = min(MAX_REPLICAS, current + maximum_change(current), current + requested_additions)
    elif scale_down:
        required = max(MIN_REPLICAS, math.ceil(sum(utilizations) / SCALE_UP_CPU_THRESHOLD))
        target = max(MIN_REPLICAS, current - maximum_change(current), required)
    else:
        target = current
    if scale_up:
        SCALE_MARKER.write_text(json.dumps({"direction": "up", "count": target - current, "target": target}), encoding="utf-8")
        LOW_CPU_COUNTER.write_text("0", encoding="utf-8")
    elif scale_down and target < current:
        SCALE_MARKER.write_text(json.dumps({"direction": "down", "count": current - target, "target": target}), encoding="utf-8")
        LOW_CPU_COUNTER.write_text("0", encoding="utf-8")
    json.dump({"targetReplicas": target}, sys.stdout)


if __name__ == "__main__":
    main()

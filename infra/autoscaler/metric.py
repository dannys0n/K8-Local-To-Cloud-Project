#!/usr/bin/env python3
"""Reduce Kubernetes CPU samples to the busiest server pod."""

import json
import os
import sys


def main() -> None:
    spec = json.load(sys.stdin)
    cpu = spec["kubernetesMetrics"][0]
    samples = cpu["resource"]["pod_metrics_info"].values()
    usage_millicores = [float(sample["Value"]) for sample in samples]
    if not usage_millicores:
        raise ValueError("no server CPU samples are available")
    request_millicores = float(os.environ["SERVER_CPU_REQUEST_MILLICORES"])
    maximum = max(usage_millicores) / request_millicores * 100
    json.dump(
        {"current_replicas": int(cpu["current_replicas"]), "maximum_utilization": maximum},
        sys.stdout,
    )


if __name__ == "__main__":
    main()

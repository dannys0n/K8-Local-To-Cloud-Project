#!/usr/bin/env python3
"""Report CPU utilization independently for every sampled server pod."""

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
    utilizations = [usage / request_millicores * 100 for usage in usage_millicores]
    json.dump(
        {"current_replicas": int(cpu["current_replicas"]), "utilizations": utilizations},
        sys.stdout,
    )


if __name__ == "__main__":
    main()

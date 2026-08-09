#!/usr/bin/env python3
"""Report per-pod CPU utilization from Prometheus."""

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import urlopen


def main() -> None:
    spec = json.load(sys.stdin)
    resource = spec["resource"]
    current = int(resource["spec"].get("replicas", 1))
    namespace = resource["metadata"]["namespace"]
    pod_pattern = os.environ["TARGET_POD_PATTERN"]
    container = os.environ["TARGET_CONTAINER"]
    window = os.getenv("CPU_RATE_WINDOW", "30s")
    query = (
        "sum by (pod) (rate(container_cpu_usage_seconds_total{"
        f'namespace="{namespace}",pod=~"{pod_pattern}",container="{container}"'
        f"}}[{window}])) * 1000"
    )
    base_url = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
    with urlopen(f"{base_url}/api/v1/query?{urlencode({'query': query})}", timeout=2) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ValueError("Prometheus CPU query failed")
    usage_millicores = [float(sample["value"][1]) for sample in payload["data"]["result"]]
    if not usage_millicores:
        raise ValueError("no CPU samples are available")
    request_millicores = float(os.environ["CPU_REQUEST_MILLICORES"])
    utilizations = [usage / request_millicores * 100 for usage in usage_millicores]
    json.dump(
        {"current_replicas": current, "utilizations": utilizations},
        sys.stdout,
    )


if __name__ == "__main__":
    main()

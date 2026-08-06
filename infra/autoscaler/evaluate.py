#!/usr/bin/env python3
"""Scale up by one when any server pod reaches the CPU threshold."""

import json
from pathlib import Path
import sys


CPU_THRESHOLD = 80
MAX_REPLICAS = 50
SCALE_MARKER = Path("/tmp/tcp-server-scale-up")


def main() -> None:
    spec = json.load(sys.stdin)
    if len(spec["metrics"]) != 1:
        raise ValueError("expected exactly one CPU metric")
    metric = json.loads(spec["metrics"][0]["value"])
    current = int(metric["current_replicas"])
    pending_location = SCALE_MARKER.exists()
    scale_up = (
        not pending_location
        and current < MAX_REPLICAS
        and float(metric["maximum_utilization"]) >= CPU_THRESHOLD
    )
    target = current + 1 if scale_up else current
    if scale_up:
        SCALE_MARKER.write_text(str(target), encoding="utf-8")
    elif not pending_location:
        SCALE_MARKER.unlink(missing_ok=True)
    json.dump({"targetReplicas": target}, sys.stdout)


if __name__ == "__main__":
    main()

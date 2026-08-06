#!/usr/bin/env python3
"""Keep logical locations aligned with successful server replica changes."""

import json
import os
from pathlib import Path
import subprocess
import sys

SCALE_MARKER = Path("/tmp/tcp-server-scale")


def main() -> None:
    json.load(sys.stdin)
    if not SCALE_MARKER.exists():
        return
    operation = json.loads(SCALE_MARKER.read_text(encoding="utf-8"))
    functions = {"up": "tcp_create_location", "down": "tcp_retire_location"}
    function = functions[operation["direction"]]
    count = int(operation["count"])
    if count < 1:
        raise ValueError("scale operation count must be positive")
    subprocess.run(
        [
            "psql", os.environ["POSTGRES_DSN"], "-v", "ON_ERROR_STOP=1", "-Atc",
            f"SELECT {function}() FROM generate_series(1, {count})",
        ],
        check=True,
    )
    SCALE_MARKER.unlink()


if __name__ == "__main__":
    main()

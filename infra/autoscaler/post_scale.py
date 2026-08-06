#!/usr/bin/env python3
"""Create one logical location for each successfully added server replica."""

import json
import os
from pathlib import Path
import subprocess
import sys

SCALE_MARKER = Path("/tmp/tcp-server-scale-up")


def main() -> None:
    json.load(sys.stdin)
    if not SCALE_MARKER.exists():
        return
    dsn = os.environ["POSTGRES_DSN"]
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-Atc", "SELECT tcp_create_location()"],
        check=True,
    )
    SCALE_MARKER.unlink()


if __name__ == "__main__":
    main()

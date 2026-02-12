"""
Builds a searchable text index (no embeddings).
"""

import json
from pathlib import Path

INDEX_DIRS = ["src", "Source", "server", "client", "Config"]

INDEX = Path(".repo_index")
INDEX.mkdir(exist_ok=True)

OUT = INDEX / "files.json"

files = {}

def should_index(p: Path):
    return any(str(p).startswith(d) for d in INDEX_DIRS)

for f in Path(".").rglob("*"):
    if not f.is_file():
        continue
    if not should_index(f):
        continue
    if f.stat().st_size > 200_000:
        continue

    try:
        text = f.read_text(errors="ignore")
    except:
        continue

    files[str(f)] = text[:12000]

OUT.write_text(json.dumps(files))
print("Indexed", len(files), "files")

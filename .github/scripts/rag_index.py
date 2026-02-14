"""
Builds a searchable text index (no embeddings).
Supports indexing directories AND specific files.
"""

import json
from pathlib import Path

# ---- WHAT PARTS OF THE PROJECT SHOULD BE LEARNED ----
# You can put folders OR files here
INDEX_PATHS = [
    # code
    "src",

    # infra / runtime
    "Makefile",

    # important root files
]

# ---- NEVER INDEX THESE ----
IGNORE_DIRS = {
    ".git",
    ".github",
    ".repo_index",
    "node_modules",
    "Binaries",
    "Build",
    "DerivedDataCache",
    "Intermediate",
    "Library",
    "dist",
    "venv",
    "__pycache__",
}

MAX_FILE_SIZE = 200_000  # bytes
MAX_CHARS = 12_000       # truncate large files

INDEX = Path(".repo_index")
INDEX.mkdir(exist_ok=True)

OUT = INDEX / "files.json"

files = {}


def normalize(p: Path) -> str:
    """Convert to repo-relative path with forward slashes."""
    return p.as_posix().lstrip("./")


def should_ignore(p: Path) -> bool:
    parts = set(p.parts)
    return any(part in IGNORE_DIRS for part in parts)


def should_index(p: Path) -> bool:
    rel = normalize(p)

    for item in INDEX_PATHS:
        if rel == item or rel.startswith(item + "/"):
            return True
    return False


for f in Path(".").rglob("*"):

    if not f.is_file():
        continue

    if should_ignore(f):
        continue

    if not should_index(f):
        continue

    if f.stat().st_size > MAX_FILE_SIZE:
        continue

    try:
        text = f.read_text(errors="ignore")
    except:
        continue

    files[normalize(f)] = text[:MAX_CHARS]


OUT.write_text(json.dumps(files, indent=2))
print("Indexed", len(files), "files")

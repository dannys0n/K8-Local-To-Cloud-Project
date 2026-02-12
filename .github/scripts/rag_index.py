"""
Builds a searchable memory of the repository.
Only indexes selected directories.
"""

import os, json, hashlib
from pathlib import Path
import requests

API_KEY = os.environ["OPENROUTER_API_KEY"]
EMBED_URL = "https://openrouter.ai/api/v1/embeddings"

# ---- CONFIGURE WHAT PARTS OF PROJECT MATTER ----
INDEX_DIRS = ["src"]


INDEX = Path(".repo_index")
INDEX.mkdir(exist_ok=True)

EMB_FILE = INDEX/"embeddings.json"
HASH_FILE = INDEX/"hashes.json"

embeddings = json.loads(EMB_FILE.read_text()) if EMB_FILE.exists() else {}
hashes = json.loads(HASH_FILE.read_text()) if HASH_FILE.exists() else {}


def sha(text):
    return hashlib.sha1(text.encode()).hexdigest()


def embed(text):
    r = requests.post(
        EMBED_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model":"openrouter/embedding","input":text[:4000]},
        timeout=60
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def should_index(path: Path):
    return any(str(path).startswith(d) for d in INDEX_DIRS)


for file in Path(".").rglob("*"):
    if not file.is_file():
        continue
    if not should_index(file):
        continue
    if file.stat().st_size > 200_000:
        continue

    text = file.read_text(errors="ignore")
    h = sha(text)

    if hashes.get(str(file)) == h:
        continue

    print("Indexing:", file)
    embeddings[str(file)] = embed(text)
    hashes[str(file)] = h


EMB_FILE.write_text(json.dumps(embeddings))
HASH_FILE.write_text(json.dumps(hashes))

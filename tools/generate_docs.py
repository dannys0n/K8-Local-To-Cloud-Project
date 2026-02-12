from pathlib import Path
import os
import re

ROOT = Path(".")
DOCS = ROOT / "docs"

EXCLUDE = {".git","node_modules","build","bin","obj",".idea",".vs",".github",".venv","dist","out"}
SKIP_EXT = (".png",".jpg",".jpeg",".gif",".dll",".exe",".pdb",".zip",".7z",".gz",".uasset",".umap")


# -------------------------------------------------------
# REPOSITORY SCAN
# -------------------------------------------------------
def scan():
    structure = {}

    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]

        rel = Path(root).relative_to(ROOT)
        valid = [f for f in files if not f.endswith(SKIP_EXT)]

        if valid:
            structure[str(rel)] = sorted(valid)

    return structure


# -------------------------------------------------------
# DEPENDENCY GRAPH (imports/includes)
# -------------------------------------------------------
def build_dependency_graph():
    edges = []

    for path in ROOT.rglob("*.*"):
        if any(x in str(path) for x in EXCLUDE):
            continue

        try:
            text = path.read_text(errors="ignore")[:4000]
        except:
            continue

        includes = re.findall(r'#include\s+[<"]([^">]+)', text)
        imports = re.findall(r'import\s+([\w\.]+)', text)

        src = path.name

        for inc in includes + imports:
            edges.append((src, inc.split("/")[-1]))

    lines = ["# Dependency Graph", "", "```mermaid", "graph LR"]

    for a,b in edges[:200]:
        lines.append(f'"{a}" --> "{b}"')

    lines.append("```")

    (DOCS / "dependencies.md").write_text("\n".join(lines))


# -------------------------------------------------------
# KUBERNETES RESOURCE MAP
# -------------------------------------------------------
def build_k8s_map():
    resources = []
    links = []

    for yaml in ROOT.rglob("*.yaml"):
        if "node_modules" in str(yaml):
            continue

        text = yaml.read_text(errors="ignore")

        kind = re.findall(r"kind:\s*(\w+)", text)
        name = re.findall(r"name:\s*([\w\-]+)", text)

        if kind and name:
            resources.append((kind[0], name[0]))

        if "serviceName" in text:
            svc = re.findall(r"serviceName:\s*([\w\-]+)", text)
            if svc and name:
                links.append((name[0], svc[0]))

    lines = ["# Kubernetes Resource Topology", "", "```mermaid", "graph TD"]

    for k,n in resources:
        lines.append(f'{n}["{k}: {n}"]')

    for a,b in links:
        lines.append(f"{a} --> {b}")

    lines.append("```")

    (DOCS / "k8s-topology.md").write_text("\n".join(lines))


# -------------------------------------------------------
# SERVICE INTERACTION MAP (ports)
# -------------------------------------------------------
def build_service_map():
    edges = []

    for file in ROOT.rglob("*.*"):
        try:
            text = file.read_text(errors="ignore")[:5000]
        except:
            continue

        ports = re.findall(r"localhost:(\d+)", text)
        for p in ports:
            edges.append((file.name, f"port-{p}"))

    lines = ["# Service Interaction Map", "", "```mermaid", "graph LR"]

    for a,b in edges[:100]:
        lines.append(f'"{a}" --> "{b}"')

    lines.append("```")

    (DOCS / "services.md").write_text("\n".join(lines))


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    DOCS.mkdir(exist_ok=True)
    scan()
    build_dependency_graph()
    build_k8s_map()
    build_service_map()

if __name__ == "__main__":
    main()

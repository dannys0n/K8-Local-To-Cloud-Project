from pathlib import Path
import os
import re

ROOT = Path(".")
DOCS = ROOT / "docs"

EXCLUDE = {".git","node_modules","build","bin","obj",".idea",".vs",".github",".venv","dist","out"}
SKIP_EXT = (".png",".jpg",".jpeg",".gif",".dll",".exe",".pdb",".zip",".7z",".gz",".uasset",".umap")

# ------------------------------
# Mermaid-safe naming
# ------------------------------
def safe(name: str):
    s = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if not s:
        s = "node"
    return s[:40]

def label(name: str):
    return name.replace('"', '').replace('\n','')[:60]


# -------------------------------------------------------
# DEPENDENCY GRAPH
# -------------------------------------------------------
def build_dependency_graph():
    nodes=set()
    edges=[]

    for path in ROOT.rglob("*.*"):
        if any(x in str(path) for x in EXCLUDE):
            continue

        try:
            text = path.read_text(errors="ignore")[:4000]
        except:
            continue

        includes = re.findall(r'#include\s+[<"]([^">]+)', text)
        imports = re.findall(r'import\s+([\w\.]+)', text)

        src = safe(path.name)
        nodes.add((src,label(path.name)))

        for inc in includes + imports:
            tgt=safe(inc.split("/")[-1])
            nodes.add((tgt,label(inc)))
            edges.append((src,tgt))

    lines=["# Dependency Graph","","```mermaid","graph LR"]

    for n,l in nodes:
        lines.append(f'{n}["{l}"]')

    for a,b in edges[:200]:
        lines.append(f"{a} --> {b}")

    lines.append("```")

    (DOCS/"dependencies.md").write_text("\n".join(lines))


# -------------------------------------------------------
# KUBERNETES TOPOLOGY
# -------------------------------------------------------
def build_k8s_map():
    nodes=set()
    edges=[]

    for yaml in ROOT.rglob("*.yaml"):
        if any(x in str(yaml) for x in EXCLUDE):
            continue

        text=yaml.read_text(errors="ignore")

        kinds=re.findall(r"kind:\s*(\w+)",text)
        names=re.findall(r"name:\s*([\w\-\.]+)",text)

        if kinds and names:
            k=kinds[0]
            n=names[0]
            nid=safe(n)
            nodes.add((nid,f"{k}: {n}"))

    lines=["# Kubernetes Resource Topology","","```mermaid","graph TD"]

    for n,l in nodes:
        lines.append(f'{n}["{l}"]')

    lines.append("```")

    (DOCS/"k8s-topology.md").write_text("\n".join(lines))


# -------------------------------------------------------
# SERVICE MAP
# -------------------------------------------------------
def build_service_map():
    nodes={}
    edges=[]

    for file in ROOT.rglob("*.*"):
        if any(x in str(file) for x in EXCLUDE):
            continue

        try:
            text=file.read_text(errors="ignore")[:5000]
        except:
            continue

        ports=re.findall(r"localhost:(\d+)",text)

        if not ports:
            continue

        src_id=safe(file.name)
        nodes[src_id]=label(file.name)

        for p in ports:
            tgt_id=f"port_{p}"
            nodes[tgt_id]=f"port {p}"
            edges.append((src_id,tgt_id))

    lines=["# Service Interaction Map","","```mermaid","graph LR"]

    # declare nodes
    for nid,lab in nodes.items():
        lines.append(f'{nid}["{lab}"]')

    # connect nodes (NO QUOTES)
    for a,b in edges:
        lines.append(f"{a} --> {b}")

    lines.append("```")

    (DOCS/"services.md").write_text("\n".join(lines))



# -------------------------------------------------------
def main():
    DOCS.mkdir(exist_ok=True)
    build_dependency_graph()
    build_k8s_map()
    build_service_map()

if __name__=="__main__":
    main()

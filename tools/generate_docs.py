from pathlib import Path
import os
import re

ROOT = Path(".")
DOCS = ROOT / "docs"

EXCLUDE = {".git",".github","node_modules","build","dist","bin","obj",".idea",".vs",".venv","out"}
CODE_EXT = {".py",".cpp",".hpp",".c",".h",".cs",".js",".ts",".go",".rs",".java"}
SCRIPT_EXT = {".sh",".ps1",".bat",".cmd",".py"}
CONFIG_EXT = {".yaml",".yml",".json",".toml",".ini",".cfg",".env",".xml"}

# ---------------------------
def safe(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)[:40] or "node"

def label(name):
    return name.replace('"','')[:60]


# ---------------------------
def scan_files():
    files=[]
    for path in ROOT.rglob("*"):
        if any(x in str(path) for x in EXCLUDE):
            continue
        if path.is_file():
            files.append(path)
    return files


# ---------------------------
# STRUCTURE
# ---------------------------
def build_structure(files):
    tree={}
    for f in files:
        folder=str(f.parent.relative_to(ROOT))
        tree.setdefault(folder,[]).append(f.name)

    lines=["# Project Structure\n"]
    for folder in sorted(tree):
        lines.append(f"## {folder}\n")
        for name in sorted(tree[folder])[:40]:
            lines.append(f"- {name}")
        lines.append("")

    (DOCS/"structure.md").write_text("\n".join(lines))


# ---------------------------
# EXECUTABLES
# ---------------------------
def build_executables(files):
    lines=["# Entrypoints / Executables\n"]

    for f in files:
        if f.suffix in SCRIPT_EXT or os.access(f,os.X_OK):
            lines.append(f"- `{f}`")

    (DOCS/"executables.md").write_text("\n".join(lines))


# ---------------------------
# CONFIG FILES
# ---------------------------
def build_config(files):
    lines=["# Configuration Files\n"]

    for f in files:
        if f.suffix in CONFIG_EXT:
            lines.append(f"- `{f}`")

    (DOCS/"config.md").write_text("\n".join(lines))


# ---------------------------
# DEPENDENCY GRAPH
# ---------------------------
def build_dependencies(files):
    nodes=set()
    edges=[]

    for f in files:
        if f.suffix not in CODE_EXT:
            continue

        try:
            text=f.read_text(errors="ignore")[:4000]
        except:
            continue

        src=safe(f.name)
        nodes.add((src,label(f.name)))

        includes=re.findall(r'#include\s+[<"]([^">]+)',text)
        imports=re.findall(r'import\s+([\w\.]+)',text)

        for dep in includes+imports:
            tgt=safe(dep.split("/")[-1])
            nodes.add((tgt,label(dep)))
            edges.append((src,tgt))

    lines=["# Dependency Graph","","```mermaid","graph LR"]

    for n,l in nodes:
        lines.append(f'{n}["{l}"]')
    for a,b in edges[:200]:
        lines.append(f"{a} --> {b}")

    lines.append("```")

    (DOCS/"dependencies.md").write_text("\n".join(lines))


# ---------------------------
def main():
    DOCS.mkdir(exist_ok=True)
    files=scan_files()

    build_structure(files)
    build_executables(files)
    build_config(files)
    build_dependencies(files)


if __name__=="__main__":
    main()

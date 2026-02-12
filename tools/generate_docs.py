import os
from pathlib import Path

ROOT = Path(".")
DOCS = ROOT / "docs"

EXCLUDE = {
    ".git","node_modules","build","bin","obj",".idea",".vs",
    "__pycache__",".pytest_cache",".venv","dist","out"
}

def scan_tree():
    structure = {}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]

        rel = os.path.relpath(root, ".")
        if rel.startswith(".git"):
            continue

        clean_files = [
            f for f in files
            if not f.endswith((".png",".jpg",".dll",".exe",".pdb",".uasset",".umap",".zip"))
        ]

        if clean_files:
            structure[rel] = clean_files

    return structure


def generate_architecture(structure):
    lines = ["# Repository Architecture\n"]

    for folder in sorted(structure):
        lines.append(f"## {folder}\n")
        for f in structure[folder][:20]:
            lines.append(f"- {f}")
        lines.append("")

    (DOCS / "architecture-auto.md").write_text("\n".join(lines))


def generate_readme(structure):
    lines = [
        "# K8 Local To Cloud Project",
        "",
        "This README is automatically generated.",
        "",
        "## Project Structure",
        ""
    ]

    for folder in sorted(structure):
        lines.append(f"### {folder}")
        for f in structure[folder][:10]:
            lines.append(f"- {f}")
        lines.append("")

    Path("README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    tree = scan_tree()
    generate_architecture(tree)
    generate_readme(tree)

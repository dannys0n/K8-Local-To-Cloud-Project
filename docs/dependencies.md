# Dependency Graph

```mermaid
graph LR
re["re"]
os["os"]
generate_docs_py["generate_docs.py"]
Path["Path"]
generate_docs_py --> Path
generate_docs_py --> os
generate_docs_py --> re
```
# mdbox

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![PyPI](https://img.shields.io/pypi/v/mdbox.svg)](https://pypi.org/project/mdbox/)

Pack and unpack text files into structured, plain-text archives with embedded XML. Perfectly formatted for LLM context windows.


## Overview

The `mdbox` utility bundles text-based file directories into a single plain-text archive structured with an embedded XML block. It provides a `zipfile`-like Python API and a `tar`-style CLI for creating, extracting, modifying, and deleting entries. 

Every archive embeds a visual directory tree and stores file contents safely in `CDATA` sections. The result is an archive that is completely Git-friendly and easily parseable by any standard XML tool.

### 🤖 Built for LLM Pipelines
Standard archive formats (`.zip`, `.tar.gz`) are binary and invisible to Large Language Models. `mdbox` solves this by creating self-contained, text-only bundles. 

By utilizing the **preamble** and **epilogue** features (which allow you to attach arbitrary text before and after the XML data), a single `mdbox` archive becomes a complete **prompt + context bundle**. Just attach your system instructions in the preamble, pack the relevant codebase, and feed the single file directly into your LLM pipeline.

---

## Key Features

### Core Capabilities
- **Human-readable XML:** Archives are plain text, Git-friendly, and inspectable in any text editor.
- **Embedded Directory Tree:** Every archive includes a visual, `tree`-style directory listing at a glance.
- **Preamble & Epilogue:** Prepend and append arbitrary text (like Markdown prompts or system instructions) around the XML block.
- **Secure Extraction:** Sandboxed unpacking prevents malicious path traversal (e.g., `../` attacks).
- **Strict Content Validation:** Built-in UTF-8 enforcement and XML 1.0 compatibility checks with precise error localization.

### Developer Experience
- **`zipfile`-compatible Python API:** Familiar methods like `open()`, `write()`, `readstr()`, `extractall()`, and `namelist()`.
- **`tar`-style CLI:** Quick and familiar command-line interface with bundled flags (`-cvf`).
- **Async Extraction Pipeline:** Concurrent file writing powered by `asyncio` and `aiofile` for maximum I/O performance.
- **Transactional Safety:** Atomic repacking for add/delete operations ensures no partial writes corrupt your archive if an exception occurs.
- **Lazy File Reading:** Disk sources are only read when content is explicitly accessed or the archive is flushed.

---

## Installation

Requires **Python 3.12+**.

```bash
# Standard pip
pip install mdbox

# With uv (Recommended)
uv add mdbox
````

-----

## Quick Start

### 1\. Create an Archive

Pack a directory and a specific file into a single `.xml` bundle.

**CLI:**

```bash
mdbox -cvf backup.xml src/ README.md
```

**Python:**

```python
import mdbox

with mdbox.open("backup.xml", mode="w") as qf:
    qf.write("src")
    qf.write("README.md")
```

### 2\. Extract an Archive

Unpack the bundle back to your local disk.

**CLI:**

```bash
mdbox -xf backup.xml output/
```

**Python:**

```python
with mdbox.open("backup.xml", mode="r") as qf:
    qf.extractall("output")
```

-----

## The Archive Format

An `mdbox` archive consists of three distinct sections:

1.  **Preamble:** Arbitrary text (Markdown, system prompts, prose).
2.  **`<archive>` XML block:** The structured file data and directory tree.
3.  **Epilogue:** Arbitrary trailing text (metadata, formatting closures).

Because file contents are stored in `<![CDATA[...]]>` blocks, all characters are preserved exactly without requiring strict entity encoding.

```xml
# Project Snapshot
> System Prompt: Review the following codebase for security vulnerabilities.

<archive version="1.0">
  <directory_tree><![CDATA[
.
├── src/
│   ├── main.py
│   └── utils/
│       └── helpers.py
└── README.md
]]></directory_tree>
  <file path="README.md">
    <content><![CDATA[# My Project
A sample project.
]]></content>
  </file>
  <file path="src/main.py">
    <content><![CDATA[print("hello")
]]></content>
  </file>
</archive>

---
*End of context bundle.*
```

-----

## CLI Reference

The `mdbox` utility supports standard `tar`-style bundled flags. *Note: The `-f` flag must always come last in a bundle.*

### Create (`-c`)

```bash
# Basic creation
mdbox -cvf archive.xml src/ docs/ README.md

# Creation with prompt injection (Preamble/Epilogue)
mdbox -cvf archive.xml --preamble "Build 2024-01-15" --epilogue license.txt src/
```

### Extract (`-x`)

```bash
# Extracts to default (.) or specified output directory
mdbox -xf archive.xml output/
```

### Add / Upsert (`-a`)

Creates the archive if it doesn't exist, or safely merges new entries into an existing one via atomic replacement.

```bash
mdbox -avf archive.xml new_module.py
```

### Delete (`--delete`)

Removes files or whole directory prefixes. Uses atomic repacking to prevent corruption.

```bash
mdbox --delete -f archive.xml old_module.py src/deprecated/
```

### Global Options

| Flag | Description |
|------|-------------|
| `-c` | Create a new archive |
| `-x` | Extract an archive |
| `-a` | Add/upsert files into an archive |
| `--delete` | Remove files from an archive |
| `-f <file>` | Archive file path (**required**) |
| `-v` | Verbose output |
| `--debug` | Structured debug logging |
| `--preamble <text\|file>`| Text or file content to prepend before XML |
| `--epilogue <text\|file>`| Text or file content to append after XML |

-----

## Python API Reference

### Opening & Iterating

```python
import mdbox

# Write mode (creates or overwrites)
with mdbox.open("archive.xml", mode="w") as qf:
    qf.write("src")

# Read mode (parses existing archive)
with mdbox.open("archive.xml", mode="r") as qf:
    for info in qf:
        print(f"File: {info.name}, Size: {info.length} bytes")
```

### Advanced Writing

```python
with mdbox.open("archive.xml", mode="w") as qf:
    qf.write("main.py")                       # Add single file
    qf.write("src")                           # Add entire directory
    qf.write("build/out.js", arcname="dist.js") # Override internal path
    qf.writestr("virtual.txt", "hello world")   # Write straight from memory
```

### Advanced Reading

```python
import io

with mdbox.open("archive.xml", mode="r") as qf:
    names = qf.namelist()                  # ['src/main.py', ...]
    text_content = qf.readstr("src/main.py") # Returns decoded string
    raw_bytes = qf.read("src/main.py")       # Returns raw bytes
    
    # Access injected LLM prompts
    print("Prompt:", qf.preamble)
    print("Trailing:", qf.epilogue)

# mdbox fully supports in-memory file-like objects
with io.BytesIO() as buffer:
    with mdbox.open(buffer, mode="w") as qf:
        qf.writestr("test.txt", "hello")
```

### Safe Extraction

```python
with mdbox.open("archive.xml", mode="r") as qf:
    # Extract everything
    qf.extractall("output/")

    # Extract conditionally
    python_files = [info for info in qf if info.name.endswith(".py")]
    qf.extractall("src_only/", members=python_files)
```

### Exception Handling

The `mdbox` library provides strict validation. Malformed inputs or malicious extraction paths will throw explicit errors:

```python
from mdbox import BinaryFileError, PathTraversalError

try:
    with mdbox.open("archive.xml", mode="w") as qf:
        qf.write("image.png") 
except BinaryFileError as e:
    print(f"Rejected: {e}") # Triggers if file fails UTF-8 checks

try:
    with mdbox.open("archive.xml", mode="r") as qf:
        qf.extractall()
except PathTraversalError as e:
    print(f"Blocked malicious path: {e}") # Triggers on absolute paths or ../
```

-----

## Architecture & Design

  * **Security:** Extraction paths are strictly validated using `Path.relative_to()`. Absolute paths and `..` escape attempts are blocked outright.
  * **Data Validation:** Files must pass UTF-8 decoding, and content is scanned to ensure XML 1.0 compatibility (blocking `NULL` and `C0/C1` control characters) to guarantee parseability.
  * **Performance:** \* In read mode, data is extracted via `memoryview` slicing directly from raw bytes to skip redundant parsing overhead.
      * `extractall()` leverages a bounded async queue and concurrent workers.
  * **Transactional Safety:** If a `with` block encounters an exception, `__exit__` safely aborts without writing, avoiding corrupt output states.

-----

## Development

```bash
# Clone and sync dependencies
git clone [https://github.com/chgroeling/mdbox.git](https://github.com/chgroeling/mdbox.git)
cd mdbox
uv sync --all-extras

# Run full quality gate (format, lint, type-check, test)
uv run ruff format src/ tests/ && \
uv run ruff check src/ tests/ && \
uv run mypy src/ && \
uv run pytest

# Check coverage
uv run pytest --cov=mdbox --cov-report=html
```

## License

MIT — see [LICENSE](https://www.google.com/search?q=LICENSE).

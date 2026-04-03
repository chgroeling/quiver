# mdbox

> Pack and unpack text files into structured archives with embedded XML.

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)

## Overview

MdBox bundles text file directories into a plain text archive with an embedded XML block. It provides a `zipfile`-like Python API and a `tar`-style CLI for creating, extracting, modifying, and deleting entries. Each archive embeds a visual directory tree and stores file contents in CDATA sections — Git-friendly and parseable by any XML tool.

**Designed for LLM pipelines.** The preamble and epilogue — arbitrary text before and after the XML — are ideal for system prompts and instructions. A single archive becomes a self-contained prompt + context bundle ready for any LLM.

## Features

- **Human-readable XML** — archives are plain text, Git-friendly, and inspectable with any editor
- **Embedded directory tree** — every archive contains a visual `tree`-style directory listing
- **`zipfile`-compatible Python API** — familiar `open()`, `write()`, `readstr()`, `read()`, `extractall()`, `namelist()`, iteration
- **`tar`-style CLI** — bundled flags (`-cvf`), create/extract/add/delete modes
- **Preamble & epilogue support** — attach arbitrary text before and after the XML block
- **Secure extraction** — sandboxed unpack with path traversal prevention
- **Content validation** — UTF-8 enforcement and XML 1.0 compatibility checks with precise error locations
- **Async extraction pipeline** — concurrent file writing via `asyncio` and `aiofile`
- **Transactional safety** — atomic repack for add/delete operations; no partial writes on exceptions
- **Lazy file reading** — disk sources are read only when content is accessed or the archive is flushed

## Archive Format

A mdbox archive is a plain text file with three sections: an arbitrary preamble, a central `<archive>` XML element, and an arbitrary epilogue. The preamble and epilogue can contain any text — Markdown, prose, system prompts, metadata — while the XML block in between holds the structured file data.

```markdown
# Project Snapshot

> Codebase exported on 2024-01-15

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
  <file path="src/utils/helpers.py">
    <content><![CDATA[def helper():
    pass
]]></content>
  </file>
</archive>

---
*License: MIT*
```

The `<directory_tree>` element provides an at-a-glance structure. File contents are stored in `<![CDATA[...]]>` blocks, preserving all characters without entity encoding.

## Installation

```bash
pip install mdbox
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv add mdbox
```

Requires **Python 3.12+**.

## Quick Start

**CLI** — create an archive from a directory:

```bash
mdbox -cvf backup.xml src/ README.md
```

**Python** — same operation programmatically:

```python
import mdbox

with mdbox.open("backup.xml", mode="w") as qf:
    qf.write("src")
    qf.write("README.md")
```

**Extract** back to disk:

```bash
mdbox -xf backup.xml output/
```

```python
with mdbox.open("backup.xml", mode="r") as qf:
    qf.extractall("output")
```

## CLI Reference

### Create (`-c`)

Pack files and directories into a new archive.

```bash
mdbox -cvf archive.xml src/ docs/ README.md
```

With preamble and epilogue (text or file path):

```bash
mdbox -cvf archive.xml --preamble "Build 2024-01-15" --epilogue license.txt src/
```

### Extract (`-x`)

Extract all entries to a destination directory (default: `.`).

```bash
mdbox -xf archive.xml output/
```

### Add / Upsert (`-a`)

Add new files or replace existing ones. Creates the archive if it doesn't exist.

```bash
mdbox -avf archive.xml new_module.py
```

The add operation uses a safe repack strategy: reads the existing archive, merges new entries, writes to a temp file, then atomically replaces the original.

### Delete (`--delete`)

Remove files or directory prefixes from an archive.

```bash
mdbox --delete -f archive.xml old_module.py src/deprecated/
```

Like add, delete uses atomic repack — no partial writes can corrupt the archive.

### Flag Bundling

MdBox supports `tar`-style bundled flags. The `-f` flag must come last in a bundle:

```bash
mdbox -cvf archive.xml src/    # create, verbose, file
mdbox -xf archive.xml ./out    # extract, file
```

### Options

| Flag | Description |
|------|-------------|
| `-c` | Create a new archive |
| `-x` | Extract an archive |
| `-a` | Add/upsert files into an archive |
| `--delete` | Remove files from an archive |
| `-f <file>` | Archive file path (required) |
| `-v` | Verbose output |
| `--debug` | Structured debug logging |
| `--preamble <text\|file>` | Text or file content to prepend before XML |
| `--epilogue <text\|file>` | Text or file content to append after XML |

## Python API

### Opening Archives

```python
import mdbox

# Write mode — creates or overwrites
with mdbox.open("archive.xml", mode="w") as qf:
    qf.write("src")

# Read mode — parses existing archive
with mdbox.open("archive.xml", mode="r") as qf:
    for info in qf:
        print(info.name, info.length)
```

### Writing

```python
with mdbox.open("archive.xml", mode="w") as qf:
    # Add a file (stored path: "main.py")
    qf.write("main.py")

    # Add a directory (stored paths: "src/a.py", "src/b.py")
    qf.write("src")

    # Override stored path with arcname
    qf.write("build/output.js", arcname="dist/bundle.js")

    # Add in-memory string
    qf.writestr("generated.txt", "hello world")
```

### Reading

```python
import io

with mdbox.open("archive.xml", mode="r") as qf:
    # List members
    names = qf.namelist()

    # Read as string (text)
    content = qf.readstr("src/main.py")

    # Read as bytes (raw)
    raw_bytes = qf.read("src/main.py")

    # Iterate with metadata
    for info in qf:
        if info.isfile():
            text = qf.readstr(info)
            data = qf.read(info)

    # Access preamble/epilogue
    print(qf.preamble)
    print(qf.epilogue)

# File-like object support (read or write mode)
with io.BytesIO() as buffer:
    with mdbox.open(buffer, mode="w") as qf:
        qf.writestr("test.txt", "hello")

    buffer.seek(0)  # Rewind to read
    with mdbox.open(buffer, mode="r") as qf:
        print(qf.readstr("test.txt"))  # "hello"
```

### Extraction

```python
with mdbox.open("archive.xml", mode="r") as qf:
    # Extract all to current directory
    qf.extractall()

    # Extract to specific path
    qf.extractall("output/")

    # Extract selected members
    members = [info for info in qf if info.name.startswith("src/")]
    qf.extractall("src_only/", members=members)
```

### Error Handling

```python
from mdbox import BinaryFileError, PathTraversalError

try:
    with mdbox.open("archive.xml", mode="w") as qf:
        qf.write("binary.dat")  # raises BinaryFileError
except BinaryFileError as e:
    print(f"Invalid text file: {e}")

try:
    with mdbox.open("archive.xml", mode="r") as qf:
        qf.extractall()  # raises PathTraversalError on malicious paths
except PathTraversalError as e:
    print(f"Unsafe path: {e}")
```

## Design

### Security

Extraction is sandboxed. Every entry path is validated before writing: absolute paths and `..` components are rejected outright, and resolved paths are verified to remain inside the destination directory via `Path.relative_to()`.

### Validation

All content passes two checks before entering the archive:

1. **UTF-8 decoding** — rejects binary files with a clear error message
2. **XML 1.0 compatibility** — scans for forbidden control characters (NULL, C0/C1 range) and reports up to 5 error locations with line/column/hex details

### Performance

- **Lazy reads** — in write mode, disk files are not read until `close()` or an explicit `read()` call
- **Memoryview slicing** — in read mode, file content is extracted from the raw archive bytes via `memoryview` without re-parsing
- **Async extraction** — `extractall()` uses a bounded async pipeline with concurrent workers partitioned across entries
- **Bounded queues** — reader/writer queues prevent OOM on large archives

### Transactional Safety

- **Exception safety** — if an exception propagates inside a `with` block, `__exit__` marks the archive closed without writing, preventing corrupt output
- **Atomic repack** — CLI add and delete operations write to a temp file then use `os.replace()` for atomic replacement, guaranteeing no partial writes

## Development

### Setup

```bash
# Clone and sync dependencies
git clone https://github.com/chgroeling/mdbox.git
cd mdbox
uv sync --all-extras
```

### Quality Checks

```bash
# Format and lint
uv run ruff format src/ tests/
uv run ruff check src/ tests/

# Type checking
uv run mypy src/

# Run tests
uv run pytest

# Full pre-commit gate
uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest
```

### Test Coverage

```bash
uv run pytest --cov=mdbox --cov-report=html
```

## License

MIT — see [LICENSE](LICENSE).

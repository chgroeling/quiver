# Feature: Directory Archiving & Asynchronous Reader/Writer Queue

## 1. Overview
Extension of the core logic to recursively pack entire directories. This feature focuses exclusively on the asynchronous concurrency architecture (`asyncio`) with a strict Reader/Writer pattern to prevent Out-of-Memory (OOM) errors when processing many or large files. The visual directory tree (`<directory_tree>`) is **not** yet part of this feature.

## 2. Requirements

### 2.1. CLI & Input
* **Command:** `quiver -c <input_folder> -f <output_archive.xml>`
* The CLI must detect whether the input (`-c`) is a single file or a directory.
* For a directory, all contained text files are collected recursively.

### 2.2. Concurrency & OOM Protection (Reader/Writer Pattern)
* **Reader Tasks:** Multiple asynchronous tasks (`asyncio`) traverse the directory and read files in parallel.
* **Bounded Queue:** Implementation of a size-limited `asyncio.Queue` for data exchange between readers and the writer (backpressure mechanism).
* **Single Writer:** A dedicated task consumes the prepared file contents from the queue, sorts them strictly alphabetically by their POSIX path, and writes them sequentially to the XML format.

### 2.3. XML Structure
* **Paths:** All paths in the XML (`<file path="...">`) must be normalized as POSIX paths (using `/`) and **include the top-level directory name as a prefix**, matching `tar` semantics. For example, packing a directory named `myproject` stores paths as `myproject/src/main.py`, not `src/main.py`.
* **`arcname` override:** When `arcname` is supplied to `QuiverFile.write()`, it replaces the directory's own name as the prefix (e.g., `arcname="bundle"` stores `bundle/src/main.py`).

### 2.4. Expected XML Output Format
The XML contains a flat, alphabetically sorted list of files with full directory-prefixed paths:

```xml
<archive version="1.0">
  <file path="myproject/src/main.py">
    <content><![CDATA[...]]></content>
  </file>
  <file path="myproject/src/utils/helper.py">
    <content><![CDATA[...]]></content>
  </file>
</archive>
# Feature: Asynchronous Archive Extraction & Path Sandboxing

## 1. Overview
Implement the extraction logic to unpack a `quiver` XML archive back into its original directory structure. A critical component of this feature is strict security sandboxing to prevent path traversal attacks. Furthermore, the actual file writing process must be highly performant and non-blocking, utilizing `asyncio` and `aiofile`.

## 2. Requirements

### 2.1. CLI & Input
* **Command:** `quiver -x <input_archive.xml> [destination_folder]`
* **Aliases:** `--extract` for `-x`.
* **Destination:** If `destination_folder` is not provided, default to the current working directory (`.`).
* **Flags:** Respond to `--verbose` (progress via `rich`) and `--debug` (`structlog`) appropriately.

### 2.2. Asynchronous Parsing & File Writing
* **Non-blocking I/O:** The process of writing the extracted files to disk must be fully asynchronous. Use `aiofile` (or `aiofiles`) to write the text content of multiple files concurrently.
* **Threaded Parsing:** Since `lxml.iterparse` is a synchronous, CPU-bound operation, the parsing of the XML archive should ideally be offloaded to a thread pool (e.g., using `asyncio.to_thread()`) or structured in a way that it yields control back to the event loop, feeding an `asyncio.Queue` with the parsed file data.
* **Directory Creation:** Ensure parent directories are created asynchronously (e.g., using `anyio.Path.mkdir` or `asyncio.to_thread(os.makedirs)`).
* **Extraction Task Workers:** Implement asynchronous worker tasks that consume the parsed file data from the queue and perform the actual `aiofile` writing operations in parallel.

### 2.3. Strict Path Sandboxing (Security)
* **Path Validation:** Before creating *any* file or directory, the target path must be resolved and checked against the `destination_folder`.
* **Traversal Blocking:** If a `path` attribute contains relative up-level references (`../` or `..\`) or is an absolute path (e.g., `/root/secret.txt`), the extraction must abort immediately and raise a clear security exception.
* **Platform Compatibility:** Normalize paths during extraction so that an archive created on a POSIX system extracts correctly on Windows, and vice versa.

## 3. Acceptance Criteria (Testing)
All tests must run asynchronously (`pytest-asyncio`) and enforce strict typing.

* [ ] **Test Async Extraction:** An archive containing nested folders and files is correctly recreated using asynchronous file writing.
* [ ] **Test Async Concurrency:** Multiple files are written concurrently without blocking the main event loop.
* [ ] **Test Path Sandboxing (Absolute/Traversal):** Attempting to extract an archive with absolute paths or `../` references raises a security error and aborts immediately.
* [ ] **Test Missing Directories:** The extraction process correctly and safely creates intermediate parent directories.
* [ ] **Type Checking:** All new parsing, path validation, and async writing logic passes `mypy --strict` without errors.
# Feature: Python API & LLM Streaming (`AsyncQuiverFile`)

## 1. Overview
Implement the core programmatic interface for the `quiver` package. Developers need a clean, object-oriented API to interact with archives directly from their Python code, without invoking the CLI. A critical component of this API is the `stream_content()` method, which allows asynchronous chunked reading of file contents to efficiently feed Large Language Models (LLMs) without loading massive files entirely into memory.

## 2. Requirements

### 2.1. API Design: `AsyncQuiverFile`
* **Context Manager:** Implement `AsyncQuiverFile` as an asynchronous context manager (`async with AsyncQuiverFile("archive.xml", mode="r") as aqf:`).
* **Initialization:** When opened in read mode (`"r"`), the class must automatically detect the boundaries of the first `<archive>` tag (ignoring any `PREAMBLE` or `EPILOGUE`) and parse the internal structure to build an index of available files.

### 2.2. Metadata Methods
The class must provide the following methods to inspect the archive:
* `namelist() -> list[str]`: Returns a list of all normalized POSIX file paths contained in the archive.
* `infolist() -> list[QuiverInfo]`: Returns a list of `QuiverInfo` objects. 
    * A `QuiverInfo` object must contain at least: `name` (the path) and `size` (the length of the extracted text content in bytes).

### 2.3. The Streaming Interface: `stream_content()`
* **Signature:** `async def stream_content(self, path: str, chunk_size: int = 8192) -> AsyncIterator[str]`
* **Behavior:** * Finds the `<file>` node corresponding to the given `path`. If not found, raise a `KeyError` or a custom `QuiverFileNotFoundError`.
    * Extracts the text from the `<content>` CDATA block.
    * **Crucial:** Yields the text asynchronously in chunks of `chunk_size` characters/bytes. 
* **Integration:** This method must cleanly isolate the specific file's text, completely ignoring the `PREAMBLE`, `EPILOGUE`, and XML tags.

### 2.4. Example Usage
```python
import asyncio
from quiver import AsyncQuiverFile

async def process_for_llm():
    async with AsyncQuiverFile("codebase.xml", mode="r") as aqf:
        paths = aqf.namelist()
        if "src/main.py" in paths:
            # Stream directly to LLM or processing pipeline
            async for chunk in aqf.stream_content("src/main.py", chunk_size=1024):
                print(chunk, end="")

asyncio.run(process_for_llm())
```

## 3. Acceptance Criteria (Testing)
All tests must run asynchronously (`pytest-asyncio`), use `pyfakefs`, and enforce strict typing.

* [ ] **Test Context Manager:** The archive opens and closes cleanly, releasing all file handles.
* [ ] **Test `namelist`:** Correctly returns a list of all file paths present in the archive, ignoring directories.
* [ ] **Test `infolist`:** Correctly returns `QuiverInfo` objects with accurate `name` and `size` properties.
* [ ] **Test `stream_content` Chunking:** Streaming a large text file correctly yields multiple chunks of the specified size. The concatenated chunks must perfectly match the original file content.
* [ ] **Test File Not Found:** Requesting to stream a path that does not exist in the archive raises a clearly typed error.
* [ ] **Test Embedded Archives:** The API correctly ignores `PREAMBLE` and `EPILOGUE` texts when opened in read mode.
* [ ] **Type Checking:** The API classes, `QuiverInfo`, and the async generator logic pass `mypy --strict` without errors.

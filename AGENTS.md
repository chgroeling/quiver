# AGENTS.md - System Context & Developer Guidelines for "quiver"

## 1. Project Context
You are developing `quiver`, a high-performance, cross-platform Python package and command-line tool. Its core purpose is packing and unpacking text files into a strictly formatted, machine-readable XML format.

## 2. Workflow & Git Rules
Strict rules apply to version control and the development workflow in this project:
* **Semantic Versioning (SemVer):** All versioning must strictly follow the SemVer standard (`MAJOR.MINOR.PATCH`).
* **Conventional Commits:** All commit messages must adhere to the Conventional Commits standard (e.g., `feat: ...`, `fix: ...`, `chore: ...`, `refactor: ...`).
* **Commits Only on Request:** **Never** commit code autonomously. Commits must only be executed when the user explicitly requests them.

## 3. Technology Stack
Exclusively use the following technologies and libraries for implementation:
* **Language:** Python 3.12.3
* **Environment & Packaging:** `uv`, `pyproject.toml`
* **CLI & UI:** `click` (Framework), `rich` (User feedback, verbose mode)
* **Logging:** `structlog` (Internal, structured debug logging)
* **XML & I/O:** `lxml` (Processing), `aiofile` (Asynchronous I/O)
* **Quality Assurance:** `ruff` (Linter/Formatter), `mypy` (Strict mode typechecking)
* **Testing:** `pytest`, `pyfakefs` (Mocking), `pytest-benchmark`
* **Documentation:** `mkdocs`

## 4. Coding Standards
* **Strict Typing:** The entire codebase must be statically typed. Type hints are mandatory and must pass `mypy` in strict mode without errors.
* **Formatting:** The code must strictly follow PEP8 guidelines, enforced by `ruff`.
* **Testing:** For every written function, at least one unit test (preferably more for edge cases) must exist. Use `pyfakefs` to mock file system operations in tests.
* **Separation of UI and Logging:**
    * The CLI is "silent by default".
    * Internal logging is handled **only** via `structlog` and is disabled by default. It is only activated if an explicit debug flag is passed in the CLI.
    * User feedback (progress, generic outputs) is handled **only** via `rich` and is only activated if a verbose flag is passed. Never mix UI outputs with the internal logger.

## 5. Architecture & Internal Mechanisms
* **Concurrency & Memory Management (OOM Protection):**
    * I/O operations are handled asynchronously (`asyncio`) or via threading.
    * Implement a strict Reader/Writer pattern using a size-limited queue to prevent Out-of-Memory (OOM) crashes (backpressure).
    * Large files must be streamed in chunks.
* **Single Writer Principle:** To prevent deadlocks and ensure Git-friendly, deterministic results, only one dedicated task is permitted to write the prepared XML data.
* **Sorting & Paths:** File entries must be written to the XML in strict alphabetical order. All internal paths must be normalized to POSIX paths (forward slashes `/`).
* **Security & Validation:**
    * Only UTF-8 readable text files may be processed.
    * Unpacking requires strict sandboxing. Absolute paths or path-traversal attempts (`../`) must be blocked immediately and cause an abort.
* **XML Constraints:** The content of text files must be placed within unescaped `<![CDATA[ ... ]]>` blocks in the XML. Do not use entity encoding (`&lt;`) for the file body.
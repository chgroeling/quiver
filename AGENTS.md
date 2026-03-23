# AGENTS.md

## Project description
`quiver` is a high-performance Python utility that bi-directionally serializes text file directories into a strictly structured, machine-readable XML format.

## Project Structure

```text
quiver/
├── .python-version              # Python 3.12.3 version pin
├── pyproject.toml               # Project configuration and dependencies
├── uv.lock                      # Locked dependency versions (tracked in git)
├── .gitignore                   # Git ignore rules
├── AGENTS.md                    # This file - developer guidelines
├── LICENSE                      # MIT License
├── README.md                    # Project documentation
│
├── src/quiver/                  # Main source code (src-layout)
│   ├── __init__.py              # Package metadata + quiver.open() factory
│   ├── archive.py               # QuiverFile, QuiverInfo, BinaryFileError, XML logic
│   ├── cli.py                   # Click CLI entry point
│   ├── logging.py               # configure_debug_logging(), get_console()
│   └── utils/                   # Utility modules
│       └── __init__.py
│
├── tests/                       # Test suite
│   ├── __init__.py
│   ├── conftest.py              # Pytest fixtures and configuration
│   ├── test_cli.py              # CLI smoke tests
│   ├── test_archive.py          # QuiverFile / QuiverInfo unit tests
│   └── test_pack_cli.py         # pack subcommand integration tests
│
└── docs/                        # MkDocs documentation
    └── index.md                 # Documentation home page
```

# Development Workflows

### UV Environment & Dependencies
- **Sync:** `uv sync` (add `--all-extras` for dev/docs).
- **Update:** `uv lock --upgrade`.
- **Management:** `uv add <pkg>` (use `--dev` for dev); `uv remove <pkg>`; `uv pip list`.
- **Strategy:** Use min constraints (e.g., `click>=8.1.0`) in `pyproject.toml`; rely on `uv.lock` for reproducibility. Avoid manual lock edits.

### Execution & Lifecycle
- **Run:** `uv run [quiver|python script.py|tool] [args]`.
- **Project:** `uv init` (setup); `uv check` (compat-check).
- **Dist:** `uv build` (wheel/sdist); `uv publish` (upload).

### Standards & Git
- **Versioning:** Strict SemVer (`MAJOR.MINOR.PATCH`).
- **Commits:** Follow Conventional Commits (e.g., `feat:`, `fix:`, `chore:`).
- **Automation:** **Never** commit autonomously; only execute on explicit user request.

## Testing & QA

### Quality Checks
Uses `ruff` (lint/format) and `mypy` (types) for speed and centralization.
- **Lint:** `uv run ruff check src/ tests/`
- **Format:** `uv run ruff format src/ tests/`
- **Format Check:** `uv run ruff format --check src/ tests/`
- **Type Check:** `uv run mypy src/`

### Pre-Commit Gate
Run sequence: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest`

### Execution
- **All:** `uv run pytest` (-v for verbose)
- **Targeted:** `uv run pytest tests/[file].py` or `tests/[file].py::[function]`
- **Coverage:** `uv run pytest --cov=quiver --cov-report=html`

### Structure
- **Location:** `tests/` directory.
- **Mapping:** 1:1 module-to-test file ratio (e.g., `cli.py` -> `test_cli.py`).
- **Practices:** Use `tmp_path` for FS tests; prioritize critical path coverage.


## Tech Stack & Standards
- **Runtime:** Python 3.12.3
- **Concurrency:** `asyncio` (core)
- **Package Mgmt:** `uv` via `pyproject.toml` (Build: `hatchling`)
- **CLI/UI:** `click` (commands); `rich` (UI/verbose)
- **Logging:** `structlog` (debug/structured)
- **Parsing/IO:** `lxml` (XML); `aiofile` (async I/O)
- **Quality:** `ruff` (lint/fmt); `mypy` (strict)
- **Testing:** `pytest` (plugins: `benchmark`, `asyncio`)
- **Docs:** `mkdocs` with Material theme

## Coding Standards
- **Typing:** Strict `mypy` for `src/` (reliability); relaxed for `tests/` (mocking flexibility).
- **Format:** PEP8 enforced via `ruff`; max line length 100 chars.
- **Testing:** Min. 1 unit test per function; use `tmp_path` for FS tests.
- **UI vs. Logging:** CLI silent by default.
    - **structlog:** Internal logs only; enabled via debug flag.
    - **rich:** User feedback/progress only; enabled via verbose flag.
    - **Strict Isolation:** Never mix UI output with internal loggers.

## Python API

The public API follows the `tarfile` pattern. Entry point: `quiver.open()` in `__init__.py`.

### `QuiverFile` (`src/quiver/archive.py`)
- Factory: `QuiverFile.open(name, mode)` or `quiver.open(name, mode)`
- Modes: `'r'` (read), `'w'` (write), `'a'` (append)
- Context manager: calls `close()` on `__exit__`
- `add(name, arcname=None)` — accepts file or directory input; validates UTF-8, normalizes POSIX paths, stores entries
- Directory packing uses an internal async reader/writer flow with bounded queue backpressure and a single writer task
- `close()` — sorts entries alphabetically, builds lxml XML tree, writes to disk
- `getnames()` / `getmembers()` — return names / `QuiverInfo` objects (write mode only for now)
- `extractall()` — scaffolded; raises `NotImplementedError`

### `QuiverInfo` (`src/quiver/archive.py`)
- `name: str` — normalized POSIX path
- `size: int` — content size in bytes
- `isfile() -> bool`, `isdir() -> bool`

### Exceptions
- `BinaryFileError(ValueError)` — raised when a file is not valid UTF-8

### `quiver.open()` (`src/quiver/__init__.py`)
- Top-level factory; delegates to `QuiverFile.open()`
- Exports: `open`, `QuiverFile`, `QuiverInfo`, `BinaryFileError`, `__version__`

## CLI
Command style mirrors `tar`:

- **Create**: `quiver -cf <archive.xml> <input_path...>` bundles short flags; `-f` must be last in a bundle.
- **Verbose**: include `-v` (e.g., `quiver -cvf archive.xml src docs`).
- **Debug logging**: `--debug` (no short form).
- **Extract stub**: `quiver -xf <archive.xml>` prints "not yet implemented".
- Inputs after the archive path are packed recursively; multiple paths are allowed.
- Silent by default—no stdout unless `-v` or `--debug` is supplied.

## Logging & UI (`src/quiver/logging.py`)

- `configure_debug_logging(enabled)` — configures `structlog`; use `logging.CRITICAL` (50) as the minimum no-op level. **Do not use `logging.CRITICAL + 1`** — it is not a valid structlog filter level and raises `KeyError`.
- `get_console(verbose)` — returns a `rich.Console()` (stdout) when verbose, or `Console(quiet=True)` otherwise.
- Rich verbose console writes to **stdout** (not stderr) so `CliRunner` captures it in `result.output`.

### structlog usage rules
- Use `structlog.get_logger(__name__)` in all modules — **never** `logging.getLogger()`.
- Pass context as keyword arguments: `logger.debug("msg", key=value)` — **never** use `extra={...}`.
  - `extra={"name": ...}` crashes: `name` is a reserved `LogRecord` attribute (`KeyError`).
- Processor chain for `PrintLoggerFactory` (debug-enabled path):
  ```python
  processors=[
      structlog.processors.add_log_level,
      structlog.processors.StackInfoRenderer(),
      structlog.dev.ConsoleRenderer(),
  ]
  ```
- **Do not use** `structlog.stdlib.add_log_level` or `structlog.stdlib.add_logger_name` with `PrintLoggerFactory` — they require `logging.LoggerFactory()` and crash with `AttributeError: 'PrintLogger' object has no attribute 'name'`.

### Established log fields in `archive.py`
| Call site | Fields |
|---|---|
| `QuiverFile.__init__` | `archive_name=`, `mode=` |
| `QuiverFile.add` | `entry_path=`, `size=` |
| `QuiverFile.close` | `archive_name=` |

## Architecture & Internal Mechanisms
- **CLI Structure:** A single Click command emulates tar-style flags, including bundled short options (`-cvf`). Custom argument preprocessing expands bundles before Click parses them.
- **Concurrency & Memory Management (OOM Protection):**
    * I/O operations are handled asynchronously (`asyncio`) or via threading.
    * Implement a strict Reader/Writer pattern using a size-limited queue to prevent Out-of-Memory (OOM) crashes (backpressure).
    * Large files must be streamed in chunks.
- **Single Writer Principle:** To prevent deadlocks and ensure Git-friendly, deterministic results, only one dedicated task is permitted to write the prepared XML data.
- **Sorting & Paths:** File entries must be written to the XML in strict alphabetical order. All internal paths must be normalized to POSIX paths (forward slashes `/`).
- **Security & Validation:**
    * Only UTF-8 readable text files may be processed.
    * Unpacking requires strict sandboxing. Absolute paths or path-traversal attempts (`../`) must be blocked immediately and cause an abort.
- **XML Constraints:** The content of text files must be placed within unescaped `<![CDATA[ ... ]]>` blocks in the XML. Do not use entity encoding (`&lt;`) for the file body.


## Docstring Rules
- **Format:** Google Style (`Args:`, `Returns:`, `Raises:`).
- **Markup:** Markdown ONLY. NO reST/Sphinx directives (`:class:`, `:func:`, `:exc:`, `::`).
- **Code:** Single backticks for inline (`` `x` ``). Triple backticks for blocks (````python````).
- **Links:** Use MkDocs autorefs: `[MyClass][]` or `[func][module.func]`.
- **Types:** Rely on Python type hints in the signature. Do not duplicate types in docstrings.
- **Style:** PEP 257 imperative mood ("Return X", not "Returns X").
- **Length:** Use pure one-liners (`"""Do X."""`) for simple, private, or trivial functions. Use multi-line (summary, blank line, sections) ONLY for complex or public APIs. Do not force `Args:`/`Returns:` if the function is self-explanatory.

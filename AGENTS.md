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
│   ├── test_create_cli.py       # create operation integration tests
│   └── test_utils.py            # utils/__init__.py unit tests
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
- **Mapping:** 1:1 module-to-test file ratio.
    - `cli.py` -> `test_cli.py` (smoke) and `test_create_cli.py` (integration).
    - `archive.py` -> `test_archive.py`.
    - `utils/__init__.py` -> `test_utils.py`.
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
- **Type Aliases:** Use the Python 3.12 `type X = ...` keyword syntax. **Never** `X: TypeAlias = ...` — ruff flags it as `UP040`.
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
- **Validation**: Mode flags (`-c`, `-x`) are mutually exclusive; exactly one is required.

## Logging & UI (`src/quiver/logging.py`)
- `configure_debug_logging(enabled)`: Configures `structlog`. Use `logging.CRITICAL` (50) for no-op. **Avoid `logging.CRITICAL + 1`** (causes `KeyError`).
- `get_console(verbose)`: Returns `rich.Console()`. Verbose writes to **stdout** for `CliRunner` capture; otherwise `quiet=True`.

### structlog Rules
- **Init**: Use `structlog.get_logger(__name__)`. **Never** `logging.getLogger()`.
- **Context**: Use kwargs: `logger.debug("msg", k=v)`. **Never** `extra={...}` (crashes on reserved keys like `name`).
- **Processors (PrintLoggerFactory)**:
  ```python
  processors=[
      structlog.processors.add_log_level,
      structlog.processors.StackInfoRenderer(),
      structlog.dev.ConsoleRenderer(),
  ]


### Established log fields in `archive.py`
| Call site | Fields |
|---|---|
| `QuiverFile.__init__` | `archive_name=`, `mode=` |
| `QuiverFile.add` | `entry_path=`, `size=` |
| `QuiverFile.close` | `archive_name=` |

# Architecture & Mechanisms
- **CLI:** Single Click command; emulates tar-style bundled short flags (`-cvf`) via custom pre-processing expansion.
- **Concurrency/OOM:** Asyncio/threading with size-limited queues (Reader/Writer pattern) for backpressure; chunk-stream large files to prevent OOM.
- **Single Writer:** One dedicated task handles XML output for determinism, Git-friendliness, and deadlock prevention.
- **Normalization:** POSIX paths (forward slashes) only; file entries sorted alphabetically in XML.
- **Security:** UTF-8 text only; sandbox unpacking; abort on absolute paths or traversal (`../`) attempts.
- **XML Specs:** File content must use unescaped `<![CDATA[ ... ]]>` blocks; no entity encoding for bodies.
  - `<directory_tree>` is always the **first child** of `<archive>`, placed before all `<file>` elements.
  - `<directory_tree>` content is also CDATA-wrapped (`"\n" + tree_text + "\n"`) for consistency and to future-proof against special characters in filenames.
  - An empty archive renders `<directory_tree>` containing just `"."`.

## Docstring Rules
- **Format:** Google Style (`Args:`, `Returns:`, `Raises:`).
- **Markup:** Markdown ONLY. NO reST/Sphinx directives (`:class:`, `:func:`, `:exc:`, `::`).
- **Code:** Single backticks for inline (`` `x` ``). Triple backticks for blocks (````python````).
- **Links:** Use MkDocs autorefs: `[MyClass][]` or `[func][module.func]`.
- **Types:** Rely on Python type hints in the signature. Do not duplicate types in docstrings.
- **Style:** PEP 257 imperative mood ("Return X", not "Returns X").
- **Length:** Use pure one-liners (`"""Do X."""`) for simple, private, or trivial functions. Use multi-line (summary, blank line, sections) ONLY for complex or public APIs. Do not force `Args:`/`Returns:` if the function is self-explanatory.

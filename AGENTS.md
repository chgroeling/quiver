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
│   ├── test_extract_cli.py      # extract operation integration tests
│   ├── test_add_cli.py          # add/upsert operation integration tests
│   ├── test_embedding.py        # preamble/epilogue embedding integration tests
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
    - `cli.py` -> `test_cli.py` (smoke), `test_create_cli.py` (create integration), `test_extract_cli.py` (extract integration), `test_add_cli.py` (add/upsert integration), `test_embedding.py` (embedding integration).
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

### Import Rules (ruff enforced)
- **Order:** stdlib → third-party → local (`from quiver...`). One blank line between each group. `ruff` enforces this as `I001`; always run `uv run ruff check --fix` or `uv run ruff format` after adding imports.
- **No unused imports:** Every import must be referenced in the file. Remove unused imports immediately — ruff flags them as `F401`.
- **`TYPE_CHECKING` blocks:** Only use `if TYPE_CHECKING:` when there is at least one symbol inside. An empty `if TYPE_CHECKING: pass` block is flagged as `TC005` and must be deleted entirely.
- **Async-safe I/O (`ASYNC240`):** Inside `async def` functions **never** call blocking `pathlib.Path` methods (`read_text`, `write_text`, `mkdir`, `replace`, `unlink`, etc.) directly. Wrap them with `asyncio.to_thread(path.method, ...)`. Violations are flagged as `ASYNC240`.
- **Path helpers over `os` (`PTH*`):** Prefer `Path(...).replace(...)` over `os.replace(...)`, `Path(...).unlink()` over `os.remove()`, etc. Ruff flags raw `os` path calls as `PTH105`, `PTH107`, etc. Only import `os` if no `pathlib` equivalent exists.
- **`contextlib.suppress` over bare `try/except/pass` (`SIM105`):** Use `with contextlib.suppress(SomeError):` instead of a `try: ... except SomeError: pass` block.

### mypy Rules (strict mode)
- **Return types:** All functions — including `__exit__` — must have explicit return type annotations.
- **`__exit__` signature:** Must be typed exactly as:
  ```python
  def __exit__(
      self,
      exc_type: type[BaseException] | None,
      exc_val: BaseException | None,
      exc_tb: TracebackType | None,
  ) -> None:
  ```
  Import `TracebackType` from `types` inside an `if TYPE_CHECKING:` block (or at the top of the file).
- **lxml types:** `lxml` ships no inline stubs; use `lxml-stubs` (already a dev dependency). When annotating element variables use `etree._Element`; for CDATA use `etree.CDATA(...)`. mypy may still emit false positives for some lxml internals — suppress with `# type: ignore[assignment]` only as a last resort.
- **`asyncio.to_thread` with methods:** Pass bound methods as the first argument: `asyncio.to_thread(path.read_text, encoding="utf-8")` — not `asyncio.to_thread(lambda: path.read_text(...))`. The lambda form loses the return-type inference.

## Python API

The public API follows the `tarfile` pattern. Entry point: `quiver.open()` in `__init__.py`.

### `QuiverFile` (`src/quiver/archive.py`)
- Factory: `QuiverFile.open(name, mode, preamble=None, epilogue=None)` or `quiver.open(name, mode, preamble=None, epilogue=None)`
- Modes: `'r'` (read), `'w'` (write), `'a'` (append)
- Context manager: calls `close()` on `__exit__`
- `add(name, arcname=None)` — accepts file or directory input; validates UTF-8 encoding **and** XML-1.0 character compatibility (via `_validate_xml_compatible`), normalizes POSIX paths, stores entries. When packing a **directory**, the directory's own name is preserved as a path prefix (matching `tar` semantics — e.g. `add("mydir")` stores `mydir/file.txt`, not `file.txt`). Supply `arcname` to override the prefix.
- Directory packing uses an internal async reader/writer flow with bounded queue backpressure and a single writer task
- `close()` — in `'w'` mode: sorts entries, builds lxml XML tree, writes to disk (creates or overwrites). In `'a'` mode: delegates to `_UpsertPipeline` for streaming merge. In both modes: **skipped entirely** (archive untouched) when `close()` is reached via `__exit__` with a propagating exception.
- `getnames()` / `getmembers()` — return names / `QuiverInfo` objects; works in both read and write mode
- `extractall(path=".", members=None)` — extracts all (or selected) members to *path* using an async pipeline; validates every path against the destination sandbox before writing; writes `PREAMBLE` and `EPILOGUE` files to *path* when the archive contains non-whitespace surrounding text

### `QuiverInfo` (`src/quiver/archive.py`)
- `name: str` — normalized POSIX path
- `size: int` — content size in bytes
- `isfile() -> bool`, `isdir() -> bool`

### Exceptions
- `BinaryFileError(ValueError)` — raised when a file (a) is not valid UTF-8, **or** (b) is valid UTF-8 but contains XML-1.0-forbidden characters (NULL bytes, C0/C1 control characters other than tab/LF/CR). The error message always includes the file path and, for case (b), up to `_MAX_REPORTED_OFFENCES` (default 5) locations formatted as `line N, col M: \xNN`.
- `PathTraversalError(ValueError)` — raised when an archive entry path is absolute, contains `..`, or resolves outside the extraction destination

### `quiver.open()` (`src/quiver/__init__.py`)
- Top-level factory; delegates to `QuiverFile.open()`
- Accepts explicit `preamble` and `epilogue` keyword arguments (no `**kwargs` — typed directly)
- Exports: `open`, `QuiverFile`, `QuiverInfo`, `BinaryFileError`, `PathTraversalError`, `__version__`

## CLI
Command style mirrors `tar`:

- **Create**: `quiver -cf <archive.xml> <input_path...>` bundles short flags; `-f` must be last in a bundle.
- **Extract**: `quiver -xf <archive.xml> [destination]` extracts to `destination` (default: `.`).
- **Add/Upsert**: `quiver -af <archive.xml> <input_path...>` upserts files into an existing archive (insert new, replace existing, preserve alphabetical order).
- **Verbose**: include `-v` (e.g., `quiver -cvf archive.xml src docs`).
- **Debug logging**: `--debug` (no short form).
- **Preamble**: `--preamble <text_or_filepath>` — prepends text before the XML. If the argument is an existing file path, its contents are used; otherwise treated as a raw string.
- **Epilogue**: `--epilogue <text_or_filepath>` — appends text after the XML. Same filepath-or-string resolution as `--preamble`.
- Inputs after the archive path are packed recursively for `-c` and `-a`; for `-x` the first positional arg is the destination.
- Silent by default—no stdout unless `-v` or `--debug` is supplied.
- **Validation**: Mode flags (`-c`, `-x`, `-a`) are mutually exclusive; exactly one is required.

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
| Call site                         | Fields                   |
| --------------------------------- | ------------------------ |
| `QuiverFile.__init__`             | `archive_name=`, `mode=` |
| `QuiverFile.add`                  | `entry_path=`, `size=`   |
| `QuiverFile.close`                | `archive_name=`          |
| `_ExtractPipeline._writer_worker` | `entry_path=`, `size=`   |
| `_UpsertPipeline._run_async`      | `archive_name=`          |

# Architecture & Mechanisms
- **CLI:** Single Click command; emulates tar-style bundled short flags (`-cvf`) via custom pre-processing expansion.
- **Concurrency/OOM:** Asyncio/threading with size-limited queues (Reader/Writer pattern) for backpressure; chunk-stream large files to prevent OOM.
- **Single Writer:** One dedicated task handles XML output for determinism, Git-friendliness, and deadlock prevention.
- **Normalization:** POSIX paths (forward slashes) only; file entries sorted alphabetically in XML.
- **Security:** UTF-8 text only; sandbox unpacking; abort on absolute paths or traversal (`../`) attempts. `_validate_extraction_path()` (Layer 1) performs pre-resolution rejection of absolute paths and `..` components, then confirms the resolved path is inside the destination with `Path.relative_to()`.
- **Content validation (Layer 2):** File content goes through two sequential checks inside `_read_text_file()` / `_read_text_file_async()` before it is stored:
  1. `_decode_utf8()` — rejects non-UTF-8 bytes → `BinaryFileError`.
  2. `_validate_xml_compatible()` — rejects XML-1.0-forbidden characters (matched by `_XML_FORBIDDEN_RE`: `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]`) → `BinaryFileError` with line/col/hex location info (up to `_MAX_REPORTED_OFFENCES = 5` occurrences). This prevents a late crash inside `lxml.etree.CDATA()` at serialization time with no useful context.
- **Extraction Pipeline (`_ExtractPipeline`, Layer 3.5):** Mirrors `_PackPipeline`. Feeds `(stored_path, content)` pairs through a bounded `asyncio.Queue`; concurrent worker tasks validate, create parent dirs via `asyncio.to_thread`, and write files with `aiofile`. XML parsing uses `lxml.etree.fromstring` on the isolated XML block (not the full file).
- **Upsert Pipeline (`_UpsertPipeline`, Layer 3.7):** Implements OOM-safe stream-merge for `mode='a'`. Performs two sequential `lxml.iterparse` passes over `io.BytesIO(xml_bytes)` (never loads the full XML into RAM): **Pass 1** collects existing `path` attributes (calling `element.clear()` after each) to regenerate `<directory_tree>` from the merged path set; **Pass 2** streams `<file>` elements and merge-inserts/upserts/copies against the sorted new-entry list, writing each element via `aiofile` to `archive.xml.tmp`. Finalizes with `</archive>` + epilogue, then calls `asyncio.to_thread(Path(tmp).replace, archive_path)` for an atomic swap. On any error the `.tmp` file is removed with `contextlib.suppress(FileNotFoundError)` and the original is never touched.
- **`__exit__` exception-propagation rule:** `QuiverFile.__exit__` checks `exc_type is not None` and, if so, sets `self._closed = True` without calling `close()`. This guarantees that a failed `add()` call inside a `with` block never triggers a write/upsert that would corrupt or silently truncate the archive. This is the same contract as `tarfile.TarFile`.
- **Read mode (`QuiverFile.__init__`):** Opening in `'r'` immediately parses the archive via `_parse_archive()` and populates `self._entries`, `self._preamble`, and `self._epilogue`; raises `FileNotFoundError` if the file does not exist.
- **Embedded archive (preamble/epilogue):** The archive file may contain arbitrary plain text before and after the `<archive>` block. `_split_archive_text()` locates the **first** `<archive ...>` and first `</archive>` to isolate the XML; everything outside is preamble/epilogue. The **first-match rule** means any subsequent `<archive>` blocks are treated as pure epilogue text, never parsed as XML.
- **XML Specs:** File content must use unescaped `<![CDATA[ ... ]]>` blocks; no entity encoding for bodies.
  - `<directory_tree>` is always the **first child** of `<archive>`, placed before all `<file>` elements.
  - `<directory_tree>` content is also CDATA-wrapped (`"\n" + tree_text + "\n"`) for consistency and to future-proof against special characters in filenames.
  - An empty archive renders `<directory_tree>` containing just `"."`.)
- **Sentinel constants (`_PREAMBLE_SENTINEL`, `_EPILOGUE_SENTINEL`):** Defined in Layer 0 of `archive.py`. Written at the preamble→XML and XML→epilogue seams so boundaries are deterministic and round-trippable. Currently `_PREAMBLE_SENTINEL = "\n"` (ensures `<archive>` starts on its own line); `_EPILOGUE_SENTINEL = ""` (lxml's own trailing `\n` already provides the separator). Changing the values requires updating `_write_archive`, `_split_archive_text`, and the sentinel unit tests.
- **lxml `pretty_print` trailing newline:** `etree.tostring(..., pretty_print=True)` **always** appends a `\n` after the closing tag. This `\n` appears at the **start of the epilogue slice** in `_split_archive_text` (it is outside `</archive>`, not inside `xml_content`). `_split_archive_text` strips exactly one leading `\n` from the epilogue unconditionally to absorb this artifact and ensure verbatim round-trip. Do not add logic that assumes `xml_content` ends with `\n` — it ends with `>`.

## Docstring Rules
- **Format:** Google Style (`Args:`, `Returns:`, `Raises:`).
- **Markup:** Markdown ONLY. NO reST/Sphinx directives (`:class:`, `:func:`, `:exc:`, `::`).
- **Code:** Single backticks for inline (`` `x` ``). Triple backticks for blocks (````python````).
- **Links:** Use MkDocs autorefs: `[MyClass][]` or `[func][module.func]`.
- **Types:** Rely on Python type hints in the signature. Do not duplicate types in docstrings.
- **Style:** PEP 257 imperative mood ("Return X", not "Returns X").
- **Length:** Use pure one-liners (`"""Do X."""`) for simple, private, or trivial functions. Use multi-line (summary, blank line, sections) ONLY for complex or public APIs. Do not force `Args:`/`Returns:` if the function is self-explanatory.
- **Staleness:** When implementing a previously scaffolded method (e.g., one that raised `NotImplementedError`), always update its docstring, any inline section comments, and the class-level `Supported modes:` block to reflect the new behaviour. Treat stale "not yet implemented" language anywhere in the codebase as a bug.

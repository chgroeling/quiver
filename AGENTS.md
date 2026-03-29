# AGENTS.md

## Project description
`quiver` is a high-performance Python utility that bi-directionally serializes text file directories into a strictly structured, machine-readable XML format.

## Project Structure
```text
quiver/
├── .python-version, pyproject.toml, uv.lock  # Env & Dependency management
├── AGENTS.md, README.md, LICENSE             # Docs & Guidelines
├── src/quiver/                               # Source (src-layout)
│   ├── __init__.py                           # Metadata + quiver.open()
│   ├── archive.py                            # QuiverFile, QuiverInfo, XML logic
│   ├── cli.py                                # Click CLI entry point
│   ├── logging.py                            # Debug & console config
│   └── utils/                                # Utility modules
├── tests/                                    # Pytest suite
│   ├── conftest.py                           # Fixtures
│   ├── test_archive.py, test_utils.py        # Unit tests
│   └── test_*_cli.py, test_embedding.py      # Integration tests
└── docs/                                     # MkDocs source
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
**Tools:** `ruff` (lint/fmt), `mypy` (types). Prefix cmds with `uv run`.
- **Fmt/Lint:** `ruff format [--check] src/ tests/`, `ruff check src/ tests/`
- **Types:** `mypy src/`
- **Pre-Commit Gate:** `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest`

### Tests (`uv run pytest`)
- **Exec:** `.` (all), `-v` (verbose), `tests/[file].py[::func]` (targeted), `--cov=quiver --cov-report=html` (coverage).
- **Structure:** `tests/` dir 1:1 mapping (`archive.py`->`test_archive.py`, `utils/__init__.py`->`test_utils.py`). `cli.py` splits to `test_cli.py` (smoke), `test_[create|extract|add]_cli.py`, `test_embedding.py`.
- **FS Rules:** Prioritize critical paths. Use `tmp_path`. Name staging dirs `project/` (avoids `src/src/` nesting).
- **Paths:** Stored paths include top-level prefix (`project/src/main.py`). Assert via `endswith()` or `rglob()`.
- **Upserts:** Merges compare **full stored paths**. Triggers require replacement file added from a dir matching original `add()` root.

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
- **Typing:** Strict `mypy` for `src/`; relaxed for `tests/`.
- **Type Aliases:** Use PEP 695 `type X = ...` (Python 3.12+). **Avoid** `TypeAlias` (ruff `UP040`).
- **Format:** PEP8 via `ruff`; 100 char limit.
- **Testing:** ≥1 unit test/function; use `tmp_path` for FS.
- **UI/Logging:** CLI silent by default. Use `structlog` for internal debug logs and `rich` for verbose user feedback. **Strictly isolate** UI output from internal loggers.

### Import Rules (ruff)
- **Order (I001):** stdlib → third-party → local. Separate with one blank line. Run `uv run ruff check --fix` or `format` to resolve.
- **Unused Imports (F401):** Remove immediately; every import must be referenced.
- **`TYPE_CHECKING` (TC005):** Delete empty `if TYPE_CHECKING: pass` blocks; use only if containing symbols.
- **Async-safe I/O (ASYNC240):** Never call blocking `pathlib.Path` methods inside `async def`. Wrap with `asyncio.to_thread(path.method, ...)`.
- **Pathlib over `os` (PTH):** Use `pathlib` equivalents (e.g., `Path.unlink()`) over `os`. Avoid `os` unless no `pathlib` alternative exists.
- **`contextlib.suppress` (SIM105):** Replace `try: ... except Error: pass` with `with contextlib.suppress(Error):`.

### mypy Rules (strict)
- **Return types:** All functions (including `__exit__`) require explicit annotations.
- **`__exit__` signature:** Use exact typing:
  ```python
  def __exit__(
      self,
      exc_type: type[BaseException] | None,
      exc_val: BaseException | None,
      exc_tb: TracebackType | None,
  ) -> None:
  ```
  Import `TracebackType` from `types` (use `if TYPE_CHECKING:` if preferred).
- **lxml types:** Use `lxml-stubs`. Annotate elements as `etree._Element` and CDATA as `etree.CDATA(...)`. Suppress internal false positives with `# type: ignore[assignment]` only when necessary.
- **`asyncio.to_thread`:** Pass bound methods directly: `asyncio.to_thread(path.read_text, encoding="utf-8")`. Avoid lambdas to preserve return-type inference.

## Python API
Public API mirrors `tarfile`. Entry: `quiver.open()`.

### `QuiverFile` (`src/quiver/archive.py`)
- **Factory**: `QuiverFile.open(name, mode, preamble=None, epilogue=None)`
- **Modes**: `'r'` (read), `'w'` (write), `'a'` (append).
- **Context Manager**: Auto-calls `close()`.
- **`add(name, arcname=None)`**:
  - Validates UTF-8 & XML-1.0 compatibility.
  - Normalizes POSIX paths; upserts `self._entries`.
  - Preserves dir name as prefix (e.g., `add("dir")` -> `dir/file.txt`) unless `arcname` provided.
  - Uses async reader/writer with bounded backpressure.
- **`add_text(arcname, content)`**:
  - Inserts an in-memory string as an archive entry (upserts by `arcname`).
  - Requires mode `'w'` or `'a'`. Useful for repack workflows.
- **`preamble` / `epilogue`** (read-only properties): Return preamble/epilogue text parsed from or supplied to the archive (`None` if absent).
- **`entries`** (read-only property): Return a defensive copy of all `(QuiverInfo, content)` pairs in the archive.
- **`close()`**: Sorts entries, builds `lxml` tree, writes to disk. **Aborts** if `__exit__` has propagating exception.
- **`getnames()` / `getmembers()`**: Returns names or `QuiverInfo` objects.
- **`extractall(path=".", members=None)`**:
  - Async pipeline extraction.
  - Validates sandbox paths.
  - Writes `PREAMBLE`/`EPILOGUE` files if non-whitespace text exists.

### `QuiverInfo`
- `name: str` (POSIX path), `size: int`.
- `isfile()`, `isdir()`.

### Exceptions
- **`BinaryFileError`**: Invalid UTF-8 or XML-1.0 forbidden chars (e.g., NULL, C0/C1). Reports up to 5 error locations.
- **`PathTraversalError`**: Absolute paths, `..`, or escape from destination sandbox.

### `quiver.__init__`
- Exports: `open`, `QuiverFile`, `QuiverInfo`, `BinaryFileError`, `PathTraversalError`, `__version__`.

## CLI
Style: `tar` (e.g., `quiver -cvf archive.xml src`)

- **-c (Create)**: `quiver -cf <archive.xml> <path...>`
- **-x (Extract)**: `quiver -xf <archive.xml> [dest]` (default: `.`)
- **-a (Add/Upsert)**: `quiver -af <archive.xml> <path...>` New/replace; maintains alpha-order. Creates if missing.
- **--delete (Delete)**: `quiver --delete -f <archive.xml> <path...>` Remove files or directory prefixes; no-op if not found. No short flag. **Implemented as a CLI-only repack** (read → filter → write to temp file → atomic rename); no `delete()` method exists on the Python API.
- **-v (Verbose)**: Enables stdout.
- **--debug**: Detailed logging (no short flag).
- **--preamble <txt|file>**: Prepend string or file content before XML.
- **--epilogue <txt|file>**: Append string or file content after XML.

**Rules:**
- Flags: `-f` must end short-flag bundles.
- Validation: Exactly one mode (`-c`, `-x`, `-a`, `--delete`) required; mutually exclusive.
- Recursion: Recursive packing for `-c`/`-a`.
- Output: Silent by default unless `-v` or `--debug`.

## Logging & UI (`src/quiver/logging.py`)
- `configure_debug_logging(enabled)`: Configures `structlog`. Use `logging.CRITICAL` (50) for no-op. **Avoid `logging.CRITICAL + 1`** (causes `KeyError`).
- `get_console(verbose)`: Returns `rich.Console()`. Verbose writes to **stdout** for `CliRunner` capture; otherwise `quiet=True`.

### structlog Rules
- **Init**: Use `structlog.get_logger(__name__)`. **Never** `logging.getLogger()`.
- **Context**: Use kwargs: `logger.debug("msg", k=v)`. **Never** `extra={...}` (crashes on reserved keys like `name`).

### Established log fields in `archive.py`
| Call site                         | Fields                   |
| --------------------------------- | ------------------------ |
| `QuiverFile.__init__`             | `archive_name=`, `mode=` |
| `QuiverFile.add`                  | `entry_path=`, `size=`   |
| `QuiverFile.close`                | `archive_name=`          |
| `_ExtractPipeline._writer_worker` | `entry_path=`, `size=`   |

## Architecture & Mechanisms
- **CLI:** Tar-style bundled flags (`-cvf`) via custom pre-processing.
- **Concurrency/OOM:** Asyncio/threading, bounded Reader/Writer queues, chunk-streaming large files.
- **Single Writer:** Dedicated task for XML output ensures determinism, Git-friendliness, & prevents deadlocks.
- **Normalization:** POSIX paths. Entries sorted alphabetically in XML by *full stored path*. Tar semantics: `add("mydir")` prepends dir (`mydir/file.txt`); `arcname` overrides. Edge case `Path(".").name == ""` uses `.resolve().name`.
- **Security (L1):** Sandboxed unpack. `_validate_extraction_path()` rejects absolute & `../` pre-resolution; validates inside dest via `Path.relative_to()`.
- **Content Validation (L2):** Checks inside `_read_text_file[_async]()` raise `BinaryFileError`: 1) `_decode_utf8()` (rejects non-UTF-8). 2) `_validate_xml_compatible()` (rejects `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]`, max 5 errors reported) preventing late `lxml` serialization crash.
- **Extraction (`_ExtractPipeline`, L3.5):** Bounded `asyncio.Queue` streams `(path, content)` to concurrent workers that validate, create dirs (`to_thread`), and write (`aiofile`). Isolated XML parsed via `lxml.etree.fromstring`.
- **Transactions (`__exit__`):** If `exc_type` exists, `QuiverFile.__exit__` sets `self._closed = True` without `close()`. Prevents corrupt/truncated archive on failed `add()` (matches `tarfile`).
- **Modes ('r'/'a') & Upsert:** Open `'r'` or `'a'` parses archive (`_parse_archive()`), populating `_entries`, `_preamble`, `_epilogue` (`'r'` raises `FileNotFoundError` if missing). Upserts are in-RAM replace/append via `add()`; `close()` behaves like `'w'` mode. *Do not re-introduce separate upsert pipeline.*
- **Delete (CLI repack):** `--delete` is not a Python API method. The CLI opens the archive in `'r'` mode, reads `entries`/`preamble`/`epilogue` via public properties, filters entries, writes the result to a sibling temp file via `'w'` mode + `add_text()`, then atomically replaces the original with `os.replace()`. No partial writes can corrupt the archive.
- **Embedded Text & Sentinels (L0):** `_split_archive_text()` isolates *first* `<archive>`...`</archive>`; rest is preamble/epilogue (first-match rule). `_PREAMBLE_SENTINEL = "\n"`, `_EPILOGUE_SENTINEL = ""`. Sync required if changed.
- **XML Specs:** Unescaped `<![CDATA[...]]>`, no entity encoding. `<directory_tree>` is first child, CDATA-wrapped (`"\n" + tree_text + "\n"`), `"."` if empty.
- **lxml Artifact:** `pretty_print=True` appends `\n` after closing tag. `_split_archive_text` unconditionally strips exactly 1 leading `\n` from epilogue for verbatim round-trip. `xml_content` ends with `>`, not `\n`.


## Docstring Rules
- **Format:** Google Style (`Args:`, `Returns:`, `Raises:`).
- **Markup:** Markdown ONLY; NO reST/Sphinx directives (`:class:`, etc.).
- **Code/Links:** Backticks (single inline, triple block). MkDocs autorefs (`[MyClass][]`).
- **Types:** Rely on Python type hints; do not duplicate in docstrings.
- **Style:** PEP 257 imperative mood ("Return X", not "Returns X").
- **Length:** One-liners for simple/private. Multi-line/sections ONLY for complex/public APIs. Omit redundant `Args:`/`Returns:`.
- **Staleness:** Always update docstrings, inline comments, and class `Supported modes:` when implementing scaffolds. Treat stale "not yet implemented" text as a bug.
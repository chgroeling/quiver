# AGENTS.md

## Project description
`mdbox` is a high-performance Python utility that bi-directionally serializes text file directories into a strictly structured, machine-readable XML format.

## Project Structure
```text
mdbox/
├── .python-version, pyproject.toml, uv.lock  # Env & Dependency management
├── AGENTS.md, README.md, LICENSE             # Docs & Guidelines
├── src/mdbox/                               # Source (src-layout)
│   ├── __init__.py                           # Metadata + mdbox.open()
│   ├── archive.py                            # MdBoxFile, MdBoxInfo, XML logic
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
- **Run:** `uv run [mdbox|python script.py|tool] [args]`.
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
- **Exec:** `.` (all), `-v` (verbose), `tests/[file].py[::func]` (targeted), `--cov=mdbox --cov-report=html` (coverage).
- **Structure:** `tests/` dir 1:1 mapping (`archive.py`->`test_archive.py`, `utils/__init__.py`->`test_utils.py`). `cli.py` splits to `test_cli.py` (smoke), `test_[create|extract|add]_cli.py`, `test_embedding.py`.
- **FS Rules:** Prioritize critical paths. Use `tmp_path`. Name staging dirs `project/` (avoids `src/src/` nesting).
- **Paths:** Stored paths include top-level prefix (`project/src/main.py`). Assert via `endswith()` or `rglob()`.
- **Upserts:** Merges compare **full stored paths**. Triggers require replacement file added from a dir matching original `write()` root.
- **Public API only:** Never import or call private symbols (names starting with `_`) from `src/` in tests. Test behaviour exclusively through the public API.
- **No inline imports:** All imports must be at the top of the test file. `import` statements inside test functions are forbidden.

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
Public API mirrors `zipfile`. Entry: `mdbox.open()`.

### `MdBoxFile` (`src/mdbox/archive.py`)
- **Factory**: `MdBoxFile.open(name, mode, preamble=None, epilogue=None)`
- **Modes**: `'r'` (read), `'w'` (write).
- **Context Manager**: Auto-calls `close()`.
- **`write(name, arcname=None)`**:
  - Validates UTF-8 & XML-1.0 compatibility.
  - Normalizes POSIX paths; upserts in-memory metadata/content caches.
  - Preserves dir name as prefix (e.g., `write("dir")` -> `dir/file.txt`) unless `arcname` provided.
  - Caches file text so serialization can stream without re-reading the filesystem.
- **`writestr(arcname, content)`**:
  - Inserts an in-memory string as an archive entry (upserts by `arcname`).
  - Requires mode `'w'`. Useful for repack workflows.
- **`preamble` / `epilogue`** (read-only properties): Return preamble/epilogue text parsed from or supplied to the archive (`None` if absent).
- **`close()`**: Sorts entries, builds XML, writes to disk. **Aborts** if `__exit__` has propagating exception.
- **`namelist()` / `infolist()`**: Zipfile-style metadata accessors (names or `MdBoxInfo` objects).
- **`read(member)`**: Returns text content for a stored path or `MdBoxInfo`. Lazily seeks/reads from disk in `'r'` mode; uses in-memory cache in `'w'` mode.
- **`read(member)`**: Returns text content for a stored path or `MdBoxInfo`. Slices the cached archive memoryview in `'r'` mode; uses in-memory cache in `'w'` mode.
- **Iteration**: `for info in MdBoxFile:` yields `MdBoxInfo` objects (no content) matching `zipfile.ZipFile` semantics.
- **`extractall(path=".", members=None)`**:
  - Async pipeline extraction with partitioned async writers.
  - Reads entry payloads from the cached archive memoryview; only writes hit the filesystem.
  - Validates sandbox paths.
  - Writes `PREAMBLE`/`EPILOGUE` files if non-whitespace text exists.

### `MdBoxInfo`
- `name: str` (POSIX path), `size: int`.
- `isfile()`, `isdir()`.

### Exceptions
- **`BinaryFileError`**: Invalid UTF-8 or XML-1.0 forbidden chars (e.g., NULL, C0/C1). Reports up to 5 error locations.
- **`PathTraversalError`**: Absolute paths, `..`, or escape from destination sandbox.

### `mdbox.__init__`
- Exports: `open`, `MdBoxFile`, `MdBoxInfo`, `BinaryFileError`, `PathTraversalError`, `__version__`.

## CLI
Style: `tar` (e.g., `mdbox -cvf archive.xml src`)

- **-c (Create)**: `mdbox -cf <archive.xml> <path...>`
- **-x (Extract)**: `mdbox -xf <archive.xml> [dest]` (default: `.`)
- **-a (Add/Upsert)**: `mdbox -af <archive.xml> <path...>` New/replace; maintains alpha-order. Creates if missing. **Implemented as a CLI-only repack** (read → merge → write to temp file → atomic rename); no `'a'` mode exists on the Python API.
- **--delete (Delete)**: `mdbox --delete -f <archive.xml> <path...>` Remove files or directory prefixes; no-op if not found. No short flag. **Implemented as a CLI-only repack** (read → filter → write to temp file → atomic rename); no `delete()` method exists on the Python API.
- **-v (Verbose)**: Enables stdout.
- **--debug**: Detailed logging (no short flag).
- **--preamble <txt|file>**: Prepend string or file content before XML.
- **--epilogue <txt|file>**: Append string or file content after XML.

**Rules:**
- Flags: `-f` must end short-flag bundles.
- Validation: Exactly one mode (`-c`, `-x`, `-a`, `--delete`) required; mutually exclusive.
- Recursion: Recursive packing for `-c`/`-a`.
- Output: Silent by default unless `-v` or `--debug`.

## Logging & UI (`src/mdbox/logging.py`)
- `configure_debug_logging(enabled)`: Configures `structlog`. Use `logging.CRITICAL` (50) for no-op. **Avoid `logging.CRITICAL + 1`** (causes `KeyError`).
- `get_console(verbose)`: Returns `rich.Console()`. Verbose writes to **stdout** for `CliRunner` capture; otherwise `quiet=True`.

### structlog Rules
- **Init**: Use `structlog.get_logger(__name__)`. **Never** `logging.getLogger()`.
- **Context**: Use kwargs: `logger.debug("msg", k=v)`. **Never** `extra={...}` (crashes on reserved keys like `name`).

### Established log fields in `archive.py` and `cli.py`
| Call site                         | Fields                                        |
| --------------------------------- | --------------------------------------------- |
| `MdBoxFile.__init__`             | `archive_name=`, `mode=`                      |
| `MdBoxFile.__init__` (parse)     | `archive_name=`, `elapsed_s=`, `entry_count=` |
| `MdBoxFile.write`                | `entry_path=`, `size=`                        |
| `MdBoxFile.close`                | `archive_name=`, `elapsed_s=`, `entry_count=` |
| `_PackPipeline.run`               | `elapsed_s=`, `file_count=`, `total_bytes=`   |
| `_ExtractPipeline.run`            | `elapsed_s=`, `file_count=`, `total_bytes=`   |
| `_ExtractPipeline._writer_worker` | `entry_path=`, `size=`                        |
| `_run_delete` (cli.py)            | `elapsed_s=`, `deleted_count=`, `kept_count=` |
| `_run_add` (cli.py)               | `elapsed_s=`, `entry_count=`                  |

## Architecture & Mechanisms
- **CLI:** Tar-style bundled flags (`-cvf`) via custom pre-processing.
- **Concurrency/OOM:** Asyncio/threading, bounded Reader/Writer queues, chunk-streaming large files.
- **Single Writer:** Dedicated task for XML output ensures determinism, Git-friendliness, & prevents deadlocks.
- **Normalization:** POSIX paths. Entries sorted alphabetically in XML by *full stored path*. Tar semantics: `write("mydir")` prepends dir (`mydir/file.txt`); `arcname` overrides. Edge case `Path(".").name == ""` uses `.resolve().name`.
- **Security (L1):** Sandboxed unpack. `_validate_extraction_path()` rejects absolute & `../` pre-resolution; validates inside dest via `Path.relative_to()`.
- **Content Validation (L2):** Checks inside `_read_text_file[_async]()` raise `BinaryFileError`: 1) `_decode_utf8()` (rejects non-UTF-8). 2) `_validate_xml_compatible()` (rejects `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]`, max 5 errors reported) preventing late `lxml` serialization crash.
- **Extraction (`_ExtractPipeline`, L3.5):** Bounded `asyncio.Queue` streams `(path, content)` to concurrent workers that validate, create dirs (`to_thread`), and write (`aiofile`). Isolated XML parsed via `lxml.etree.fromstring`.
- **Transactions (`__exit__`):** If `exc_type` exists, `MdBoxFile.__exit__` sets `self._closed = True` without `close()`. Prevents corrupt/truncated archive on failed `write()` (matches `tarfile`).
- **Modes ('r'/'w'):** Open `'r'` parses archive (`_parse_archive()`), recording offsets/lengths plus `_preamble`/`_epilogue` (raises `FileNotFoundError` if missing). Open `'w'` starts with empty metadata/content caches; `close()` serializes them to disk.
- **Add/Upsert (CLI repack):** `-a` is not a Python API mode. The CLI opens the archive in `'r'` mode, streams metadata via iteration + `read()`, replays entries into a `'w'`-mode temp file via `writestr()`, adds new inputs via `add()`, then atomically replaces the original with `os.replace()`. When the archive does not exist, `-a` degrades to a plain `'w'` create. No partial writes can corrupt the archive.
- **Delete (CLI repack):** `--delete` is not a Python API method. The CLI opens the archive in `'r'` mode, filters metadata using iteration + `read()`, writes the result to a sibling temp file via `'w'` mode + `writestr()`, then atomically replaces the original with `os.replace()`. No partial writes can corrupt the archive.
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

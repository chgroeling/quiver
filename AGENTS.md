# AGENTS.md

## Project Context
You are developing `quiver`, a high-performance, cross-platform Python package and command-line tool. Its core purpose is packing and unpacking text files into a strictly formatted, machine-readable XML format.

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
│   ├── __init__.py              # Package metadata (__version__)
│   ├── cli.py                   # Click CLI entry point
│   └── utils/                   # Utility modules
│       └── __init__.py
│
├── tests/                       # Test suite
│   ├── __init__.py
│   ├── conftest.py              # Pytest fixtures and configuration
│   └── test_cli.py              # CLI tests
│
└── docs/                        # MkDocs documentation
    └── index.md                 # Documentation home page
```

## Development Workflows

### Installation & Environment Setup
* **Sync dependencies:** `uv sync` - Install all dependencies from uv.lock
* **Sync with extras:** `uv sync --all-extras` - Install including dev and docs dependencies
* **Update dependencies:** `uv lock --upgrade` - Update uv.lock with latest compatible versions

### Dependency Management
* **Version Strategy:** Use minimum version constraints (e.g., `click>=8.1.0`) in `pyproject.toml` instead of pinning exact versions. This prevents downstream conflicts for users, while our `uv.lock` file still guarantees reproducible builds during development.
* **Add runtime dependency:** `uv add <package>`
* **Add dev dependency:** `uv add --dev <package>`
* **Remove dependency:** `uv remove <package>`
* **Show installed packages:** `uv pip list`

### Running Commands
* **Run CLI:** `uv run quiver [command]` - Execute the quiver CLI
* **Run Python scripts:** `uv run python script.py` - Run Python with project dependencies
* **Run any tool:** `uv run [tool] [args]` - Execute any installed tool (pytest, mypy, ruff, etc.)

### Building & Publishing
* **Build package:** `uv build` - Creates wheel and sdist in dist/
* **Publish to PyPI:** `uv publish` - Upload to PyPI (requires credentials)

## Testing & Quality Assurance

### Code Quality Checks
We exclusively use `ruff` for both linting and formatting because it replaces multiple legacy tools (black, flake8, isort), centralizes configuration, and is significantly faster.
* **Lint code:** `uv run ruff check src/ tests/`
* **Format code:** `uv run ruff format src/ tests/`
* **Check formatting:** `uv run ruff format --check src/ tests/`
* **Type check:** `uv run mypy src/`

### Pre-Commit Quality Gate
Before committing, run this sequence to ensure all checks pass:
```bash
uv run ruff format src/ tests/     # Format code
uv run ruff check src/ tests/      # Lint code
uv run mypy src/                   # Type check
uv run pytest                      # Run tests
```

### Running Tests
* **Run all tests:** `uv run pytest`
* **Run with verbose output:** `uv run pytest -v`
* **Run specific test file:** `uv run pytest tests/test_cli.py`
* **Run specific test function:** `uv run pytest tests/test_cli.py::test_cli_version`
* **Run with coverage:** `uv run pytest --cov=quiver --cov-report=html`

### Test Structure
* Tests are located in `tests/` directory
* Use `pyfakefs` fixture (`fake_fs`) for filesystem mocking
* Each module should have corresponding test file (e.g., `cli.py` → `test_cli.py`)
* Aim for high test coverage, especially for critical paths

## Workflow & Git Rules
Strict rules apply to version control and the development workflow in this project:
* **Semantic Versioning (SemVer):** All versioning must strictly follow the SemVer standard (`MAJOR.MINOR.PATCH`).
* **Conventional Commits:** All commit messages must adhere to the Conventional Commits standard (e.g., `feat: ...`, `fix: ...`, `chore: ...`, `refactor: ...`).
* **Commits Only on Request:** **Never** commit code autonomously. Commits must only be executed when the user explicitly requests them.

## Technology Stack
Exclusively use the following technologies and libraries for implementation:
* **Language:** Python 3.12.3
* **Environment & Packaging:** `uv`, `pyproject.toml`
* **Build System:** `hatchling`
* **CLI & UI:** `click` (Framework), `rich` (User feedback, verbose mode)
* **Logging:** `structlog` (Internal, structured debug logging)
* **XML & I/O:** `lxml` (Processing), `aiofile` (Asynchronous I/O)
* **Quality Assurance:** `ruff` (Linter/Formatter), `mypy` (Strict mode typechecking)
* **Testing:** `pytest`, `pyfakefs` (Mocking), `pytest-benchmark`
* **Documentation:** `mkdocs` with the Material theme. This provides a fast, Markdown-based, and visually appealing site that is easily hosted on GitHub Pages.

## Coding Standards
* **Strict Typing:** The codebase must be statically typed. Apply strict `mypy` rules to the `src/` directory to ensure production reliability, but relax these rules for the `tests/` directory to allow flexibility for mocking and fixtures without unnecessary boilerplate.
* **Formatting:** The code must strictly follow PEP8 guidelines, enforced by `ruff`.
* **Testing:** For every written function, at least one unit test (preferably more for edge cases) must exist. Use `pyfakefs` to mock file system operations in tests.
* **Separation of UI and Logging:**
    * The CLI is "silent by default".
    * Internal logging is handled **only** via `structlog` and is disabled by default. It is only activated if an explicit debug flag is passed in the CLI.
    * User feedback (progress, generic outputs) is handled **only** via `rich` and is only activated if a verbose flag is passed. Never mix UI outputs with the internal logger.

## Architecture & Internal Mechanisms
* **CLI Structure:** Use Click's group/command pattern to naturally separate `pack` and `unpack` into subcommands. This aligns with modern CLI UX, keeps help text organized, and makes adding future commands straightforward.
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

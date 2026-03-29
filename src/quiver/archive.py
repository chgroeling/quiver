"""Core archive API: QuiverFile and QuiverInfo.

Provides a tarfile-like interface for packing and unpacking text files
into the quiver XML archive format.

Internal layer layout (top → bottom, no upward imports):

    Layer 0 — Constants & Exceptions
    Layer 1 — Path Normalization   (pure, no I/O)
    Layer 2 — I/O                 (file reading, directory walking)
    Layer 3 — Async Pack Pipeline  (_PackPipeline)
    Layer 3.5 — Async Extract Pipeline (_ExtractPipeline)
    Layer 4 — XML Serialization   (string-building + regex, no asyncio)
    Layer 5 — Public API          (QuiverInfo, QuiverFile)
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

import structlog
from aiofile import async_open

from quiver.utils import build_directory_tree

if TYPE_CHECKING:
    from types import TracebackType

logger = structlog.get_logger(__name__)

# ===========================================================================
# Layer 0 — Constants & Exceptions
# ===========================================================================

VALID_MODES = frozenset({"r", "w"})
ARCHIVE_VERSION = "1.0"
MAX_DIRECTORY_READERS = 8
QUEUE_MAXSIZE = 64

# Sentinels written at the preamble→XML and XML→epilogue seams.
# Defined as constants so the marker can be changed in one place.
# _PREAMBLE_SENTINEL is appended after the preamble so that <archive> always
# starts on its own line.  _EPILOGUE_SENTINEL is empty because _build_xml_str
# always ends the XML block with \n (after </archive>), which acts as the
# natural separator before the epilogue.
_PREAMBLE_SENTINEL = "\n"
_EPILOGUE_SENTINEL = ""


class BinaryFileError(ValueError):
    """Raised when a file cannot be read as valid UTF-8 text."""


class PathTraversalError(ValueError):
    """Raised when a stored archive path would escape the extraction destination."""


# ===========================================================================
# Layer 1 — Path Normalization
# ===========================================================================


def _normalize_stored_path(path_value: str) -> str:
    """Normalize a stored archive path value to clean relative POSIX format."""
    posix = PurePosixPath(path_value)
    parts = [part for part in posix.parts if part not in ("/", "..")]
    return str(PurePosixPath(*parts)) if parts else posix.name


def _normalize_path(path: Path) -> str:
    """Normalize *path* to a clean POSIX string suitable for archive storage.

    - Converts backslashes to forward slashes.
    - Strips any leading `/` (makes the path relative).
    - Collapses `..` components to prevent traversal issues.

    Args:
        path: The filesystem path to normalize.

    Returns:
        A clean, relative POSIX path string.
    """
    return _normalize_stored_path(path.as_posix())


def _directory_stored_path(relative: Path, arcname: str | None) -> str:
    """Build the stored archive path for a file relative to a packed directory."""
    relative_path = _normalize_path(relative)
    if arcname is None:
        return relative_path
    return _normalize_stored_path(str(PurePosixPath(arcname) / relative_path))


def _validate_extraction_path(stored_path: str, destination: Path) -> Path:
    """Validate *stored_path* against *destination* and return the resolved target.

    Rejects absolute paths, any `..` component, and any path that resolves
    outside the destination sandbox.

    Args:
        stored_path: The path attribute read from the archive.
        destination: The resolved absolute extraction root directory.

    Returns:
        The resolved, sandboxed absolute `Path` for the output file.

    Raises:
        PathTraversalError: If *stored_path* is absolute, contains `..`,
            or escapes *destination* after resolution.
    """
    # Reject absolute paths and explicit traversal components before any
    # filesystem interaction.
    posix = PurePosixPath(stored_path)
    if posix.is_absolute():
        raise PathTraversalError(
            f"Archive entry {stored_path!r} is an absolute path and cannot be extracted safely."
        )
    if ".." in posix.parts:
        raise PathTraversalError(
            f"Archive entry {stored_path!r} contains '..' and cannot be extracted safely."
        )

    # Normalize to the local platform path, then resolve against destination.
    local_path = Path(stored_path)
    resolved = (destination / local_path).resolve()

    # Ensure the resolved path is still inside the destination sandbox.
    try:
        resolved.relative_to(destination)
    except ValueError as exc:
        raise PathTraversalError(
            f"Archive entry {stored_path!r} resolves outside the destination directory."
        ) from exc

    return resolved


# ===========================================================================
# Layer 2 — I/O
# ===========================================================================


def _decode_utf8(raw: bytes, path: Path) -> str:
    """Decode *raw* bytes as UTF-8 or raise `BinaryFileError`.

    Args:
        raw: Raw bytes to decode.
        path: Source path used only in the error message.

    Returns:
        Decoded string.

    Raises:
        BinaryFileError: If *raw* is not valid UTF-8.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(
            f"File {str(path)!r} is not valid UTF-8 text and cannot be packed."
        ) from exc


# XML 1.0 forbidden characters: everything except #x9 | #xA | #xD | [#x20-#xD7FF] |
# [#xE000-#xFFFD] | [#x10000-#x10FFFF].  In practice this means NULL bytes and
# the C0/C1 control characters that are not tab/LF/CR.
_XML_FORBIDDEN_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]")
_MAX_REPORTED_OFFENCES = 5


def _validate_xml_compatible(content: str, label: str) -> None:
    """Raise `BinaryFileError` if *content* contains XML-1.0-forbidden characters.

    Scans for NULL bytes and control characters that are legal UTF-8 but rejected
    by the CDATA serializer.  Reports up to `_MAX_REPORTED_OFFENCES` locations
    (line number, column, hex value) so the caller can locate the offending bytes.

    Args:
        content: Decoded text to validate.
        label: Human-readable identifier (file path or arcname) used in the error message.

    Raises:
        BinaryFileError: If any forbidden character is found, with a message that
            includes *label* and the location of each offending character.
    """
    offences: list[str] = []
    for match in _XML_FORBIDDEN_RE.finditer(content):
        if len(offences) >= _MAX_REPORTED_OFFENCES:
            break
        pos = match.start()
        line = content.count("\n", 0, pos) + 1
        col = pos - content.rfind("\n", 0, pos)  # 1-based; rfind returns -1 before first \n
        char = match.group()
        offences.append(f"  line {line}, col {col}: \\x{ord(char):02x}")

    if offences:
        locations = "\n".join(offences)
        raise BinaryFileError(
            f"File {label!r} contains XML-incompatible control characters "
            f"and cannot be packed.\n{locations}"
        )


def _read_text_file(path: Path) -> str:
    """Read *path* as UTF-8 text.

    Args:
        path: File to read.

    Returns:
        The file content as a string.

    Raises:
        BinaryFileError: If the file cannot be decoded as UTF-8 or contains
            XML-incompatible control characters.
    """
    content = _decode_utf8(path.read_bytes(), path)
    _validate_xml_compatible(content, str(path))
    return content


async def _read_text_file_async(path: Path) -> str:
    """Asynchronously read *path* as UTF-8 text.

    Raises:
        BinaryFileError: If the file cannot be decoded as UTF-8 or contains
            XML-incompatible control characters.
    """
    async with async_open(path, "rb") as afp:
        raw = cast("bytes", await afp.read())
    content = _decode_utf8(raw, path)
    _validate_xml_compatible(content, str(path))
    return content


def _collect_directory_files(directory_path: Path) -> list[Path]:
    """Collect all regular files under *directory_path* recursively."""
    return sorted(path for path in directory_path.rglob("*") if path.is_file())


# Matches the opening tag of the first <archive ...> element.
_ARCHIVE_OPEN_RE = re.compile(r"<archive(?:\s[^>]*)?>")
# Matches the first </archive> closing tag.
_ARCHIVE_CLOSE_RE = re.compile(r"</archive>")


def _split_archive_text(raw: str) -> tuple[str, str, str]:
    """Split *raw* file content into preamble, XML, and epilogue.

    Locates the first `<archive ...>` opening tag and the first `</archive>`
    closing tag to isolate the embedded XML block.  Everything before the
    opening tag is the preamble; everything after the closing tag is the
    epilogue.

    Args:
        raw: Full text content of the archive file.

    Returns:
        A ``(preamble, xml_content, epilogue)`` triple where *xml_content*
        is the full `<archive>…</archive>` string.

    Raises:
        ValueError: If no `<archive>` or `</archive>` tag can be found.
    """
    open_match = _ARCHIVE_OPEN_RE.search(raw)
    if open_match is None:
        raise ValueError("No <archive> element found in file.")

    close_match = _ARCHIVE_CLOSE_RE.search(raw, open_match.start())
    if close_match is None:
        raise ValueError("No </archive> closing tag found in file.")

    preamble = raw[: open_match.start()]
    xml_content = raw[open_match.start() : close_match.end()]
    epilogue = raw[close_match.end() :]

    # Remove the sentinels injected by _write_archive, but only when the
    # surrounding text is non-empty (no sentinel is written for absent
    # preamble/epilogue, so stripping is skipped symmetrically).  Also skip
    # stripping when the sentinel itself is empty (no-op sentinel).
    if _PREAMBLE_SENTINEL and preamble and preamble.endswith(_PREAMBLE_SENTINEL):
        preamble = preamble[: -len(_PREAMBLE_SENTINEL)]
    if _EPILOGUE_SENTINEL and epilogue and epilogue.startswith(_EPILOGUE_SENTINEL):
        epilogue = epilogue[len(_EPILOGUE_SENTINEL) :]
    # _build_xml_str always appends \n after </archive>.  That \n lands at
    # the start of the epilogue slice (not inside xml_content) and is a
    # formatting artifact, not user content.  Strip exactly one leading \n so
    # the epilogue round-trips verbatim.  Only strip when epilogue is non-empty
    # to keep the no-epilogue path a no-op.
    if epilogue.startswith("\n"):
        epilogue = epilogue[1:]

    return preamble, xml_content, epilogue


type _ParseResult = tuple[list[tuple[str, str]], str, str]


# Matches a single <file path="...">…</file> block.  The content CDATA is
# captured lazily (re.DOTALL) so that multiple entries do not bleed together.
_FILE_ENTRY_RE = re.compile(
    r'<file\s+path="([^"]*)">'  # group 1 — stored path attribute
    r"\s*<content>"  # opening <content> tag
    r"(?:<!\[CDATA\[(.*?)\]\]>|"  # group 2 — CDATA block (may be absent)
    r"([^<]*))"  # group 3 — plain text (empty element)
    r"\s*</content>"  # closing </content> tag
    r"\s*</file>",  # closing </file> tag
    re.DOTALL,
)


def _unescape_cdata(raw_cdata: str) -> str:
    """Reverse the CDATA split applied by `_escape_cdata`.

    A sequence ``]]]]><![CDATA[>`` is the standard way to embed ``]]>`` inside
    a CDATA section by splitting it into two adjacent sections.  Collapse those
    split-points back to the original ``]]>`` string.
    """
    return raw_cdata.replace("]]]]><![CDATA[>", "]]>")


def _parse_archive(archive_path: str) -> _ParseResult:
    """Parse a quiver XML archive and return its file entries with surrounding text.

    Reads the raw file, splits out any preamble and epilogue via
    [_split_archive_text][], then extracts ``<file>`` entries from the XML
    block using regex — no XML parser required.

    Args:
        archive_path: Filesystem path to the quiver XML archive.

    Returns:
        A tuple of ``(entries, preamble, epilogue)`` where *entries* is a list
        of ``(stored_path, content)`` pairs in document order, *preamble* is
        the raw text before the first ``<archive>`` tag, and *epilogue* is the
        raw text after the first ``</archive>`` tag.

    Raises:
        FileNotFoundError: If *archive_path* does not exist.
        ValueError: If no ``<archive>`` element is found.
    """
    raw = Path(archive_path).read_text(encoding="utf-8")
    preamble, xml_content, epilogue = _split_archive_text(raw)

    entries: list[tuple[str, str]] = []
    for m in _FILE_ENTRY_RE.finditer(xml_content):
        stored_path = m.group(1)
        # group 2 is set when a CDATA block is present; group 3 covers plain text.
        raw_content = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        content = _unescape_cdata(raw_content)
        entries.append((stored_path, content))

    return entries, preamble, epilogue


# ===========================================================================
# Layer 3 — Async Pack Pipeline
# ===========================================================================


class _PackPipeline:
    """Bounded-queue async pipeline that reads a directory and returns entries.

    Separates concurrency mechanics from `QuiverFile` so that the public
    class stays free of asyncio internals.

    Args:
        root_dir: Root directory to pack.
        arcname: Optional override prefix stored in the archive.
    """

    def __init__(self, root_dir: Path, arcname: str | None) -> None:
        self._root_dir = root_dir
        self._arcname = arcname
        self._results: list[tuple[QuiverInfo, str]] = []

    def run(self) -> list[tuple[QuiverInfo, str]]:
        """Execute the pipeline synchronously and return collected entries."""
        t0 = time.perf_counter()
        asyncio.run(self._run_async())
        elapsed = time.perf_counter() - t0
        total_bytes = sum(info.size for info, _ in self._results)
        logger.debug(
            "Pack pipeline completed",
            elapsed_s=round(elapsed, 4),
            file_count=len(self._results),
            total_bytes=total_bytes,
        )
        return self._results

    async def _run_async(self) -> None:
        """Coordinate bounded-queue async readers and a single writer task."""
        files = _collect_directory_files(self._root_dir)
        if not files:
            return

        worker_count = min(MAX_DIRECTORY_READERS, len(files))
        file_queue: asyncio.Queue[Path | None] = asyncio.Queue()
        data_queue: asyncio.Queue[tuple[QuiverInfo, str] | None] = asyncio.Queue(
            maxsize=QUEUE_MAXSIZE
        )

        for file_path in files:
            file_queue.put_nowait(file_path)
        for _ in range(worker_count):
            file_queue.put_nowait(None)

        writer_task = asyncio.create_task(self._writer(data_queue))
        reader_tasks = [
            asyncio.create_task(self._reader_worker(file_queue, data_queue))
            for _ in range(worker_count)
        ]

        try:
            await asyncio.gather(*reader_tasks)
        except Exception:
            for task in reader_tasks:
                task.cancel()
            await asyncio.gather(*reader_tasks, return_exceptions=True)
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
            raise

        await data_queue.put(None)
        await writer_task

    async def _reader_worker(
        self,
        file_queue: asyncio.Queue[Path | None],
        data_queue: asyncio.Queue[tuple[QuiverInfo, str] | None],
    ) -> None:
        """Read queued files and forward normalized entries to the writer."""
        while True:
            file_path = await file_queue.get()
            if file_path is None:
                file_queue.task_done()
                return
            try:
                content = await _read_text_file_async(file_path)
                relative = file_path.relative_to(self._root_dir)
                stored_path = _directory_stored_path(relative=relative, arcname=self._arcname)
                info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
                await data_queue.put((info, content))
                logger.debug("Added file", entry_path=stored_path, size=info.size)
            finally:
                file_queue.task_done()

    async def _writer(
        self,
        data_queue: asyncio.Queue[tuple[QuiverInfo, str] | None],
    ) -> None:
        """Consume entries from the data queue and accumulate them."""
        while True:
            item = await data_queue.get()
            if item is None:
                return
            self._results.append(item)


# ===========================================================================
# Layer 3.5 — Async Extract Pipeline
# ===========================================================================


class _ExtractPipeline:
    """Bounded-queue async pipeline that writes extracted files to disk.

    File writing is fully asynchronous via `aiofile`.

    Args:
        entries: List of `(stored_path, content)` pairs to extract.
        destination: Resolved absolute path of the extraction root directory.
    """

    def __init__(self, entries: list[tuple[str, str]], destination: Path) -> None:
        self._entries = entries
        self._destination = destination

    def run(self) -> None:
        """Execute the pipeline synchronously."""
        t0 = time.perf_counter()
        asyncio.run(self._run_async())
        elapsed = time.perf_counter() - t0
        total_bytes = sum(len(c.encode("utf-8")) for _, c in self._entries)
        logger.debug(
            "Extract pipeline completed",
            elapsed_s=round(elapsed, 4),
            file_count=len(self._entries),
            total_bytes=total_bytes,
        )

    async def _run_async(self) -> None:
        """Feed entries into a bounded queue and run concurrent writer workers."""
        if not self._entries:
            return

        worker_count = min(MAX_DIRECTORY_READERS, len(self._entries))
        work_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

        producer_task = asyncio.create_task(self._producer(work_queue, worker_count))
        worker_tasks = [
            asyncio.create_task(self._writer_worker(work_queue)) for _ in range(worker_count)
        ]

        try:
            await asyncio.gather(producer_task, *worker_tasks)
        except Exception:
            producer_task.cancel()
            for task in worker_tasks:
                task.cancel()
            await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
            raise

    async def _producer(
        self,
        work_queue: asyncio.Queue[tuple[str, str] | None],
        worker_count: int,
    ) -> None:
        """Feed all entries into *work_queue*, then send sentinel values."""
        for entry in self._entries:
            await work_queue.put(entry)
        for _ in range(worker_count):
            await work_queue.put(None)

    async def _writer_worker(
        self,
        work_queue: asyncio.Queue[tuple[str, str] | None],
    ) -> None:
        """Consume entries from *work_queue* and write each to disk."""
        while True:
            item = await work_queue.get()
            if item is None:
                return
            stored_path, content = item
            target = _validate_extraction_path(stored_path, self._destination)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            async with async_open(target, "w", encoding="utf-8") as afp:
                await afp.write(content)
            logger.debug(
                "Extracted file",
                entry_path=stored_path,
                size=len(content.encode("utf-8")),
            )


# ===========================================================================
# Layer 4 — XML Serialization
# ===========================================================================


def _escape_cdata(content: str) -> str:
    """Escape *content* for safe embedding inside a CDATA section.

    A CDATA section cannot contain the literal string ``]]>``.  The standard
    workaround is to split the CDATA block at that sequence, closing the
    current section and immediately opening a new one so that ``]]>`` itself
    never appears verbatim.

    Args:
        content: Raw text to embed.

    Returns:
        The text with ``]]>`` replaced by ``]]]]><![CDATA[>``.
    """
    return content.replace("]]>", "]]]]><![CDATA[>")


def _build_xml_str(entries: list[tuple[QuiverInfo, str]]) -> str:
    """Build the XML string for the archive without an XML parser.

    Entries are sorted alphabetically by their stored POSIX path. A
    ``<directory_tree>`` element is inserted as the first child of
    ``<archive>``, containing a CDATA-wrapped visual tree of all packed paths.
    Each ``<file>`` entry wraps its content in a CDATA section.

    The returned string ends with ``\n`` (after ``</archive>``) to match the
    behaviour of lxml ``pretty_print=True`` so that round-trip parsing via
    [_split_archive_text][] and [_parse_archive][] is byte-identical.

    Args:
        entries: List of `(QuiverInfo, content)` pairs.

    Returns:
        The complete ``<archive>…</archive>\n`` XML string.
    """
    sorted_entries = sorted(entries, key=lambda e: e[0].name)
    paths = [info.name for info, _ in sorted_entries]
    tree_text = build_directory_tree(paths)

    lines: list[str] = []
    lines.append(f'<archive version="{ARCHIVE_VERSION}">')
    lines.append(f"  <directory_tree><![CDATA[\n{tree_text}\n]]></directory_tree>")
    for info, content in sorted_entries:
        lines.append(f'  <file path="{info.name}">')
        lines.append(f"    <content><![CDATA[{_escape_cdata(content)}]]></content>")
        lines.append("  </file>")
    lines.append("</archive>")
    return "\n".join(lines) + "\n"


def _write_archive(
    output_path: str,
    entries: list[tuple[QuiverInfo, str]],
    preamble: str | None = None,
    epilogue: str | None = None,
) -> None:
    """Serialize the XML archive to *output_path*.

    Optionally wraps the XML with plain-text *preamble* and/or *epilogue*.
    A single newline is inserted between the preamble and the opening
    ``<archive>`` tag, and between the closing ``</archive>`` tag and the
    epilogue, so that the boundaries are always on separate lines.

    Args:
        output_path: Destination file path.
        entries: List of ``(QuiverInfo, content)`` pairs to include.
        preamble: Optional text to prepend before the XML.
        epilogue: Optional text to append after the XML.
    """
    xml_str = _build_xml_str(entries)

    parts: list[str] = []
    if preamble:
        parts.append(preamble)
        parts.append(_PREAMBLE_SENTINEL)
    parts.append(xml_str)
    if epilogue:
        parts.append(_EPILOGUE_SENTINEL)
        parts.append(epilogue)

    Path(output_path).write_text("".join(parts), encoding="utf-8")


# ===========================================================================
# Layer 5 — Public API
# ===========================================================================


class QuiverInfo:
    """Metadata for a single file entry within a quiver archive.

    Attributes:
        name: Normalized POSIX path of the file.
        size: Size of the file content in bytes.
    """

    def __init__(self, name: str, size: int) -> None:
        self.name = name
        self.size = size

    def isfile(self) -> bool:
        """Return True — all current quiver entries are files."""
        return True

    def isdir(self) -> bool:
        """Return False — directory entries are not yet supported."""
        return False

    def __repr__(self) -> str:
        return f"QuiverInfo(name={self.name!r}, size={self.size})"


class QuiverFile:
    """Central archive class for reading and writing quiver XML archives.

    Analogous to `tarfile.TarFile`. Use the [open][QuiverFile.open] factory
    method or the module-level `quiver.open` function to create instances.

    Supported modes:
        `'r'`: Open for reading; parses the archive immediately on open.
        `'w'`: Open for writing; creates or overwrites the archive.

    Example:
        ```python
        with QuiverFile.open("archive.xml", mode="w") as qf:
            qf.write("README.md")
            qf.write("src/main.py", arcname="main.py")
        ```
    """

    def __init__(
        self,
        name: str,
        mode: str = "r",
        preamble: str | None = None,
        epilogue: str | None = None,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode {mode!r}. Must be one of: {', '.join(sorted(VALID_MODES))}"
            )
        self._name = name
        self._mode = mode
        self._entries: list[tuple[QuiverInfo, str]] = []
        self._closed = False
        self._preamble: str | None = preamble
        self._epilogue: str | None = epilogue
        logger.debug("QuiverFile opened", archive_name=name, mode=mode)

        if mode == "r":
            archive_path = Path(name)
            if not archive_path.exists():
                raise FileNotFoundError(f"Archive not found: {name!r}")
            t0 = time.perf_counter()
            raw_entries, parsed_preamble, parsed_epilogue = _parse_archive(name)
            self._preamble = parsed_preamble if parsed_preamble.strip() else None
            self._epilogue = parsed_epilogue if parsed_epilogue.strip() else None
            self._entries = [
                (QuiverInfo(name=stored_path, size=len(content.encode("utf-8"))), content)
                for stored_path, content in raw_entries
            ]
            logger.debug(
                "Archive parsed",
                archive_name=name,
                elapsed_s=round(time.perf_counter() - t0, 4),
                entry_count=len(raw_entries),
            )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def open(
        name: str,
        mode: str = "r",
        preamble: str | None = None,
        epilogue: str | None = None,
    ) -> QuiverFile:
        """Open a quiver archive and return a [QuiverFile][] instance.

        Args:
            name: Path to the archive file.
            mode: `'r'` (read) or `'w'` (write).
            preamble: Optional text to prepend before the XML when writing.
                Ignored in read mode (preamble is parsed from the file).
            epilogue: Optional text to append after the XML when writing.
                Ignored in read mode (epilogue is parsed from the file).

        Returns:
            A new [QuiverFile][] instance.

        Raises:
            ValueError: If *mode* is not `'r'` or `'w'`.
        """
        return QuiverFile(name, mode, preamble=preamble, epilogue=epilogue)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> QuiverFile:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            # An exception is propagating — mark closed without writing so the
            # archive file is never touched by a partial operation.
            self._closed = True
            return
        self.close()

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def write(self, name: str, arcname: str | None = None) -> None:
        """Add a file or directory to the archive.

        Args:
            name: Path to the file or directory on disk.
            arcname: Override the path prefix stored inside the archive.
                If omitted, the normalized POSIX version of *name* is used.

        Raises:
            FileNotFoundError: If *name* does not exist.
            BinaryFileError: If *name* cannot be decoded as UTF-8 text.
            ValueError: If the archive is not open for writing, or if it has already been closed.
        """
        if self._mode != "w":
            raise ValueError(f"Cannot write files in mode {self._mode!r}. Use mode 'w'.")
        if self._closed:
            raise ValueError("Cannot write files to a closed archive.")

        file_path = Path(name)
        if not file_path.exists():
            raise FileNotFoundError(f"No such file: {name!r}")

        if file_path.is_dir():
            # When no arcname override is given, default to the directory's own
            # name so that the stored paths include it as a prefix — matching
            # tar semantics (e.g. `tar -cf a.tar mydir` stores `mydir/file`).
            # Handle the edge case where the name would be empty (e.g. Path(".")
            # or Path("/")) by falling back to the resolved directory name.
            if arcname is None:
                dir_name = file_path.name or file_path.resolve().name
                effective_arcname = dir_name if dir_name else None
            else:
                effective_arcname = arcname
            entries = _PackPipeline(root_dir=file_path, arcname=effective_arcname).run()
            # Upsert each packed entry: replace existing path matches, or append.
            existing_by_path = {info.name: i for i, (info, _) in enumerate(self._entries)}
            for new_info, new_content in entries:
                if new_info.name in existing_by_path:
                    self._entries[existing_by_path[new_info.name]] = (new_info, new_content)
                else:
                    self._entries.append((new_info, new_content))
            return

        content = _read_text_file(file_path)
        stored_path = (
            _normalize_stored_path(arcname) if arcname is not None else _normalize_path(file_path)
        )
        info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
        # Upsert: replace an existing entry with the same path, or append.
        for i, (existing_info, _) in enumerate(self._entries):
            if existing_info.name == stored_path:
                self._entries[i] = (info, content)
                logger.debug("Added file", entry_path=stored_path, size=info.size)
                return
        self._entries.append((info, content))
        logger.debug("Added file", entry_path=stored_path, size=info.size)

    def add_text(self, arcname: str, content: str) -> None:
        """Add an in-memory string as an archive entry.

        Upserts: if an entry with the same *arcname* already exists it is
        replaced; otherwise the entry is appended.  Suitable for repacking
        workflows where content is already loaded in memory.

        Args:
            arcname: POSIX path to store inside the archive.  Must be a clean
                relative path: absolute paths and any ``..`` component are
                rejected.
            content: UTF-8 text content for the entry.  Must not contain
                XML-1.0-forbidden control characters.

        Raises:
            PathTraversalError: If *arcname* is absolute or contains ``..``.
            BinaryFileError: If *content* contains XML-incompatible control
                characters.
            ValueError: If the archive is not open for writing, or if it has already been closed.
        """
        if self._mode != "w":
            raise ValueError(f"Cannot write files in mode {self._mode!r}. Use mode 'w'.")
        if self._closed:
            raise ValueError("Cannot write data to a closed archive.")

        posix = PurePosixPath(arcname)
        if posix.is_absolute():
            raise PathTraversalError(
                f"arcname {arcname!r} is an absolute path and cannot be stored safely."
            )
        if ".." in posix.parts:
            raise PathTraversalError(
                f"arcname {arcname!r} contains '..' and cannot be stored safely."
            )
        _validate_xml_compatible(content, arcname)

        stored_path = _normalize_stored_path(arcname)
        info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
        for i, (existing_info, _) in enumerate(self._entries):
            if existing_info.name == stored_path:
                self._entries[i] = (info, content)
                logger.debug("Added data", entry_path=stored_path, size=info.size)
                return
        self._entries.append((info, content))
        logger.debug("Added data", entry_path=stored_path, size=info.size)

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    @property
    def preamble(self) -> str | None:
        """Return the preamble text parsed from or supplied to the archive."""
        return self._preamble

    @property
    def epilogue(self) -> str | None:
        """Return the epilogue text parsed from or supplied to the archive."""
        return self._epilogue

    @property
    def entries(self) -> list[tuple[QuiverInfo, str]]:
        """Return a defensive copy of all `(QuiverInfo, content)` pairs."""
        return list(self._entries)

    def getnames(self) -> list[str]:
        """Return a list of archive member paths.

        Returns the paths of all files in the archive, regardless of mode.
        """
        return [info.name for info, _ in self._entries]

    def getmembers(self) -> list[QuiverInfo]:
        """Return a list of [QuiverInfo][] objects for all archive members.

        Returns metadata for all files in the archive, regardless of mode.
        """
        return [info for info, _ in self._entries]

    def extractall(
        self,
        path: str = ".",
        members: list[QuiverInfo] | None = None,
    ) -> None:
        """Extract all (or selected) archive members to *path*.

        Uses an asynchronous pipeline for concurrent file writing.
        Each entry's path is validated against *path* before extraction
        to prevent directory traversal attacks.

        Args:
            path: Destination directory. Defaults to the current directory.
            members: If given, only these [QuiverInfo][] members are extracted.
                Must be a subset of [getmembers][].

        Raises:
            ValueError: If the archive is not open in read mode.
            PathTraversalError: If any entry path escapes the destination.
            FileNotFoundError: If the destination directory cannot be created.
        """
        if self._mode != "r":
            raise ValueError(
                f"Cannot extract in mode {self._mode!r}. Open the archive with mode 'r'."
            )

        destination = Path(path).resolve()
        destination.mkdir(parents=True, exist_ok=True)

        allowed_names: set[str] | None = (
            {info.name for info in members} if members is not None else None
        )
        entries = [
            (info.name, content)
            for info, content in self._entries
            if allowed_names is None or info.name in allowed_names
        ]

        _ExtractPipeline(entries=entries, destination=destination).run()
        self._write_surrounding_text(destination)

    def _write_surrounding_text(self, destination: Path) -> None:
        """Write `PREAMBLE` and `EPILOGUE` files to *destination* if present.

        A file is only created when the corresponding text contains at least
        one non-whitespace character.

        Args:
            destination: Resolved extraction root directory.
        """
        if self._preamble and self._preamble.strip():
            (destination / "PREAMBLE").write_text(self._preamble, encoding="utf-8")
        if self._epilogue and self._epilogue.strip():
            (destination / "EPILOGUE").write_text(self._epilogue, encoding="utf-8")

    # ------------------------------------------------------------------
    # Close / flush
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the archive, writing the XML file if in write mode.

        In `'w'` mode: sorts all added entries alphabetically, builds the XML
        tree, and serializes it to the output file (creates or overwrites).
        """
        if self._closed:
            return
        self._closed = True

        if self._mode == "w":
            t0 = time.perf_counter()
            _write_archive(self._name, self._entries, self._preamble, self._epilogue)
            logger.debug(
                "Archive written",
                archive_name=self._name,
                elapsed_s=round(time.perf_counter() - t0, 4),
                entry_count=len(self._entries),
            )

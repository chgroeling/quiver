"""Core archive API: QuiverFile and QuiverInfo.

Provides a zipfile-like interface for packing and unpacking text files
into the quiver XML archive format.

Internal layer layout (top → bottom, no upward imports):

    Layer 0 — Constants & Exceptions
    Layer 1 — Path Normalization   (pure, no I/O)
    Layer 2 — I/O                 (file reading, directory walking)
    Layer 3 — Async Extract Pipeline (_ExtractPipeline)
    Layer 4 — XML Serialization   (string-building + regex, no asyncio)
    Layer 5 — Public API          (QuiverInfo, QuiverFile)
"""

from __future__ import annotations

import asyncio
import io
import re
import time
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

import structlog
from aiofile import async_open

from quiver.utils import build_directory_tree

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

logger = structlog.get_logger(__name__)

# ===========================================================================
# Layer 0 — Constants & Exceptions
# ===========================================================================

VALID_MODES = frozenset({"r", "w"})
ARCHIVE_VERSION = "1.0"
MAX_DIRECTORY_READERS = 8

# Sentinels written at the preamble→XML and XML→epilogue seams.
# Defined as constants so the marker can be changed in one place.
# _PREAMBLE_SENTINEL is appended after the preamble so that <archive> always
# starts on its own line.  _EPILOGUE_SENTINEL is empty because
# _write_archive_stream() always ends the XML block with \n (after </archive>),
# which acts as the natural separator before the epilogue.
_PREAMBLE_SENTINEL = "\n"
_EPILOGUE_SENTINEL = ""
_PREAMBLE_SENTINEL_BYTES = _PREAMBLE_SENTINEL.encode("utf-8")
_EPILOGUE_SENTINEL_BYTES = _EPILOGUE_SENTINEL.encode("utf-8")


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


def _collect_directory_files(directory_path: Path) -> list[Path]:
    """Collect all regular files under *directory_path* recursively."""
    return sorted(path for path in directory_path.rglob("*") if path.is_file())


# Matches the opening tag of the first <archive ...> element (bytes).
_ARCHIVE_OPEN_RE_BYTES = re.compile(rb"<archive(?:\s[^>]*)?>")
# Matches the first </archive> closing tag (bytes).
_ARCHIVE_CLOSE_RE_BYTES = re.compile(rb"</archive>")


def _split_archive_bytes(raw: bytes) -> tuple[str, bytes, str, int]:
    """Split *raw* bytes into preamble, XML (bytes), and epilogue.

    Returns the decoded preamble/epilogue strings alongside the raw
    `<archive>…</archive>` bytes and the byte offset where the XML block
    begins inside *raw*.
    """

    open_match = _ARCHIVE_OPEN_RE_BYTES.search(raw)
    if open_match is None:
        raise ValueError("No <archive> element found in file.")

    close_match = _ARCHIVE_CLOSE_RE_BYTES.search(raw, open_match.start())
    if close_match is None:
        raise ValueError("No </archive> closing tag found in file.")

    xml_start = open_match.start()
    xml_end = close_match.end()
    preamble_bytes = raw[:xml_start]
    xml_bytes = raw[xml_start:xml_end]
    epilogue_bytes = raw[xml_end:]

    if (
        _PREAMBLE_SENTINEL_BYTES
        and preamble_bytes
        and preamble_bytes.endswith(_PREAMBLE_SENTINEL_BYTES)
    ):
        preamble_bytes = preamble_bytes[: -len(_PREAMBLE_SENTINEL_BYTES)]
    if (
        _EPILOGUE_SENTINEL_BYTES
        and epilogue_bytes
        and epilogue_bytes.startswith(_EPILOGUE_SENTINEL_BYTES)
    ):
        epilogue_bytes = epilogue_bytes[len(_EPILOGUE_SENTINEL_BYTES) :]
    if epilogue_bytes.startswith(b"\n"):
        epilogue_bytes = epilogue_bytes[1:]

    preamble = preamble_bytes.decode("utf-8")
    epilogue = epilogue_bytes.decode("utf-8")
    return preamble, xml_bytes, epilogue, xml_start


type _ParsedEntry = tuple[str, int, int]
type _ParseResult = tuple[list[_ParsedEntry], str, str, memoryview]


# Matches a single <file path="...">…</file> block in bytes form.
_FILE_ENTRY_RE_BYTES = re.compile(
    rb'<file\s+path="([^"]*)">'  # group 1 — stored path attribute
    rb"\s*<content>"  # opening <content> tag
    rb"(?:<!\[CDATA\[(.*?)\]\]>|"  # group 2 — CDATA block (may be absent)
    rb"([^<]*))"  # group 3 — plain text (empty element)
    rb"\s*</content>"  # closing </content> tag
    rb"\s*</file>",  # closing </file> tag
    re.DOTALL,
)


def _unescape_cdata(raw_cdata: str) -> str:
    """Reverse the CDATA split applied by `_escape_cdata`.

    A sequence ``]]]]><![CDATA[>`` is the standard way to embed ``]]>`` inside
    a CDATA section by splitting it into two adjacent sections.  Collapse those
    split-points back to the original ``]]>`` string.
    """
    return raw_cdata.replace("]]]]><![CDATA[>", "]]>")


def _parse_archive_bytes(raw_bytes: bytes) -> _ParseResult:
    """Parse a quiver XML archive from raw bytes and return its file entries.

    Splits out any preamble and epilogue via [_split_archive_bytes][], then
    extracts ``<file>`` entries from the XML block using regex.

    Args:
        raw_bytes: Raw bytes of the archive file.

    Returns:
        A tuple of ``(entries, preamble, epilogue, raw_bytes_view)`` where *entries*
        is a list of ``(stored_path, length, byte_offset)`` tuples in document
        order; *preamble* is the raw text before the first ``<archive>`` tag;
        *epilogue* is the raw text after the first ``</archive>`` tag; and
        *raw_bytes_view* is a read-only :class:`memoryview` of the entire archive.

    Raises:
        ValueError: If no ``<archive>`` element is found.
    """
    preamble, xml_bytes, epilogue, xml_start = _split_archive_bytes(raw_bytes)

    entries: list[_ParsedEntry] = []
    for match in _FILE_ENTRY_RE_BYTES.finditer(xml_bytes):
        stored_path = match.group(1).decode("utf-8")
        content_group = 2 if match.group(2) is not None else 3
        raw_content_bytes = match.group(content_group) or b""
        byte_length = len(raw_content_bytes)
        local_start, _ = match.span(content_group)
        byte_start = xml_start + local_start
        entries.append((stored_path, byte_length, byte_start))

    return entries, preamble, epilogue, memoryview(raw_bytes)


# ===========================================================================
# Layer 3 — Async Extract Pipeline
# ===========================================================================


class _ExtractPipeline:
    """Async pipeline that writes extracted files to disk.

    File writing is fully asynchronous via `aiofile`, and entries are
    partitioned evenly across a bounded number of workers.

    Args:
        entries: List of archive members to extract.
        destination: Resolved absolute path of the extraction root directory.
        quiver_file: Archive instance used to read member contents.
    """

    def __init__(
        self,
        entries: list[QuiverInfo],
        destination: Path,
        *,
        quiver_file: QuiverFile,
    ) -> None:
        self._entries = entries
        self._destination = destination
        self._quiver_file = quiver_file

    async def run_async(self) -> None:
        """Execute the pipeline inside an existing event loop."""
        if not self._entries:
            return

        t0 = time.perf_counter()
        await self._run_async()
        elapsed = time.perf_counter() - t0
        total_bytes = sum(info.length for info in self._entries)
        logger.debug(
            "Extract pipeline completed",
            elapsed_s=round(elapsed, 4),
            file_count=len(self._entries),
            total_bytes=total_bytes,
        )

    def run(self) -> None:
        """Execute the pipeline with its own event loop."""
        asyncio.run(self.run_async())

    async def _run_async(self) -> None:
        """Partition entries and run concurrent writer workers."""
        worker_count = min(MAX_DIRECTORY_READERS, len(self._entries))
        chunks = [self._entries[i::worker_count] for i in range(worker_count)]
        worker_tasks = [
            asyncio.create_task(self._writer_worker(chunk)) for chunk in chunks if chunk
        ]

        try:
            await asyncio.gather(*worker_tasks)
        except Exception:
            for task in worker_tasks:
                task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            raise

    async def _writer_worker(self, entries: list[QuiverInfo]) -> None:
        """Write each entry in *entries* to disk."""
        for info in entries:
            content = self._quiver_file.readstr(info)
            target = _validate_extraction_path(info.name, self._destination)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            async with async_open(target, "w", encoding="utf-8") as afp:
                await afp.write(content)
            logger.debug(
                "Extracted file",
                entry_path=info.name,
                size=info.length,
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


# ===========================================================================
# Layer 5 — Public API
# ===========================================================================


class QuiverInfo:
    """Metadata for a single file entry within a quiver archive.

    Attributes:
        name: Normalized POSIX path of the file.
        length: Payload length in bytes as stored inside the archive.
    """

    def __init__(
        self,
        name: str,
        length: int,
        *,
        _offset: int | None = None,
    ) -> None:
        self.name = name
        self.length = length
        self._offset = _offset

    def isfile(self) -> bool:
        """Return True — all current quiver entries are files."""
        return True

    def isdir(self) -> bool:
        """Return False — directory entries are not yet supported."""
        return False

    def __repr__(self) -> str:
        return f"QuiverInfo(name={self.name!r}, length={self.length})"


class QuiverFile:
    """Central archive class for reading and writing quiver XML archives.

    Analogous to `zipfile.ZipFile`. Use the [open][QuiverFile.open] factory
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

        ```python
        with QuiverFile.open("archive.xml", mode="r") as qf:
            for info in qf:
                content = qf.read(info)
        ```
    """

    def __init__(
        self,
        name: str | IO[bytes],
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
        self._members: list[QuiverInfo] = []
        self._member_map: dict[str, QuiverInfo] = {}
        self._content_cache: dict[str, str] = {}
        self._source_map: dict[str, Path] = {}
        self._closed = False
        self._preamble: str | None = preamble
        self._epilogue: str | None = epilogue
        self._raw_bytes: memoryview | None = None
        self._fileobj: IO[bytes] | None = None
        self._path: str | None = None
        logger.debug("QuiverFile opened", archive_name=name, mode=mode)

        if mode == "r":
            name_str: str
            if isinstance(name, str):
                self._path = name
                name_str = name
                archive_path = Path(name_str)
                if not archive_path.exists():
                    raise FileNotFoundError(f"Archive not found: {name!r}")
                raw_bytes = archive_path.read_bytes()
            else:
                self._fileobj = name
                raw_bytes = name.read()
            t0 = time.perf_counter()
            raw_entries, parsed_preamble, parsed_epilogue, raw_buffer = _parse_archive_bytes(
                raw_bytes
            )
            self._preamble = parsed_preamble if parsed_preamble.strip() else None
            self._epilogue = parsed_epilogue if parsed_epilogue.strip() else None
            self._raw_bytes = raw_buffer
            for stored_path, payload_length, byte_offset in raw_entries:
                info = QuiverInfo(
                    name=stored_path,
                    length=payload_length,
                    _offset=byte_offset,
                )
                self._members.append(info)
                self._member_map[stored_path] = info
            logger.debug(
                "Archive parsed",
                archive_name=name,
                elapsed_s=round(time.perf_counter() - t0, 4),
                entry_count=len(raw_entries),
            )

        elif mode == "w":
            if isinstance(name, str):
                self._path = name
            else:
                self._fileobj = name

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def open(
        name: str | IO[bytes],
        mode: str = "r",
        preamble: str | None = None,
        epilogue: str | None = None,
    ) -> QuiverFile:
        """Open a quiver archive and return a [QuiverFile][] instance.

        Args:
            name: Path to the archive file, or a file-like object.
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
            BinaryFileError: If *name* cannot be decoded as UTF-8 text or contains
                XML-incompatible control characters. Validation occurs when the
                content is first read (either via [read][] or when [close][]
                streams the entry into the archive).
            ValueError: If the archive is not open for writing, or if it has already been closed.
        """
        if self._mode != "w":
            raise ValueError(f"Cannot write files in mode {self._mode!r}. Use mode 'w'.")
        if self._closed:
            raise ValueError("Cannot write files to a closed archive.")

        file_path = Path(name)
        if not file_path.exists():
            raise FileNotFoundError(f"No such file: {name!r}")

        resolved_path = file_path.resolve()

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
            resolved_root = resolved_path
            for child in _collect_directory_files(resolved_root):
                relative = child.relative_to(resolved_root)
                stored_path = _directory_stored_path(relative=relative, arcname=effective_arcname)
                length = child.stat().st_size
                info = QuiverInfo(name=stored_path, length=length)
                self._register_source_entry(info, child)
            return

        stored_path = (
            _normalize_stored_path(arcname) if arcname is not None else _normalize_path(file_path)
        )
        length = resolved_path.stat().st_size
        info = QuiverInfo(name=stored_path, length=length)
        self._register_source_entry(info, resolved_path)

    def writestr(self, arcname: str, content: str) -> None:
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
        info = QuiverInfo(name=stored_path, length=0)
        self._cache_entry(info, content)
        logger.debug("Added data", entry_path=stored_path, length_bytes=info.length)

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

    def namelist(self) -> list[str]:
        """Return a list of archive member paths.

        Analogous to `zipfile.ZipFile.namelist`.
        """
        return [info.name for info in self._members]

    def infolist(self) -> list[QuiverInfo]:
        """Return a list of [QuiverInfo][] objects for all archive members.

        Analogous to `zipfile.ZipFile.infolist`.
        """
        return list(self._members)

    def readstr(self, member: str | QuiverInfo) -> str:
        """Return the content of an archive member as a string.

        Analogous to `zipfile.ZipFile.read`, but returns `str` instead of
        `bytes` because quiver archives contain only UTF-8 text.

        Args:
            member: The member to read — either a stored path (`str`) or
                a [QuiverInfo][] object obtained from [infolist][] or
                iteration.

        Returns:
            The content as a string.

        Raises:
            KeyError: If no entry with the given name exists in the archive.
        """
        target = self._resolve_member(member)
        cached = self._content_cache.get(target.name)
        if cached is not None:
            return cached

        if self._mode == "w":
            try:
                return self._get_entry_content(target.name)
            except ValueError as exc:  # pragma: no cover - defensive
                raise KeyError(target.name) from exc

        if target._offset is None or self._raw_bytes is None:
            raise KeyError(target.name)

        raw_slice = self._raw_bytes[target._offset : target._offset + target.length]
        text = str(raw_slice, "utf-8")
        return _unescape_cdata(text)

    def read(self, member: str | QuiverInfo) -> bytes:
        """Return the content of an archive member as bytes.

        Args:
            member: The member to read — either a stored path (`str`) or
                a [QuiverInfo][] object obtained from [infolist][] or
                iteration.

        Returns:
            The content as bytes.

        Raises:
            KeyError: If no entry with the given name exists in the archive.
        """
        target = self._resolve_member(member)
        cached = self._content_cache.get(target.name)
        if cached is not None:
            return cached.encode("utf-8")

        if self._mode == "w":
            try:
                content = self._get_entry_content(target.name)
                return content.encode("utf-8")
            except ValueError as exc:  # pragma: no cover - defensive
                raise KeyError(target.name) from exc

        if target._offset is None or self._raw_bytes is None:
            raise KeyError(target.name)

        raw_slice = self._raw_bytes[target._offset : target._offset + target.length]
        return bytes(raw_slice)

    def __iter__(self) -> Iterator[QuiverInfo]:
        """Iterate over archive members, yielding [QuiverInfo][] objects.

        Example:
            ```python
            with quiver.open("archive.xml", mode="r") as qf:
                for info in qf:
                    content = qf.readstr(info)
            ```
        """
        return iter(self._members)

    def _resolve_member(self, member: str | QuiverInfo) -> QuiverInfo:
        if isinstance(member, QuiverInfo):
            return member
        try:
            return self._member_map[member]
        except KeyError:
            raise KeyError(member) from None

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
                Must be a subset of [infolist][].

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
        entries: list[QuiverInfo] = []
        for info in self._members:
            if allowed_names is not None and info.name not in allowed_names:
                continue
            entries.append(info)

        pipeline = _ExtractPipeline(
            entries=entries,
            destination=destination,
            quiver_file=self,
        )

        asyncio.run(pipeline.run_async())
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
        tree, lazily reads any pending filesystem sources, and serializes the
        result to the output file (creates or overwrites).
        """
        if self._closed:
            return
        self._closed = True

        if self._mode == "w":
            t0 = time.perf_counter()
            self._write_archive_stream()
            logger.debug(
                "Archive written",
                archive_name=self._name,
                elapsed_s=round(time.perf_counter() - t0, 4),
                entry_count=len(self._members),
            )

    def _cache_entry(self, info: QuiverInfo, content: str) -> None:
        """Store *content* for *info*, upserting in write mode."""

        stored_info = self._upsert_member(info)
        self._content_cache[stored_info.name] = content
        self._source_map.pop(stored_info.name, None)
        stored_info.length = len(_escape_cdata(content).encode("utf-8"))

    def _register_source_entry(self, info: QuiverInfo, source: Path) -> None:
        """Register a disk-backed entry that can be read lazily later."""

        stored_info = self._upsert_member(info)
        self._source_map[stored_info.name] = source
        self._content_cache.pop(stored_info.name, None)
        logger.debug("Added file", entry_path=stored_info.name, length_bytes=stored_info.length)

    def _upsert_member(self, info: QuiverInfo) -> QuiverInfo:
        info._offset = None
        existing = self._member_map.get(info.name)
        if existing is None:
            self._members.append(info)
            self._member_map[info.name] = info
            return info
        existing.length = info.length
        existing._offset = None
        return existing

    def _get_entry_content(self, name: str) -> str:
        """Return the text for *name*, reading from disk lazily if needed."""

        cached = self._content_cache.get(name)
        if cached is not None:
            return cached

        source = self._source_map.get(name)
        if source is None:
            raise ValueError(f"No cached content or source path for entry {name!r}")

        content = _read_text_file(source)
        self._content_cache[name] = content
        info = self._member_map[name]
        info.length = len(_escape_cdata(content).encode("utf-8"))
        return content

    def _write_archive_stream(self) -> None:
        """Serialize the archive by streaming file contents lazily."""

        sorted_infos = sorted(self._members, key=lambda info: info.name)
        paths = [info.name for info in sorted_infos]
        tree_text = build_directory_tree(paths)

        if self._fileobj is not None:
            self._write_archive_to_fileobj(self._fileobj, sorted_infos, tree_text)
        elif self._path is not None:
            output_path = Path(self._path)
            with output_path.open("w", encoding="utf-8") as fp:
                self._write_archive_to_fp(fp, sorted_infos, tree_text)

    def _write_archive_to_fileobj(
        self, fileobj: IO[bytes], sorted_infos: list[QuiverInfo], tree_text: str
    ) -> None:
        """Serialize the archive to a file-like object."""
        buffer = io.StringIO()
        self._write_archive_to_fp(buffer, sorted_infos, tree_text)
        fileobj.write(buffer.getvalue().encode("utf-8"))

    def _write_archive_to_fp(
        self, fp: IO[str], sorted_infos: list[QuiverInfo], tree_text: str
    ) -> None:
        """Serialize the archive to a text-mode file pointer."""
        if self._preamble:
            fp.write(self._preamble)
            fp.write(_PREAMBLE_SENTINEL)

        fp.write(f'<archive version="{ARCHIVE_VERSION}">\n')
        fp.write(f"  <directory_tree><![CDATA[\n{tree_text}\n]]></directory_tree>\n")

        for info in sorted_infos:
            content = self._get_entry_content(info.name)
            escaped = _escape_cdata(content)
            fp.write(
                f'  <file path="{info.name}">\n'
                f"    <content><![CDATA[{escaped}]]></content>\n"
                "  </file>\n"
            )

        fp.write("</archive>\n")

        if self._epilogue:
            fp.write(_EPILOGUE_SENTINEL)
            fp.write(self._epilogue)

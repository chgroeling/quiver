"""Core archive API: QuiverFile and QuiverInfo.

Provides a tarfile-like interface for packing and unpacking text files
into the quiver XML archive format.

Internal layer layout (top → bottom, no upward imports):

    Layer 0 — Constants & Exceptions
    Layer 1 — Path Normalization   (pure, no I/O)
    Layer 2 — I/O                 (file reading, directory walking)
    Layer 3 — Async Pack Pipeline (_PackPipeline)
    Layer 4 — XML Serialization   (lxml, no asyncio)
    Layer 5 — Public API          (QuiverInfo, QuiverFile)
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

import structlog
from aiofile import async_open
from lxml import etree

from quiver.utils import build_directory_tree

if TYPE_CHECKING:
    from types import TracebackType

logger = structlog.get_logger(__name__)

# ===========================================================================
# Layer 0 — Constants & Exceptions
# ===========================================================================

VALID_MODES = frozenset({"r", "w", "a"})
ARCHIVE_VERSION = "1.0"
MAX_DIRECTORY_READERS = 8
QUEUE_MAXSIZE = 64


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


def _read_text_file(path: Path) -> str:
    """Read *path* as UTF-8 text.

    Args:
        path: File to read.

    Returns:
        The file content as a string.

    Raises:
        BinaryFileError: If the file cannot be decoded as UTF-8.
    """
    return _decode_utf8(path.read_bytes(), path)


async def _read_text_file_async(path: Path) -> str:
    """Asynchronously read *path* as UTF-8 text.

    Raises:
        BinaryFileError: If the file cannot be decoded as UTF-8.
    """
    async with async_open(path, "rb") as afp:
        raw = cast("bytes", await afp.read())
    return _decode_utf8(raw, path)


def _collect_directory_files(directory_path: Path) -> list[Path]:
    """Collect all regular files under *directory_path* recursively."""
    return sorted(path for path in directory_path.rglob("*") if path.is_file())


def _parse_archive(archive_path: str) -> list[tuple[str, str]]:
    """Parse a quiver XML archive and return its file entries.

    Uses `lxml.etree.iterparse` to stream-parse the file, extracting the
    `path` attribute and CDATA content from each `<file>` element.

    Args:
        archive_path: Filesystem path to the quiver XML archive.

    Returns:
        A list of `(stored_path, content)` pairs in document order.

    Raises:
        FileNotFoundError: If *archive_path* does not exist.
        ValueError: If the root element is not `<archive>`.
    """
    entries: list[tuple[str, str]] = []
    context = etree.iterparse(archive_path, events=("end",), tag="file")
    for _event, elem in context:
        stored_path = elem.get("path", "")
        content_elem = elem.find("content")
        content = content_elem.text if content_elem is not None and content_elem.text else ""
        entries.append((stored_path, content))
        # Free memory for already-processed elements.
        elem.clear()
    return entries


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
        asyncio.run(self._run_async())
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

    Parsing is offloaded to a thread pool so that synchronous `lxml.iterparse`
    does not block the event loop.  File writing is fully asynchronous via
    `aiofile`.

    Args:
        entries: List of `(stored_path, content)` pairs to extract.
        destination: Resolved absolute path of the extraction root directory.
    """

    def __init__(self, entries: list[tuple[str, str]], destination: Path) -> None:
        self._entries = entries
        self._destination = destination

    def run(self) -> None:
        """Execute the pipeline synchronously."""
        asyncio.run(self._run_async())

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


def _build_xml_tree(entries: list[tuple[QuiverInfo, str]]) -> etree._Element:
    """Build the lxml element tree for the archive.

    Entries are sorted alphabetically by their stored POSIX path. A
    `<directory_tree>` element is inserted as the first child of `<archive>`,
    containing a CDATA-wrapped visual tree of all packed paths.

    Args:
        entries: List of `(QuiverInfo, content)` pairs.

    Returns:
        The `<archive>` root element.
    """
    root = etree.Element("archive", version=ARCHIVE_VERSION)

    sorted_entries = sorted(entries, key=lambda e: e[0].name)
    paths = [info.name for info, _ in sorted_entries]

    tree_text = build_directory_tree(paths)
    tree_elem = etree.SubElement(root, "directory_tree")
    tree_elem.text = etree.CDATA("\n" + tree_text + "\n")

    for info, content in sorted_entries:
        file_elem = etree.SubElement(root, "file", path=info.name)
        content_elem = etree.SubElement(file_elem, "content")
        content_elem.text = etree.CDATA(content)

    return root


def _write_archive(output_path: str, entries: list[tuple[QuiverInfo, str]]) -> None:
    """Serialize the XML archive tree to *output_path*.

    Args:
        output_path: Destination file path.
        entries: List of `(QuiverInfo, content)` pairs to include.
    """
    root = _build_xml_tree(entries)
    xml_bytes = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=False,
        encoding="unicode",
    )
    Path(output_path).write_text(xml_bytes, encoding="utf-8")


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
        `'a'`: Open for appending (not yet implemented).

    Example:
        ```python
        with QuiverFile.open("archive.xml", mode="w") as qf:
            qf.add("README.md")
            qf.add("src/main.py", arcname="main.py")
        ```
    """

    def __init__(self, name: str, mode: str = "r") -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode {mode!r}. Must be one of: {', '.join(sorted(VALID_MODES))}"
            )
        self._name = name
        self._mode = mode
        self._entries: list[tuple[QuiverInfo, str]] = []
        self._closed = False
        logger.debug("QuiverFile opened", archive_name=name, mode=mode)

        if mode == "r":
            archive_path = Path(name)
            if not archive_path.exists():
                raise FileNotFoundError(f"Archive not found: {name!r}")
            raw_entries = _parse_archive(name)
            self._entries = [
                (QuiverInfo(name=stored_path, size=len(content.encode("utf-8"))), content)
                for stored_path, content in raw_entries
            ]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def open(name: str, mode: str = "r", **kwargs: object) -> QuiverFile:  # noqa: ARG004
        """Open a quiver archive and return a [QuiverFile][] instance.

        Args:
            name: Path to the archive file.
            mode: `'r'` (read), `'w'` (write), or `'a'` (append).
            **kwargs: Reserved for future use.

        Returns:
            A new [QuiverFile][] instance.

        Raises:
            ValueError: If *mode* is not one of `'r'`, `'w'`, `'a'`.
        """
        return QuiverFile(name, mode)

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
        self.close()

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def add(self, name: str, arcname: str | None = None) -> None:
        """Add a file or directory to the archive.

        Args:
            name: Path to the file or directory on disk.
            arcname: Override the path prefix stored inside the archive.
                If omitted, the normalized POSIX version of *name* is used.

        Raises:
            FileNotFoundError: If *name* does not exist.
            BinaryFileError: If *name* cannot be decoded as UTF-8 text.
            ValueError: If the archive is not open for writing/appending,
                or if it has already been closed.
        """
        if self._mode not in {"w", "a"}:
            raise ValueError(f"Cannot add files in mode {self._mode!r}. Use mode 'w' or 'a'.")
        if self._closed:
            raise ValueError("Cannot add files to a closed archive.")

        file_path = Path(name)
        if not file_path.exists():
            raise FileNotFoundError(f"No such file: {name!r}")

        if file_path.is_dir():
            entries = _PackPipeline(root_dir=file_path, arcname=arcname).run()
            self._entries.extend(entries)
            return

        content = _read_text_file(file_path)
        stored_path = (
            _normalize_stored_path(arcname) if arcname is not None else _normalize_path(file_path)
        )
        info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
        self._entries.append((info, content))
        logger.debug("Added file", entry_path=stored_path, size=info.size)

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Close / flush
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the archive, writing the XML file if in write/append mode.

        In write/append mode, sorts all added entries alphabetically by path,
        builds the XML tree, and serializes it to the output file.
        """
        if self._closed:
            return
        self._closed = True

        if self._mode in {"w", "a"}:
            _write_archive(self._name, self._entries)
            logger.debug("Archive written", archive_name=self._name)

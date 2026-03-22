"""Core archive API: QuiverFile and QuiverInfo.

Provides a tarfile-like interface for packing and unpacking text files
into the quiver XML archive format.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

import structlog
from aiofile import async_open
from lxml import etree

if TYPE_CHECKING:
    from types import TracebackType

logger = structlog.get_logger(__name__)

VALID_MODES = frozenset({"r", "w", "a"})
ARCHIVE_VERSION = "1.0"
MAX_DIRECTORY_READERS = 8
QUEUE_MAXSIZE = 64


class BinaryFileError(ValueError):
    """Raised when a file cannot be read as valid UTF-8 text."""


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

    Analogous to :class:`tarfile.TarFile`. Use the :meth:`open` factory
    method or the module-level :func:`quiver.open` function to create instances.

    Supported modes:
        ``'r'``: Open for reading (not yet implemented).
        ``'w'``: Open for writing; creates or overwrites the archive.
        ``'a'``: Open for appending (not yet implemented).

    Example::

        with QuiverFile.open("archive.xml", mode="w") as qf:
            qf.add("README.md")
            qf.add("src/main.py", arcname="main.py")
    """

    def __init__(self, name: str, mode: str = "r") -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode {mode!r}. Must be one of: {', '.join(sorted(VALID_MODES))}"
            )
        self._name = name
        self._mode = mode
        # Ordered list of (QuiverInfo, content_str) tuples accumulated during write/append.
        self._entries: list[tuple[QuiverInfo, str]] = []
        self._closed = False
        logger.debug("QuiverFile opened", archive_name=name, mode=mode)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def open(name: str, mode: str = "r", **kwargs: object) -> QuiverFile:  # noqa: ARG004
        """Open a quiver archive and return a :class:`QuiverFile` instance.

        Args:
            name: Path to the archive file.
            mode: ``'r'`` (read), ``'w'`` (write), or ``'a'`` (append).
            **kwargs: Reserved for future use.

        Returns:
            A new :class:`QuiverFile` instance.

        Raises:
            ValueError: If *mode* is not one of ``'r'``, ``'w'``, ``'a'``.
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
        """Add a file to the archive.

        Args:
            name: Path to the file on disk.
            arcname: Override the path stored inside the archive. If omitted,
                the normalized POSIX version of *name* is used.

        Raises:
            FileNotFoundError: If *name* does not exist.
            BinaryFileError: If *name* cannot be decoded as UTF-8 text.
            ValueError: If the archive is not open for writing/appending.
        """
        if self._mode not in {"w", "a"}:
            raise ValueError(f"Cannot add files in mode {self._mode!r}. Use mode 'w' or 'a'.")
        if self._closed:
            raise ValueError("Cannot add files to a closed archive.")

        file_path = Path(name)

        if not file_path.exists():
            raise FileNotFoundError(f"No such file: {name!r}")
        if file_path.is_dir():
            self._add_directory(file_path, arcname)
            return

        content = _read_text_file(file_path)

        stored_path = (
            _normalize_stored_path(arcname) if arcname is not None else _normalize_path(file_path)
        )
        info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
        self._entries.append((info, content))
        logger.debug("Added file", entry_path=stored_path, size=info.size)

    def _add_directory(self, directory_path: Path, arcname: str | None = None) -> None:
        """Recursively add all UTF-8 files under *directory_path* using async workers."""
        asyncio.run(self._add_directory_async(directory_path, arcname))

    async def _add_directory_async(self, directory_path: Path, arcname: str | None = None) -> None:
        """Coordinate bounded-queue async readers and a single writer task."""
        files = _collect_directory_files(directory_path)
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

        writer_task = asyncio.create_task(self._directory_writer(data_queue))
        reader_tasks = [
            asyncio.create_task(
                self._directory_reader_worker(
                    root_dir=directory_path,
                    arcname=arcname,
                    file_queue=file_queue,
                    data_queue=data_queue,
                )
            )
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

    async def _directory_reader_worker(
        self,
        root_dir: Path,
        arcname: str | None,
        file_queue: asyncio.Queue[Path | None],
        data_queue: asyncio.Queue[tuple[QuiverInfo, str] | None],
    ) -> None:
        """Read queued files and send normalized entries to the writer queue."""
        while True:
            file_path = await file_queue.get()
            if file_path is None:
                file_queue.task_done()
                return

            try:
                content = await _read_text_file_async(file_path)
                relative = file_path.relative_to(root_dir)
                stored_path = _directory_stored_path(relative=relative, arcname=arcname)
                info = QuiverInfo(name=stored_path, size=len(content.encode("utf-8")))
                await data_queue.put((info, content))
                logger.debug("Added file", entry_path=stored_path, size=info.size)
            finally:
                file_queue.task_done()

    async def _directory_writer(
        self,
        data_queue: asyncio.Queue[tuple[QuiverInfo, str] | None],
    ) -> None:
        """Consume entries from the queue and append them for final archive writing."""
        while True:
            item = await data_queue.get()
            if item is None:
                return
            self._entries.append(item)

    # ------------------------------------------------------------------
    # Read API (scaffolded — read mode not yet implemented)
    # ------------------------------------------------------------------

    def getnames(self) -> list[str]:
        """Return a list of archive member paths.

        In write mode, returns the paths of all files added so far.
        In read mode, raises :exc:`NotImplementedError`.
        """
        if self._mode == "r":
            raise NotImplementedError("getnames() in read mode is not yet implemented.")
        return [info.name for info, _ in self._entries]

    def getmembers(self) -> list[QuiverInfo]:
        """Return a list of :class:`QuiverInfo` objects for all archive members.

        In write mode, returns metadata for all files added so far.
        In read mode, raises :exc:`NotImplementedError`.
        """
        if self._mode == "r":
            raise NotImplementedError("getmembers() in read mode is not yet implemented.")
        return [info for info, _ in self._entries]

    def extractall(
        self,
        path: str = ".",
        members: list[QuiverInfo] | None = None,
    ) -> None:
        """Extract archive contents to *path*.

        Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("extractall() is not yet implemented.")

    # ------------------------------------------------------------------
    # Close / flush
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the archive, writing the XML file if in write/append mode.

        In write/append mode, sorts all added entries alphabetically by path,
        builds the XML tree, and serializes it to the output file.

        Raises:
            ValueError: If called on an already-closed archive in write mode.
        """
        if self._closed:
            return
        self._closed = True

        if self._mode in {"w", "a"}:
            _write_archive(self._name, self._entries)
            logger.debug("Archive written", archive_name=self._name)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _read_text_file(path: Path) -> str:
    """Read *path* as UTF-8 text.

    Args:
        path: File to read.

    Returns:
        The file content as a string.

    Raises:
        BinaryFileError: If the file cannot be decoded as UTF-8.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(
            f"File {str(path)!r} is not valid UTF-8 text and cannot be packed."
        ) from exc


async def _read_text_file_async(path: Path) -> str:
    """Asynchronously read *path* as UTF-8 text."""
    async with async_open(path, "rb") as afp:
        raw = cast("bytes", await afp.read())
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(
            f"File {str(path)!r} is not valid UTF-8 text and cannot be packed."
        ) from exc


def _collect_directory_files(directory_path: Path) -> list[Path]:
    """Collect all regular files under *directory_path* recursively."""
    return sorted(path for path in directory_path.rglob("*") if path.is_file())


def _directory_stored_path(relative: Path, arcname: str | None) -> str:
    """Build the stored archive path for a file relative to a packed directory."""
    relative_path = _normalize_path(relative)
    if arcname is None:
        return relative_path
    return _normalize_stored_path(str(PurePosixPath(arcname) / relative_path))


def _normalize_stored_path(path_value: str) -> str:
    """Normalize a stored archive path value to clean relative POSIX format."""
    posix = PurePosixPath(path_value)
    parts = [part for part in posix.parts if part not in ("/", "..")]
    return str(PurePosixPath(*parts)) if parts else posix.name


def _normalize_path(path: Path) -> str:
    """Normalize *path* to a clean POSIX string suitable for archive storage.

    - Converts backslashes to forward slashes.
    - Strips any leading ``/`` (makes the path relative).
    - Collapses ``..`` components to prevent traversal issues.

    Args:
        path: The filesystem path to normalize.

    Returns:
        A clean, relative POSIX path string.
    """
    # Resolve to a PurePosixPath representation
    return _normalize_stored_path(path.as_posix())


def _build_xml_tree(entries: list[tuple[QuiverInfo, str]]) -> etree._Element:
    """Build the lxml element tree for the archive.

    Entries are sorted alphabetically by their stored POSIX path.

    Args:
        entries: List of ``(QuiverInfo, content)`` pairs.

    Returns:
        The ``<archive>`` root :class:`lxml.etree._Element`.
    """
    root = etree.Element("archive", version=ARCHIVE_VERSION)

    for info, content in sorted(entries, key=lambda e: e[0].name):
        file_elem = etree.SubElement(root, "file", path=info.name)
        content_elem = etree.SubElement(file_elem, "content")
        content_elem.text = etree.CDATA(content)

    return root


def _write_archive(output_path: str, entries: list[tuple[QuiverInfo, str]]) -> None:
    """Serialize the XML archive tree to *output_path*.

    Args:
        output_path: Destination file path.
        entries: List of ``(QuiverInfo, content)`` pairs to include.
    """
    root = _build_xml_tree(entries)
    xml_bytes = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=False,
        encoding="unicode",
    )
    Path(output_path).write_text(xml_bytes, encoding="utf-8")

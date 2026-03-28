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
import contextlib
import io
import re
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

# Sentinels written at the preamble→XML and XML→epilogue seams.
# Defined as constants so the marker can be changed in one place.
# _PREAMBLE_SENTINEL is appended after the preamble so that <archive> always
# starts on its own line.  _EPILOGUE_SENTINEL is empty because lxml
# pretty_print already ends the XML with \n, which acts as the natural
# separator before the epilogue.
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


def _validate_xml_compatible(content: str, path: Path) -> None:
    """Raise `BinaryFileError` if *content* contains XML-1.0-forbidden characters.

    Scans for NULL bytes and control characters that are legal UTF-8 but rejected
    by lxml's CDATA serializer.  Reports up to `_MAX_REPORTED_OFFENCES` locations
    (line number, column, hex value) so the caller can locate the offending bytes.

    Args:
        content: Decoded text to validate.
        path: Source file path used in the error message.

    Raises:
        BinaryFileError: If any forbidden character is found, with a message that
            includes the file path and the location of each offending character.
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
            f"File {str(path)!r} contains XML-incompatible control characters "
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
    _validate_xml_compatible(content, path)
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
    _validate_xml_compatible(content, path)
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
        is the full `<archive>…</archive>` string ready for ``lxml`` parsing.

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
    # lxml pretty_print always appends \n after </archive>.  That \n lands at
    # the start of the epilogue slice (not inside xml_content) and is a
    # formatting artifact, not user content.  Strip exactly one leading \n so
    # the epilogue round-trips verbatim.  Only strip when epilogue is non-empty
    # to keep the no-epilogue path a no-op.
    if epilogue.startswith("\n"):
        epilogue = epilogue[1:]

    return preamble, xml_content, epilogue


type _ParseResult = tuple[list[tuple[str, str]], str, str]


def _parse_archive(archive_path: str) -> _ParseResult:
    """Parse a quiver XML archive and return its file entries with surrounding text.

    Reads the raw file, splits out any preamble and epilogue via
    [_split_archive_text][], then parses only the first `<archive>` XML block
    using `lxml.etree.fromstring`.  CDATA text from each `<file>` element is
    extracted verbatim.

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

    root = etree.fromstring(xml_content.encode("utf-8"))  # noqa: S320
    entries: list[tuple[str, str]] = []
    for elem in root.iter("file"):
        stored_path = elem.get("path", "")
        content_elem = elem.find("content")
        content = content_elem.text if content_elem is not None and content_elem.text else ""
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
# Layer 3.7 — Async Upsert (Stream & Merge) Pipeline
# ===========================================================================


class _UpsertPipeline:
    """Stream-merge pipeline that upserts new entries into an existing archive.

    Performs two sequential `lxml.iterparse` passes over the original archive's
    XML block to stay OOM-safe regardless of archive size:

    - **Pass 1** collects existing ``path`` attributes (freeing each element
      immediately via ``element.clear()``) and regenerates the
      ``<directory_tree>`` from the merged path set.
    - **Pass 2** streams ``<file>`` elements in document order and
      merge-inserts/upserts/copies them against the sorted list of new entries,
      writing each resulting element to a ``.tmp`` file via `aiofile`.

    On success the ``.tmp`` file atomically replaces the original archive via
    ``os.replace``.  If any error occurs before that point the original file
    is never touched.

    Args:
        archive_path: Path to the existing archive to update.
        new_entries: Sorted list of ``(QuiverInfo, content)`` pairs to upsert.
        preamble: Optional preamble override.  ``None`` keeps the original.
        epilogue: Optional epilogue override.  ``None`` keeps the original.
    """

    def __init__(
        self,
        archive_path: str,
        new_entries: list[tuple[QuiverInfo, str]],
        preamble: str | None = None,
        epilogue: str | None = None,
    ) -> None:
        self._archive_path = archive_path
        self._new_entries = sorted(new_entries, key=lambda e: e[0].name)
        self._preamble = preamble
        self._epilogue = epilogue

    def run(self) -> None:
        """Execute the upsert pipeline synchronously."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Orchestrate the two-pass merge and atomic swap."""
        raw = await asyncio.to_thread(Path(self._archive_path).read_text, encoding="utf-8")
        orig_preamble, xml_content, orig_epilogue = _split_archive_text(raw)

        # Caller-supplied values override what was in the file; otherwise
        # preserve what was already there.
        effective_preamble = self._preamble if self._preamble is not None else orig_preamble
        effective_epilogue = self._epilogue if self._epilogue is not None else orig_epilogue

        xml_bytes = xml_content.encode("utf-8")
        new_entries_by_path = {info.name: content for info, content in self._new_entries}
        new_paths_sorted = [info.name for info, _ in self._new_entries]

        # ------------------------------------------------------------------
        # Pass 1 — collect existing paths for directory tree regeneration
        # ------------------------------------------------------------------
        existing_paths: list[str] = []
        for _event, elem in etree.iterparse(io.BytesIO(xml_bytes), events=("end",), tag="file"):
            path_attr = elem.get("path", "")
            if path_attr:
                existing_paths.append(path_attr)
            elem.clear()

        # Build merged, deduplicated, sorted path list for the directory tree.
        merged_paths = sorted(set(existing_paths) | set(new_paths_sorted))
        tree_text = build_directory_tree(merged_paths)

        # ------------------------------------------------------------------
        # Pass 2 — stream-merge into .tmp file
        # ------------------------------------------------------------------
        tmp_path = self._archive_path + ".tmp"
        try:
            await self._write_merged(
                tmp_path,
                xml_bytes,
                new_entries_by_path,
                new_paths_sorted,
                tree_text,
                effective_preamble,
                effective_epilogue,
            )
            await asyncio.to_thread(Path(tmp_path).replace, self._archive_path)
            logger.debug("Archive upserted", archive_name=self._archive_path)
        except Exception:
            # Leave the original archive untouched; clean up the partial tmp.
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(Path(tmp_path).unlink)
            raise

    async def _write_merged(
        self,
        tmp_path: str,
        xml_bytes: bytes,
        new_entries_by_path: dict[str, str],
        new_paths_sorted: list[str],
        tree_text: str,
        preamble: str | None,
        epilogue: str | None,
    ) -> None:
        """Write the merged archive to *tmp_path*."""
        # Index into new_paths_sorted tracking which new entries are still
        # pending insertion.
        pending_idx = 0
        pending_total = len(new_paths_sorted)

        parts: list[str] = []

        # Preamble + sentinel
        if preamble:
            parts.append(preamble)
            parts.append(_PREAMBLE_SENTINEL)

        # Opening tag + version attribute
        parts.append(f'<archive version="{ARCHIVE_VERSION}">\n')

        # Directory tree element (CDATA-wrapped, matches _build_xml_tree)
        tree_elem = etree.Element("directory_tree")
        tree_elem.text = etree.CDATA("\n" + tree_text + "\n")
        parts.append(
            "  "
            + etree.tostring(tree_elem, encoding="unicode", xml_declaration=False).rstrip("\n")
            + "\n"
        )

        # ------------------------------------------------------------------
        # Stream existing <file> elements, merge-inserting new entries
        # ------------------------------------------------------------------
        for _event, elem in etree.iterparse(io.BytesIO(xml_bytes), events=("end",), tag="file"):
            stored_path = elem.get("path", "")

            # Insert new entries that sort before this existing entry.
            while pending_idx < pending_total and new_paths_sorted[pending_idx] < stored_path:
                new_path = new_paths_sorted[pending_idx]
                parts.append(self._render_file_element(new_path, new_entries_by_path[new_path]))
                pending_idx += 1

            if stored_path in new_entries_by_path:
                # Upsert: replace existing entry with the new content.
                parts.append(
                    self._render_file_element(stored_path, new_entries_by_path[stored_path])
                )
                # Consume this new entry so it is not appended again in the drain.
                new_paths_sorted_set = set(new_paths_sorted[:pending_total])
                if stored_path in new_paths_sorted_set:
                    # Advance pending_idx past this path if it is next in line.
                    while (
                        pending_idx < pending_total and new_paths_sorted[pending_idx] <= stored_path
                    ):
                        pending_idx += 1
            else:
                # Copy: preserve the original element verbatim.
                content_elem = elem.find("content")
                content = (
                    content_elem.text if content_elem is not None and content_elem.text else ""
                )
                parts.append(self._render_file_element(stored_path, content))

            elem.clear()

        # Drain: append any new entries that sort after all existing entries.
        while pending_idx < pending_total:
            new_path = new_paths_sorted[pending_idx]
            parts.append(self._render_file_element(new_path, new_entries_by_path[new_path]))
            pending_idx += 1

        parts.append("</archive>\n")

        if epilogue:
            parts.append(_EPILOGUE_SENTINEL)
            parts.append(epilogue)

        async with async_open(tmp_path, "w", encoding="utf-8") as afp:
            await afp.write("".join(parts))

    @staticmethod
    def _render_file_element(path: str, content: str) -> str:
        """Serialize a single ``<file>`` element with CDATA content to a string."""
        file_elem = etree.Element("file", path=path)
        content_elem = etree.SubElement(file_elem, "content")
        content_elem.text = etree.CDATA(content)
        return (
            "  "
            + etree.tostring(file_elem, encoding="unicode", xml_declaration=False).rstrip("\n")
            + "\n"
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


def _write_archive(
    output_path: str,
    entries: list[tuple[QuiverInfo, str]],
    preamble: str | None = None,
    epilogue: str | None = None,
) -> None:
    """Serialize the XML archive tree to *output_path*.

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
    root = _build_xml_tree(entries)
    xml_str = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=False,
        encoding="unicode",
    )

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
        `'a'`: Open for appending; upserts new entries into the existing archive
            using a streaming merge so that the result remains alphabetically
            sorted without loading the entire archive into RAM.

    Example:
        ```python
        with QuiverFile.open("archive.xml", mode="w") as qf:
            qf.add("README.md")
            qf.add("src/main.py", arcname="main.py")
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
            raw_entries, parsed_preamble, parsed_epilogue = _parse_archive(name)
            self._preamble = parsed_preamble if parsed_preamble.strip() else None
            self._epilogue = parsed_epilogue if parsed_epilogue.strip() else None
            self._entries = [
                (QuiverInfo(name=stored_path, size=len(content.encode("utf-8"))), content)
                for stored_path, content in raw_entries
            ]

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
            mode: `'r'` (read), `'w'` (write), or `'a'` (append).
            preamble: Optional text to prepend before the XML when writing.
                Ignored in read mode (preamble is parsed from the file).
            epilogue: Optional text to append after the XML when writing.
                Ignored in read mode (epilogue is parsed from the file).

        Returns:
            A new [QuiverFile][] instance.

        Raises:
            ValueError: If *mode* is not one of `'r'`, `'w'`, `'a'`.
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
        """Close the archive, writing or merging the XML file if in write/append mode.

        - In `'w'` mode: sorts all added entries alphabetically, builds the XML
          tree, and serializes it to the output file (creates or overwrites).
        - In `'a'` mode: runs the streaming merge pipeline, upsert-inserting
          entries into the existing archive, then atomically replaces it.
        """
        if self._closed:
            return
        self._closed = True

        if self._mode == "w":
            _write_archive(self._name, self._entries, self._preamble, self._epilogue)
            logger.debug("Archive written", archive_name=self._name)
        elif self._mode == "a":
            _UpsertPipeline(
                self._name,
                self._entries,
                preamble=self._preamble,
                epilogue=self._epilogue,
            ).run()
            logger.debug("Archive upserted", archive_name=self._name)

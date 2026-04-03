"""Quiver: Pack and unpack text files into machine-readable XML."""

from __future__ import annotations

from typing import IO

from quiver.archive import BinaryFileError, PathTraversalError, QuiverFile, QuiverInfo

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "open",
    "QuiverFile",
    "QuiverInfo",
    "BinaryFileError",
    "PathTraversalError",
]


def open(
    name: str | IO[bytes],
    mode: str = "r",
    preamble: str | None = None,
    epilogue: str | None = None,
) -> QuiverFile:
    """Open a quiver archive and return a [QuiverFile][] instance.

    This is the top-level factory function, analogous to `zipfile.ZipFile`.

    Args:
        name: Path to the archive file.
        mode: ``'r'`` (read) or ``'w'`` (write).
        preamble: Optional text to prepend before the XML when writing.
            Ignored in read mode (preamble is parsed from the file).
        epilogue: Optional text to append after the XML when writing.
            Ignored in read mode (epilogue is parsed from the file).

    Returns:
        A new [QuiverFile][] instance.

    Example:
        ```python
        import quiver

        with quiver.open("archive.xml", mode="w") as qf:
            qf.write("README.md")
        ```
    """
    return QuiverFile.open(name, mode, preamble=preamble, epilogue=epilogue)

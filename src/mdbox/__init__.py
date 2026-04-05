"""Mdbox: Pack and unpack text files into machine-readable XML."""

from __future__ import annotations

from importlib.metadata import version
from typing import IO

from mdbox.archive import BinaryFileError, MdboxFile, MdboxInfo, PathTraversalError

__version__ = version("mdbox")
__all__ = [
    "__version__",
    "open",
    "MdboxFile",
    "MdboxInfo",
    "BinaryFileError",
    "PathTraversalError",
]


def open(
    name: str | IO[bytes],
    mode: str = "r",
    preamble: str | None = None,
    epilogue: str | None = None,
) -> MdboxFile:
    """Open a mdbox archive and return a [MdboxFile][] instance.

    This is the top-level factory function, analogous to `zipfile.ZipFile`.

    Args:
        name: Path to the archive file.
        mode: ``'r'`` (read) or ``'w'`` (write).
        preamble: Optional text to prepend before the XML when writing.
            Ignored in read mode (preamble is parsed from the file).
        epilogue: Optional text to append after the XML when writing.
            Ignored in read mode (epilogue is parsed from the file).

    Returns:
        A new [MdboxFile][] instance.

    Example:
        ```python
        import mdbox

        with mdbox.open("archive.xml", mode="w") as qf:
            qf.write("README.md")
        ```
    """
    return MdboxFile.open(name, mode, preamble=preamble, epilogue=epilogue)

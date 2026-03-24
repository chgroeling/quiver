"""Quiver: Pack and unpack text files into machine-readable XML."""

from __future__ import annotations

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


def open(name: str, mode: str = "r", **kwargs: object) -> QuiverFile:
    """Open a quiver archive and return a :class:`QuiverFile` instance.

    This is the top-level factory function, analogous to :func:`tarfile.open`.

    Args:
        name: Path to the archive file.
        mode: ``'r'`` (read), ``'w'`` (write), or ``'a'`` (append).
        **kwargs: Passed through to :meth:`QuiverFile.open`.

    Returns:
        A new :class:`QuiverFile` instance.

    Example::

        import quiver

        with quiver.open("archive.xml", mode="w") as qf:
            qf.add("README.md")
    """
    return QuiverFile.open(name, mode, **kwargs)

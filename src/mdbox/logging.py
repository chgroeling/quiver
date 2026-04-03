"""Logging and UI configuration for mdbox.

Keeps :mod:`structlog` (internal debug logging) and :mod:`rich` (user-facing
verbose output) strictly separated, and ensures the CLI remains silent by
default when neither flag is active.
"""

from __future__ import annotations

import logging

import structlog
from rich.console import Console

_QUIET_CONSOLE = Console(quiet=True)


def configure_debug_logging(enabled: bool) -> None:
    """Configure :mod:`structlog` for internal debug output.

    When *enabled* is ``True``, structured log records are rendered to stderr
    at DEBUG level.  When ``False``, all logging is suppressed via a
    :class:`logging.NullHandler`.

    Args:
        enabled: Activate debug logging when ``True``.
    """
    if enabled:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(message)s",
        )
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.dev.ConsoleRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(),
        )
    else:
        logging.getLogger().addHandler(logging.NullHandler())
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
            logger_factory=structlog.PrintLoggerFactory(),
        )


def get_console(verbose: bool) -> Console:
    """Return a :class:`rich.console.Console` appropriate for the verbosity level.

    When *verbose* is ``True``, a normal stderr console is returned so that
    rich output appears in the terminal.  When ``False``, a quiet console is
    returned so all :meth:`~rich.console.Console.print` calls become no-ops.

    Args:
        verbose: Enable rich terminal output when ``True``.

    Returns:
        A :class:`~rich.console.Console` instance.
    """
    if verbose:
        return Console()
    return _QUIET_CONSOLE

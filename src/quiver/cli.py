"""CLI entry point for quiver."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

import click
import structlog

from quiver.archive import BinaryFileError, PathTraversalError, QuiverFile, _normalize_stored_path
from quiver.logging import configure_debug_logging, get_console

logger = structlog.get_logger(__name__)

_BUNDLABLE_FLAGS = {"a", "c", "x", "v", "f"}


def _expand_bundled_flags(args: list[str]) -> list[str]:
    expanded: list[str] = []
    index = 0
    length = len(args)

    while index < length:
        token = args[index]

        if token == "--":
            expanded.append(token)
            expanded.extend(args[index + 1 :])
            break

        if not token.startswith("-") or token == "-" or token.startswith("--"):
            expanded.append(token)
            index += 1
            continue

        bundle = token[1:]
        if not bundle:
            expanded.append(token)
            index += 1
            continue

        if any(flag not in _BUNDLABLE_FLAGS for flag in bundle):
            raise click.UsageError(f"Unknown option '-{bundle}'.")

        if "f" in bundle:
            if bundle[-1] != "f":
                raise click.UsageError("Option '-f' must be the last flag in a bundle.")
            for flag in bundle[:-1]:
                expanded.append(f"-{flag}")
            expanded.append("-f")
            index += 1
            if index >= length:
                raise click.UsageError("Option '-f' requires an argument.")
            expanded.append(args[index])
        else:
            for flag in bundle:
                expanded.append(f"-{flag}")

        index += 1

    return expanded


class TarStyleCommand(click.Command):
    """Command that expands tar-style bundled flags before parsing."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        expanded = _expand_bundled_flags(args)
        return super().parse_args(ctx, expanded)


@click.command(cls=TarStyleCommand)
@click.version_option()
@click.option("-c", "--create", is_flag=True, help="Create a new archive from input paths.")
@click.option("-x", "--extract", is_flag=True, help="Extract files from an archive.")
@click.option("-a", "--add", is_flag=True, help="Add/upsert files into an existing archive.")
@click.option(
    "--delete", is_flag=True, help="Delete files or directories from an existing archive."
)
@click.option(
    "-f",
    "--file",
    "archive_file",
    type=click.Path(dir_okay=False, path_type=str),
    help="Archive path to write to (with -c) or read from (with -x).",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable rich UI output.")
@click.option("--debug", is_flag=True, help="Enable structured debug logging.")
@click.option(
    "--preamble",
    default=None,
    help="Text or file path to prepend before the archive XML (create mode only).",
)
@click.option(
    "--epilogue",
    default=None,
    help="Text or file path to append after the archive XML (create mode only).",
)
@click.argument("inputs", nargs=-1, type=click.Path(path_type=str))
def main(
    create: bool,
    extract: bool,
    add: bool,
    delete: bool,
    archive_file: str | None,
    verbose: bool,
    debug: bool,
    preamble: str | None,
    epilogue: str | None,
    inputs: tuple[str, ...],
) -> None:
    """Pack and unpack text files into machine-readable XML."""

    _validate_mode_flags(create, extract, add, delete)
    configure_debug_logging(debug)

    if create:
        _run_create(archive_file, verbose, inputs, preamble=preamble, epilogue=epilogue)
    elif extract:
        _run_extract(archive_file, verbose, inputs)
    elif add:
        _run_add(archive_file, verbose, inputs)
    elif delete:
        _run_delete(archive_file, verbose, inputs)


def _validate_mode_flags(create: bool, extract: bool, add: bool, delete: bool) -> None:
    active = sum([create, extract, add, delete])
    if active > 1:
        raise click.UsageError(
            "Cannot specify more than one of -c/--create, -x/--extract, -a/--add, --delete."
        )
    if active == 0:
        raise click.UsageError(
            "Specify -c/--create, -x/--extract, -a/--add, or --delete to select an operation."
        )


def _run_create(
    archive_file: str | None,
    verbose: bool,
    inputs: tuple[str, ...],
    preamble: str | None = None,
    epilogue: str | None = None,
) -> None:
    if not archive_file:
        raise click.UsageError("Option '-f/--file' is required when creating an archive.")
    if not inputs:
        raise click.UsageError("Provide at least one input file or directory to archive.")

    resolved_preamble = _resolve_text_or_file(preamble)
    resolved_epilogue = _resolve_text_or_file(epilogue)

    console = get_console(verbose)
    if verbose:
        sources = ", ".join(str(Path(path)) for path in inputs)
        console.print(f"Packing [bold]{sources}[/bold] → [bold]{archive_file}[/bold]...")

    try:
        with QuiverFile.open(
            archive_file, mode="w", preamble=resolved_preamble, epilogue=resolved_epilogue
        ) as qf:
            for input_path in inputs:
                qf.write(input_path)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except BinaryFileError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error writing archive: {exc}", err=True)
        sys.exit(1)

    if verbose:
        console.print("[green]Done.[/green]")


def _resolve_text_or_file(value: str | None) -> str | None:
    """Return the content of *value* as a string.

    If *value* is a path to an existing file, return the file's UTF-8 text.
    Otherwise return *value* unchanged (treating it as a raw string).

    Args:
        value: Raw CLI argument, a file path, or ``None``.

    Returns:
        Resolved text, or ``None`` if *value* is ``None``.
    """
    if value is None:
        return None
    candidate = Path(value)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return value


def _run_add(
    archive_file: str | None,
    verbose: bool,
    inputs: tuple[str, ...],
) -> None:
    if not archive_file:
        raise click.UsageError("Option '-f/--file' is required when adding to an archive.")
    if not inputs:
        raise click.UsageError("Provide at least one input file or directory to add.")

    archive_path = Path(archive_file)
    console = get_console(verbose)
    if verbose:
        sources = ", ".join(str(Path(path)) for path in inputs)
        console.print(f"Upserting [bold]{sources}[/bold] → [bold]{archive_file}[/bold]...")

    try:
        t0 = time.perf_counter()
        if archive_path.exists():
            # Repack: read existing archive, merge new inputs, write to temp, atomic rename.
            with QuiverFile.open(archive_file, mode="r") as src:
                existing_entries = src.entries
                preamble = src.preamble
                epilogue = src.epilogue

            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=archive_path.parent, prefix=".quiver-", suffix=".tmp"
            )
            try:
                os.close(tmp_fd)
                with QuiverFile.open(
                    tmp_name, mode="w", preamble=preamble, epilogue=epilogue
                ) as dst:
                    for info, content in existing_entries:
                        dst.add_text(info.name, content)
                    for input_path in inputs:
                        dst.write(input_path)
                Path(tmp_name).replace(archive_file)
            except Exception:
                with contextlib.suppress(OSError):
                    Path(tmp_name).unlink()
                raise
            entry_count = len(existing_entries)
        else:
            # Archive does not exist — create it from scratch.
            with QuiverFile.open(archive_file, mode="w") as dst:
                for input_path in inputs:
                    dst.write(input_path)
            entry_count = 0

        logger.debug(
            "Add repack completed",
            elapsed_s=round(time.perf_counter() - t0, 4),
            entry_count=entry_count,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except BinaryFileError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error updating archive: {exc}", err=True)
        sys.exit(1)

    if verbose:
        console.print("[green]Done.[/green]")


def _run_delete(
    archive_file: str | None,
    verbose: bool,
    inputs: tuple[str, ...],
) -> None:
    if not archive_file:
        raise click.UsageError("Option '-f/--file' is required when deleting from an archive.")
    if not inputs:
        raise click.UsageError("Provide at least one path to delete from the archive.")

    archive_path = Path(archive_file)
    if not archive_path.exists():
        click.echo(f"Error: Archive not found: {archive_file!r}", err=True)
        sys.exit(1)

    console = get_console(verbose)
    if verbose:
        targets = ", ".join(inputs)
        console.print(f"Deleting [bold]{targets}[/bold] from [bold]{archive_file}[/bold]...")

    # Normalise all target paths once so matching is consistent.
    normalized_targets = {_normalize_stored_path(t) for t in inputs}
    dir_prefixes = {t.rstrip("/") + "/" for t in normalized_targets}

    def _keep(name: str) -> bool:
        return name not in normalized_targets and not any(
            name.startswith(pfx) for pfx in dir_prefixes
        )

    try:
        with QuiverFile.open(archive_file, mode="r") as src:
            all_entries = src.entries
            filtered = [(info, content) for info, content in all_entries if _keep(info.name)]
            preamble = src.preamble
            epilogue = src.epilogue

        t0 = time.perf_counter()
        # Write to a sibling temp file then atomically replace the original.
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=archive_path.parent, prefix=".quiver-", suffix=".tmp"
        )
        try:
            os.close(tmp_fd)
            with QuiverFile.open(tmp_name, mode="w", preamble=preamble, epilogue=epilogue) as dst:
                for info, content in filtered:
                    dst.add_text(info.name, content)
            Path(tmp_name).replace(archive_file)
        except Exception:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink()
            raise
        kept_count = len(filtered)
        logger.debug(
            "Delete repack completed",
            elapsed_s=round(time.perf_counter() - t0, 4),
            deleted_count=len(all_entries) - kept_count,
            kept_count=kept_count,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error updating archive: {exc}", err=True)
        sys.exit(1)

    if verbose:
        console.print("[green]Done.[/green]")


def _run_extract(archive_file: str | None, verbose: bool, inputs: tuple[str, ...]) -> None:
    if not archive_file:
        raise click.UsageError("Option '-f/--file' is required when extracting an archive.")

    destination = inputs[0] if inputs else "."
    console = get_console(verbose)
    if verbose:
        console.print(f"Extracting [bold]{archive_file}[/bold] → [bold]{destination}[/bold]...")

    try:
        with QuiverFile.open(archive_file, mode="r") as qf:
            qf.extractall(path=destination)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except PathTraversalError as exc:
        click.echo(f"Security error: {exc}", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error extracting archive: {exc}", err=True)
        sys.exit(1)

    if verbose:
        console.print("[green]Done.[/green]")


if __name__ == "__main__":
    main()

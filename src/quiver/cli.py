"""CLI entry point for quiver."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from quiver.archive import BinaryFileError, QuiverFile
from quiver.logging import configure_debug_logging, get_console

_BUNDLABLE_FLAGS = {"c", "x", "v", "f"}


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
@click.option(
    "-x", "--extract", is_flag=True, help="Extract files from an archive (not yet implemented)."
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
@click.argument("inputs", nargs=-1, type=click.Path(path_type=str))
def main(
    create: bool,
    extract: bool,
    archive_file: str | None,
    verbose: bool,
    debug: bool,
    inputs: tuple[str, ...],
) -> None:
    """Pack and unpack text files into machine-readable XML."""

    _validate_mode_flags(create, extract)
    configure_debug_logging(debug)

    if create:
        _run_create(archive_file, verbose, inputs)
    elif extract:
        click.echo("Extract command not yet implemented")
        sys.exit(1)


def _validate_mode_flags(create: bool, extract: bool) -> None:
    if create and extract:
        raise click.UsageError("Cannot specify both -c/--create and -x/--extract.")
    if not create and not extract:
        raise click.UsageError("Specify -c/--create or -x/--extract to select an operation.")


def _run_create(archive_file: str | None, verbose: bool, inputs: tuple[str, ...]) -> None:
    if not archive_file:
        raise click.UsageError("Option '-f/--file' is required when creating an archive.")
    if not inputs:
        raise click.UsageError("Provide at least one input file or directory to archive.")

    console = get_console(verbose)
    if verbose:
        sources = ", ".join(str(Path(path)) for path in inputs)
        console.print(f"Packing [bold]{sources}[/bold] → [bold]{archive_file}[/bold]...")

    try:
        with QuiverFile.open(archive_file, mode="w") as qf:
            for input_path in inputs:
                qf.add(input_path)
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


if __name__ == "__main__":
    main()

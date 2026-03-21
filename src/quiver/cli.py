"""CLI entry point for quiver."""

from __future__ import annotations

import sys

import click

from quiver.archive import BinaryFileError, QuiverFile
from quiver.logging import configure_debug_logging, get_console


@click.group()
@click.version_option()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable rich UI output.")
@click.option("--debug", is_flag=True, default=False, help="Enable structured debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool, debug: bool) -> None:
    """Pack and unpack text files into machine-readable XML.

    Quiver is a high-performance, cross-platform tool for packing and
    unpacking text files into a strictly formatted XML format.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    configure_debug_logging(debug)


@main.command()
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "-f",
    "--file",
    "output_file",
    required=True,
    type=click.Path(),
    help="Output archive path (XML).",
)
@click.pass_context
def pack(ctx: click.Context, input_file: str, output_file: str) -> None:
    """Pack INPUT_FILE into an XML archive."""
    verbose: bool = ctx.obj.get("verbose", False)
    console = get_console(verbose)

    console.print(f"Packing [bold]{input_file}[/bold] \u2192 [bold]{output_file}[/bold]...")

    try:
        with QuiverFile.open(output_file, mode="w") as qf:
            qf.add(input_file)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except BinaryFileError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except OSError as exc:
        click.echo(f"Error writing archive: {exc}", err=True)
        sys.exit(1)

    console.print("[green]Done.[/green]")


@main.command()
def unpack() -> None:
    """Unpack XML format into text files."""
    click.echo("Unpack command not yet implemented")


if __name__ == "__main__":
    main()

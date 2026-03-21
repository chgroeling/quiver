"""CLI entry point for quiver."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Pack and unpack text files into machine-readable XML.

    Quiver is a high-performance, cross-platform tool for packing and
    unpacking text files into a strictly formatted XML format.
    """


@main.command()
def pack() -> None:
    """Pack text files into XML format."""
    click.echo("Pack command not yet implemented")


@main.command()
def unpack() -> None:
    """Unpack XML format into text files."""
    click.echo("Unpack command not yet implemented")


if __name__ == "__main__":
    main()

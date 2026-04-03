"""Tests for CLI entry point."""

from click.testing import CliRunner

from mdbox.cli import main


def test_cli_version() -> None:
    """Test that CLI shows version."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower()


def test_cli_help() -> None:
    """Test that CLI shows help."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "-c" in result.output
    assert "-x" in result.output
    assert "-f" in result.output


def test_cli_requires_operation_flag() -> None:
    """Ensure CLI errors when no operation flag is provided."""
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code != 0
    assert "-c/--create" in result.output

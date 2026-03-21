"""Tests for CLI entry point."""

from click.testing import CliRunner

from quiver.cli import main


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
    assert "pack" in result.output.lower()
    assert "unpack" in result.output.lower()


def test_pack_command() -> None:
    """Test pack command stub."""
    runner = CliRunner()
    result = runner.invoke(main, ["pack"])
    assert result.exit_code == 0
    assert "not yet implemented" in result.output.lower()


def test_unpack_command() -> None:
    """Test unpack command stub."""
    runner = CliRunner()
    result = runner.invoke(main, ["unpack"])
    assert result.exit_code == 0
    assert "not yet implemented" in result.output.lower()

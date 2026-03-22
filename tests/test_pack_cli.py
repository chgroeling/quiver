"""Integration tests for the `quiver pack` CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from lxml import etree

from quiver.cli import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


def test_pack_produces_valid_xml(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "hello.txt"
    input_file.write_text("Hello, world!", encoding="utf-8")
    output_file = tmp_path / "archive.xml"

    result = runner.invoke(main, ["pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code == 0, result.output

    raw_xml = output_file.read_text(encoding="utf-8")
    root = etree.fromstring(raw_xml.encode())
    assert root.tag == "archive"
    assert root.get("version") == "1.0"
    file_elem = root.find("file")
    assert file_elem is not None
    assert file_elem.get("path", "").endswith("hello.txt")
    content_elem = file_elem.find("content")
    assert content_elem is not None
    assert content_elem.text == "Hello, world!"


def test_pack_xml_uses_cdata(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "special.txt"
    input_file.write_text("x < y && z > w", encoding="utf-8")
    output_file = tmp_path / "archive.xml"

    result = runner.invoke(main, ["pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code == 0

    raw_xml = output_file.read_text(encoding="utf-8")
    assert "<![CDATA[" in raw_xml
    assert "&lt;" not in raw_xml
    assert "&amp;" not in raw_xml


def test_pack_directory_recursively(tmp_path: Path, runner: CliRunner) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "README.md").write_text("# demo", encoding="utf-8")
    (src / "main.py").write_text("print('ok')", encoding="utf-8")

    output = tmp_path / "archive.xml"
    result = runner.invoke(main, ["pack", str(project), "-f", str(output)])
    assert result.exit_code == 0, result.output

    root = etree.fromstring(output.read_text(encoding="utf-8").encode())
    paths = [el.get("path") for el in root.findall("file")]
    assert paths == ["README.md", "src/main.py"]


# ---------------------------------------------------------------------------
# Silent-by-default contract
# ---------------------------------------------------------------------------


def test_pack_silent_by_default(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code == 0
    assert result.output == ""


def test_pack_verbose_produces_output(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["--verbose", "pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code == 0
    assert len(result.output) > 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_pack_missing_input_file_error(tmp_path: Path, runner: CliRunner) -> None:
    """click should reject a non-existent input file before calling pack()."""
    output_file = tmp_path / "out.xml"
    result = runner.invoke(
        main, ["pack", str(tmp_path / "no_such_file.txt"), "-f", str(output_file)]
    )
    assert result.exit_code != 0


def test_pack_binary_file_error(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "binary.bin"
    output_file = tmp_path / "out.xml"
    input_file.write_bytes(b"\xff\xfe\x00\x01")

    result = runner.invoke(main, ["pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code != 0


def test_pack_directory_with_binary_file_errors(tmp_path: Path, runner: CliRunner) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "text.txt").write_text("ok", encoding="utf-8")
    (project / "binary.bin").write_bytes(b"\xff\xfe\x00")

    output = tmp_path / "out.xml"
    result = runner.invoke(main, ["pack", str(project), "-f", str(output)])
    assert result.exit_code != 0


def test_pack_requires_output_flag(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("data", encoding="utf-8")
    result = runner.invoke(main, ["pack", str(input_file)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Long-form option aliases
# ---------------------------------------------------------------------------


def test_pack_long_form_file_option(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "archive.xml"
    input_file.write_text("content", encoding="utf-8")

    result = runner.invoke(main, ["pack", str(input_file), "--file", str(output_file)])
    assert result.exit_code == 0
    assert output_file.exists()


def test_pack_help_shows_options(runner: CliRunner) -> None:
    result = runner.invoke(main, ["pack", "--help"])
    assert result.exit_code == 0
    assert "-f" in result.output or "--file" in result.output


# ---------------------------------------------------------------------------
# Regression: --debug flag must not crash due to reserved LogRecord keys
# ---------------------------------------------------------------------------


def test_pack_debug_flag_does_not_crash(tmp_path: Path, runner: CliRunner) -> None:
    """Regression: structlog debug calls must not crash and must emit bound fields."""
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["--debug", "pack", str(input_file), "-f", str(output_file)])
    assert result.exit_code == 0, result.output
    assert output_file.exists()
    assert "archive_name" in result.output
    assert "entry_path" in result.output


def test_pack_verbose_and_debug_flags(tmp_path: Path, runner: CliRunner) -> None:
    """Regression: combining --verbose and --debug must succeed and show all output."""
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(
        main,
        ["--verbose", "--debug", "pack", str(input_file), "-f", str(output_file)],
    )
    assert result.exit_code == 0, result.output
    assert len(result.output) > 0
    assert "archive_name" in result.output
    assert "entry_path" in result.output

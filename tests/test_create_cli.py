"""Integration tests for the tar-style quiver CLI command."""

from __future__ import annotations

from pathlib import Path

import lxml.etree as etree
import pytest
from click.testing import CliRunner

from quiver.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


def test_create_produces_valid_xml(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "hello.txt"
    input_file.write_text("Hello, world!", encoding="utf-8")
    output_file = tmp_path / "archive.xml"

    result = runner.invoke(main, ["-c", "-f", str(output_file), str(input_file)])
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


def test_create_xml_uses_cdata(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "special.txt"
    input_file.write_text("x < y && z > w", encoding="utf-8")
    output_file = tmp_path / "archive.xml"

    result = runner.invoke(main, ["-c", "-f", str(output_file), str(input_file)])
    assert result.exit_code == 0

    raw_xml = output_file.read_text(encoding="utf-8")
    assert "<![CDATA[" in raw_xml
    assert "&lt;" not in raw_xml
    assert "&amp;" not in raw_xml


def test_create_directory_recursively(tmp_path: Path, runner: CliRunner) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "README.md").write_text("# demo", encoding="utf-8")
    (src / "main.py").write_text("print('ok')", encoding="utf-8")

    output = tmp_path / "archive.xml"
    result = runner.invoke(main, ["-c", "-f", str(output), str(project)])
    assert result.exit_code == 0, result.output

    root = etree.fromstring(output.read_text(encoding="utf-8").encode())
    paths = [el.get("path") for el in root.findall("file")]
    assert paths == ["README.md", "src/main.py"]


# ---------------------------------------------------------------------------
# Silent-by-default contract
# ---------------------------------------------------------------------------


def test_create_accepts_multiple_inputs(tmp_path: Path, runner: CliRunner) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('ok')", encoding="utf-8")
    extra = tmp_path / "README.md"
    extra.write_text("# extra", encoding="utf-8")
    output = tmp_path / "archive.xml"

    result = runner.invoke(main, ["-c", "-f", str(output), str(src), str(extra)])
    assert result.exit_code == 0, result.output

    root = etree.fromstring(output.read_text(encoding="utf-8").encode())
    names = sorted(Path(el.get("path", "")).name for el in root.findall("file"))
    assert names == ["README.md", "main.py"]


def test_create_silent_by_default(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["-c", "-f", str(output_file), str(input_file)])
    assert result.exit_code == 0
    assert result.output == ""


def test_create_verbose_produces_output(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["-v", "-c", "-f", str(output_file), str(input_file)])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_create_flag_bundling(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["-cvf", str(output_file), str(input_file)])
    assert result.exit_code == 0
    assert len(result.output) > 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_create_missing_input_file_error(tmp_path: Path, runner: CliRunner) -> None:
    """CLI surfaces a helpful error when an input path is missing."""
    output_file = tmp_path / "out.xml"
    result = runner.invoke(main, ["-c", "-f", str(output_file), str(tmp_path / "no_such_file.txt")])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


def test_create_binary_file_error(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "binary.bin"
    output_file = tmp_path / "out.xml"
    input_file.write_bytes(b"\xff\xfe\x00\x01")

    result = runner.invoke(main, ["-c", "-f", str(output_file), str(input_file)])
    assert result.exit_code != 0


def test_create_control_char_error_message(tmp_path: Path, runner: CliRunner) -> None:
    """CLI error for XML-incompatible control chars must include path and location."""
    bad = tmp_path / "ctrl.txt"
    # Place \x07 at line 2, col 4.
    bad.write_bytes(b"first\nsec\x07ond")
    output_file = tmp_path / "out.xml"

    result = runner.invoke(main, ["-c", "-f", str(output_file), str(bad)])

    assert result.exit_code != 0
    assert "ctrl.txt" in result.output
    assert "line 2" in result.output
    assert "col 4" in result.output
    assert r"\x07" in result.output


def test_create_directory_with_binary_file_errors(tmp_path: Path, runner: CliRunner) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "text.txt").write_text("ok", encoding="utf-8")
    (project / "binary.bin").write_bytes(b"\xff\xfe\x00")

    output = tmp_path / "out.xml"
    result = runner.invoke(main, ["-c", "-f", str(output), str(project)])
    assert result.exit_code != 0


def test_create_requires_output_flag(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("data", encoding="utf-8")
    result = runner.invoke(main, ["-c", str(input_file)])
    assert result.exit_code != 0
    assert "-f/--file" in result.output


def test_create_requires_input_paths(tmp_path: Path, runner: CliRunner) -> None:
    output_file = tmp_path / "out.xml"
    result = runner.invoke(main, ["-c", "-f", str(output_file)])
    assert result.exit_code != 0
    assert "input" in result.output.lower()


def test_create_rejects_conflicting_modes(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(
        main,
        ["-c", "-x", "-f", str(output_file), str(input_file)],
    )
    assert result.exit_code != 0
    assert "cannot" in result.output.lower()


# ---------------------------------------------------------------------------
# Long-form option aliases
# ---------------------------------------------------------------------------


def test_create_long_form_file_option(tmp_path: Path, runner: CliRunner) -> None:
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "archive.xml"
    input_file.write_text("content", encoding="utf-8")

    result = runner.invoke(main, ["-c", "--file", str(output_file), str(input_file)])
    assert result.exit_code == 0
    assert output_file.exists()


def test_create_help_shows_options(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "-f" in result.output or "--file" in result.output


# ---------------------------------------------------------------------------
# Regression: --debug flag must not crash due to reserved LogRecord keys
# ---------------------------------------------------------------------------


def test_create_debug_flag_does_not_crash(tmp_path: Path, runner: CliRunner) -> None:
    """Regression: structlog debug calls must not crash and must emit bound fields."""
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(main, ["--debug", "-c", "-f", str(output_file), str(input_file)])
    assert result.exit_code == 0, result.output
    assert output_file.exists()
    assert "archive_name" in result.output
    assert "entry_path" in result.output


def test_create_verbose_and_debug_flags(tmp_path: Path, runner: CliRunner) -> None:
    """Regression: combining --verbose and --debug must succeed and show all output."""
    input_file = tmp_path / "input.txt"
    output_file = tmp_path / "out.xml"
    input_file.write_text("data", encoding="utf-8")

    result = runner.invoke(
        main,
        ["--verbose", "--debug", "-c", "-f", str(output_file), str(input_file)],
    )
    assert result.exit_code == 0, result.output
    assert len(result.output) > 0
    assert "archive_name" in result.output
    assert "entry_path" in result.output

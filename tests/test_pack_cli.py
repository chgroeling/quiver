"""Integration tests for the `quiver pack` CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from lxml import etree

from quiver.cli import main

if TYPE_CHECKING:
    from pyfakefs.fake_filesystem import FakeFilesystem


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


def test_pack_produces_valid_xml(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("hello.txt", contents="Hello, world!")
    result = runner.invoke(main, ["pack", "hello.txt", "-f", "archive.xml"])
    assert result.exit_code == 0, result.output

    raw_xml = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    root = etree.fromstring(raw_xml.encode())
    assert root.tag == "archive"
    assert root.get("version") == "1.0"
    file_elem = root.find("file")
    assert file_elem is not None
    assert file_elem.get("path") == "hello.txt"
    content_elem = file_elem.find("content")
    assert content_elem is not None
    assert content_elem.text == "Hello, world!"


def test_pack_xml_uses_cdata(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("special.txt", contents="x < y && z > w")
    result = runner.invoke(main, ["pack", "special.txt", "-f", "archive.xml"])
    assert result.exit_code == 0

    raw_xml = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    assert "<![CDATA[" in raw_xml
    assert "&lt;" not in raw_xml
    assert "&amp;" not in raw_xml


# ---------------------------------------------------------------------------
# Silent-by-default contract
# ---------------------------------------------------------------------------


def test_pack_silent_by_default(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("input.txt", contents="data")
    result = runner.invoke(main, ["pack", "input.txt", "-f", "out.xml"])
    assert result.exit_code == 0
    # No stdout; stderr is managed by rich (not captured by CliRunner by default)
    assert result.output == ""


def test_pack_verbose_produces_output(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("input.txt", contents="data")
    result = runner.invoke(main, ["--verbose", "pack", "input.txt", "-f", "out.xml"])
    assert result.exit_code == 0
    # Some output must be present when --verbose is used
    assert len(result.output) > 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_pack_missing_input_file_error(runner: CliRunner) -> None:
    """click should reject a non-existent input file before calling pack()."""
    result = runner.invoke(main, ["pack", "no_such_file.txt", "-f", "out.xml"])
    assert result.exit_code != 0


def test_pack_binary_file_error(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("binary.bin", contents=b"\xff\xfe\x00\x01", apply_umask=True)
    result = runner.invoke(main, ["pack", "binary.bin", "-f", "out.xml"])
    assert result.exit_code != 0


def test_pack_requires_output_flag(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("input.txt", contents="data")
    result = runner.invoke(main, ["pack", "input.txt"])
    # Missing required -f option should produce a usage error
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Long-form option aliases
# ---------------------------------------------------------------------------


def test_pack_long_form_file_option(fake_fs: FakeFilesystem, runner: CliRunner) -> None:
    fake_fs.create_file("input.txt", contents="content")
    result = runner.invoke(main, ["pack", "input.txt", "--file", "archive.xml"])
    assert result.exit_code == 0
    assert fake_fs.get_object("archive.xml") is not None


def test_pack_help_shows_options(runner: CliRunner) -> None:
    result = runner.invoke(main, ["pack", "--help"])
    assert result.exit_code == 0
    assert "-f" in result.output or "--file" in result.output

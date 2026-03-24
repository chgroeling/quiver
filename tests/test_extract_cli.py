"""Integration tests for the quiver CLI extract (-x) command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from quiver.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _make_archive(tmp_path: Path, files: dict[str, str]) -> Path:
    """Helper: write *files* into a temp source tree and pack into an archive."""
    src = tmp_path / "src"
    for rel, content in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    args = ["-cf", str(archive)] + [str(src)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    return archive


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


def test_extract_produces_files(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"hello.txt": "Hello, world!"})
    dest = tmp_path / "out"
    dest.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    extracted = list(dest.rglob("*.txt"))
    assert any(f.name == "hello.txt" for f in extracted)
    content = next(f for f in extracted if f.name == "hello.txt").read_text(encoding="utf-8")
    assert content == "Hello, world!"


def test_extract_recreates_nested_directory_structure(tmp_path: Path) -> None:
    archive = _make_archive(
        tmp_path,
        {
            "README.md": "# readme",
            "src/main.py": "print('hi')",
            "src/utils/helper.py": "pass",
        },
    )
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    assert (dest / "README.md").read_text(encoding="utf-8") == "# readme"
    assert (dest / "src" / "main.py").read_text(encoding="utf-8") == "print('hi')"
    assert (dest / "src" / "utils" / "helper.py").read_text(encoding="utf-8") == "pass"


def test_extract_to_custom_destination(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"data.txt": "payload"})
    dest = tmp_path / "custom_dest"

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    extracted = list(dest.rglob("data.txt"))
    assert len(extracted) == 1
    assert extracted[0].read_text(encoding="utf-8") == "payload"


def test_extract_creates_destination_if_missing(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"file.txt": "content"})
    dest = tmp_path / "new_dir" / "nested"
    assert not dest.exists()

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.exists()


# ---------------------------------------------------------------------------
# Silent-by-default contract
# ---------------------------------------------------------------------------


def test_extract_silent_by_default(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"x.txt": "x"})
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0
    assert result.output == ""


def test_extract_verbose_produces_output(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"x.txt": "x"})
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["-xvf", str(archive), str(dest)])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_extract_flag_bundling(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"x.txt": "x"})
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_extract_missing_archive_errors(tmp_path: Path) -> None:
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(tmp_path / "no_such.xml"), str(dest)])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


def test_extract_requires_archive_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["-x"])
    assert result.exit_code != 0
    assert "-f/--file" in result.output


def test_extract_path_traversal_errors(tmp_path: Path) -> None:
    """A hand-crafted archive with a ../traversal path must be rejected."""
    malicious_xml = """\
<archive version="1.0">
  <directory_tree><![CDATA[
.
└── evil.txt
  ]]></directory_tree>
  <file path="../evil.txt">
    <content><![CDATA[pwned]]></content>
  </file>
</archive>
"""
    archive = tmp_path / "malicious.xml"
    archive.write_text(malicious_xml, encoding="utf-8")
    dest = tmp_path / "out"
    dest.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 1
    assert "security" in result.output.lower()
    # The traversal target must NOT have been created.
    assert not (tmp_path / "evil.txt").exists()


def test_extract_absolute_path_in_archive_errors(tmp_path: Path) -> None:
    """An archive entry with an absolute path must be rejected."""
    malicious_xml = """\
<archive version="1.0">
  <directory_tree><![CDATA[
.
  ]]></directory_tree>
  <file path="/tmp/injected.txt">
    <content><![CDATA[pwned]]></content>
  </file>
</archive>
"""
    archive = tmp_path / "malicious.xml"
    archive.write_text(malicious_xml, encoding="utf-8")
    dest = tmp_path / "out"
    dest.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 1
    assert "security" in result.output.lower()


# ---------------------------------------------------------------------------
# Long-form option aliases
# ---------------------------------------------------------------------------


def test_extract_long_form_option(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, {"f.txt": "content"})
    dest = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(main, ["--extract", "--file", str(archive), str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.exists()

"""Integration and unit tests for the preamble/epilogue (embedding) feature."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from quiver.archive import QuiverFile
from quiver.cli import main

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Preamble / epilogue round-trip tests (public API)
# ---------------------------------------------------------------------------


def test_preamble_absent_when_none_given(tmp_path: Path) -> None:
    """An archive written without a preamble has no preamble on read-back."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("a.txt", "hi")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.preamble is None


def test_epilogue_absent_when_none_given(tmp_path: Path) -> None:
    """An archive written without an epilogue has no epilogue on read-back."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("a.txt", "hi")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.epilogue is None


def test_preamble_and_epilogue_round_trip(tmp_path: Path) -> None:
    """Preamble and epilogue supplied at write time survive a full round-trip."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(
        str(archive), mode="w", preamble="Before text", epilogue="After text"
    ) as qf:
        qf.add_text("a.txt", "hi")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.preamble == "Before text"
        assert qf.epilogue == "After text"


def test_first_archive_block_wins_multiple_blocks(tmp_path: Path) -> None:
    """When raw file content contains two <archive> blocks the second is treated as epilogue."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("a.txt", "A")
    # Append a second archive block as epilogue by rewriting the file directly.
    raw = archive.read_text(encoding="utf-8")
    second_block = '<archive version="1.0"><file path="b.txt"><content><![CDATA[B]]></content></file></archive>\n'
    archive.write_text(raw + second_block, encoding="utf-8")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.getnames() == ["a.txt"]
        assert qf.epilogue is not None
        assert "b.txt" in qf.epilogue


def test_missing_archive_tag_raises(tmp_path: Path) -> None:
    """Opening a file with no <archive> element raises ValueError."""
    bad = tmp_path / "bad.xml"
    bad.write_text("just plain text with no archive element", encoding="utf-8")
    with pytest.raises(ValueError, match="No <archive>"):
        QuiverFile.open(str(bad), mode="r")


def test_missing_close_tag_raises(tmp_path: Path) -> None:
    """Opening a file whose <archive> tag is never closed raises ValueError."""
    bad = tmp_path / "bad.xml"
    bad.write_text('<archive version="1.0"><file path="x.txt"/>', encoding="utf-8")
    with pytest.raises(ValueError, match="No </archive>"):
        QuiverFile.open(str(bad), mode="r")


def test_preamble_sentinel_stripped_on_round_trip(tmp_path: Path) -> None:
    """The newline sentinel between preamble and <archive> is not part of the preamble value."""
    archive = tmp_path / "archive.xml"
    preamble_text = "My preamble"
    with QuiverFile.open(str(archive), mode="w", preamble=preamble_text) as qf:
        qf.add_text("a.txt", "content")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.preamble == preamble_text


def test_no_sentinel_no_strip(tmp_path: Path) -> None:
    """Hand-crafted archives without preamble/epilogue read back with None for both."""
    archive = tmp_path / "archive.xml"
    archive.write_text(
        '<archive version="1.0">'
        '<file path="a.txt"><content><![CDATA[hi]]></content></file>'
        "</archive>",
        encoding="utf-8",
    )
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.preamble is None
        assert qf.epilogue is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a source directory with the given files."""
    src = tmp_path / "src"
    for rel, content in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return src


def _create_archive(
    tmp_path: Path,
    files: dict[str, str],
    preamble: str | None = None,
    epilogue: str | None = None,
) -> Path:
    """Pack *files* into an archive, optionally wrapping with preamble/epilogue."""
    src = _make_source(tmp_path, files)
    archive = tmp_path / "archive.xml"
    args: list[str] = ["-cf", str(archive), str(src)]
    if preamble is not None:
        args += ["--preamble", preamble]
    if epilogue is not None:
        args += ["--epilogue", epilogue]
    runner = CliRunner()
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    return archive


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------


def test_create_with_preamble_prepends_text(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hello"}, preamble="My preamble\n")
    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("My preamble\n")
    assert "<archive" in raw


def test_create_with_epilogue_appends_text(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hello"}, epilogue="\nMy epilogue")
    raw = archive.read_text(encoding="utf-8")
    assert "\nMy epilogue" in raw
    assert raw.endswith("\nMy epilogue")
    assert "</archive>" in raw


def test_create_with_preamble_and_epilogue(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "content"}, preamble="START\n", epilogue="\nEND")
    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("START\n")
    assert "\nEND" in raw
    assert raw.endswith("\nEND")
    xml_start = raw.index("<archive")
    xml_end = raw.index("</archive>") + len("</archive>")
    assert "START" not in raw[xml_start:xml_end]
    assert "END" not in raw[xml_start:xml_end]


def test_create_without_preamble_epilogue_is_pure_xml(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hi"})
    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("<archive")
    assert raw.rstrip().endswith("</archive>")


# ---------------------------------------------------------------------------
# Filepath resolution for --preamble
# ---------------------------------------------------------------------------


def test_preamble_from_file_reads_content(tmp_path: Path) -> None:
    preamble_file = tmp_path / "preamble.txt"
    preamble_file.write_text("File preamble content\n", encoding="utf-8")

    src = _make_source(tmp_path, {"data.txt": "body"})
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(main, ["-cf", str(archive), str(src), "--preamble", str(preamble_file)])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("File preamble content\n")


def test_epilogue_from_file_reads_content(tmp_path: Path) -> None:
    epilogue_file = tmp_path / "epilogue.txt"
    epilogue_file.write_text("\nFile epilogue content", encoding="utf-8")

    src = _make_source(tmp_path, {"data.txt": "body"})
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(main, ["-cf", str(archive), str(src), "--epilogue", str(epilogue_file)])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert "\nFile epilogue content" in raw
    assert raw.endswith("\nFile epilogue content")


def test_preamble_nonexistent_path_uses_literal_string(tmp_path: Path) -> None:
    """A non-path string is stored as-is, even if it looks like a path."""
    src = _make_source(tmp_path, {"a.txt": "x"})
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(
        main, ["-cf", str(archive), str(src), "--preamble", "not/a/real/path.txt"]
    )
    assert result.exit_code == 0, result.output
    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("not/a/real/path.txt")


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------


def test_extract_creates_preamble_file(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hello"}, preamble="Preamble text\n")
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    preamble_file = dest / "PREAMBLE"
    assert preamble_file.exists(), "PREAMBLE file should be created"
    assert preamble_file.read_text(encoding="utf-8") == "Preamble text\n"


def test_extract_creates_epilogue_file(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hello"}, epilogue="\nEpilogue text")
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    epilogue_file = dest / "EPILOGUE"
    assert epilogue_file.exists(), "EPILOGUE file should be created"
    assert epilogue_file.read_text(encoding="utf-8") == "\nEpilogue text"


def test_extract_creates_both_preamble_and_epilogue(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "data"}, preamble="BEFORE\n", epilogue="\nAFTER")
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    assert (dest / "PREAMBLE").read_text(encoding="utf-8") == "BEFORE\n"
    assert (dest / "EPILOGUE").read_text(encoding="utf-8") == "\nAFTER"
    assert (dest / "src" / "a.txt").exists()


def test_extract_also_extracts_archive_files(tmp_path: Path) -> None:
    archive = _create_archive(
        tmp_path, {"b.txt": "B content"}, preamble="intro\n", epilogue="\noutro"
    )
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    extracted = list(dest.rglob("b.txt"))
    assert len(extracted) == 1
    assert extracted[0].read_text(encoding="utf-8") == "B content"


# ---------------------------------------------------------------------------
# Whitespace-only surrounding text — no files created
# ---------------------------------------------------------------------------


def test_no_preamble_file_when_surrounding_text_is_whitespace_only(tmp_path: Path) -> None:
    """If preamble/epilogue is only whitespace, no files should be written."""
    archive = _create_archive(tmp_path, {"a.txt": "hi"}, preamble="   \n", epilogue="\n   ")
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    assert not (dest / "PREAMBLE").exists()
    assert not (dest / "EPILOGUE").exists()


def test_no_preamble_file_when_no_preamble(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, {"a.txt": "hi"})
    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    assert not (dest / "PREAMBLE").exists()
    assert not (dest / "EPILOGUE").exists()


# ---------------------------------------------------------------------------
# Multiple archive blocks — first-match rule
# ---------------------------------------------------------------------------


def test_extract_multiple_archives_only_first_parsed(tmp_path: Path) -> None:
    """Extraction of a file with two <archive> blocks should only unpack the first."""
    first_block = (
        '<archive version="1.0">\n'
        "  <directory_tree><![CDATA[\n.\n]]></directory_tree>\n"
        '  <file path="file1.txt"><content><![CDATA[Data 1]]></content></file>\n'
        "</archive>"
    )
    second_block = (
        '<archive version="1.0">\n'
        "  <directory_tree><![CDATA[\n.\n]]></directory_tree>\n"
        '  <file path="file2.txt"><content><![CDATA[Data 2]]></content></file>\n'
        "</archive>"
    )
    raw = (
        "Here is the first archive:\n\n"
        + first_block
        + "\n\nAnd here is the second archive:\n\n"
        + second_block
        + "\n"
    )

    archive = tmp_path / "multi.xml"
    archive.write_text(raw, encoding="utf-8")

    dest = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(main, ["-xf", str(archive), str(dest)])
    assert result.exit_code == 0, result.output

    # Only file1.txt from the first archive is extracted.
    assert (dest / "file1.txt").exists()
    assert (dest / "file1.txt").read_text(encoding="utf-8") == "Data 1"
    assert not (dest / "file2.txt").exists()

    # The second archive block ends up verbatim in EPILOGUE.
    epilogue_file = dest / "EPILOGUE"
    assert epilogue_file.exists(), "EPILOGUE should contain the second archive block"
    epilogue_content = epilogue_file.read_text(encoding="utf-8")
    assert "file2.txt" in epilogue_content
    assert "Data 2" in epilogue_content
    assert second_block in epilogue_content

    # Preamble file for the text before the first archive.
    preamble_file = dest / "PREAMBLE"
    assert preamble_file.exists()
    assert preamble_file.read_text(encoding="utf-8") == "Here is the first archive:\n"


# ---------------------------------------------------------------------------
# Round-trip: Python API
# ---------------------------------------------------------------------------


def test_roundtrip_via_python_api(tmp_path: Path) -> None:
    """Write an archive with preamble/epilogue via API, then read it back."""
    archive_path = tmp_path / "archive.xml"
    src_file = tmp_path / "hello.txt"
    src_file.write_text("world", encoding="utf-8")

    with QuiverFile.open(str(archive_path), mode="w", preamble="TOP\n", epilogue="\nBOTTOM") as qf:
        qf.add(str(src_file))

    raw = archive_path.read_text(encoding="utf-8")
    assert raw.startswith("TOP\n")
    assert "BOTTOM" in raw

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive_path), mode="r") as qf:
        assert qf._preamble == "TOP\n"
        assert qf._epilogue == "\nBOTTOM"
        qf.extractall(path=str(dest))

    assert (dest / "PREAMBLE").read_text(encoding="utf-8") == "TOP\n"
    assert (dest / "EPILOGUE").read_text(encoding="utf-8") == "\nBOTTOM"

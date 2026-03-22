"""Tests for src/quiver/archive.py — QuiverFile and QuiverInfo core API."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

import quiver
from quiver.archive import BinaryFileError, QuiverFile, QuiverInfo, _normalize_path

# ---------------------------------------------------------------------------
# QuiverInfo
# ---------------------------------------------------------------------------


def test_quiverinfo_isfile() -> None:
    info = QuiverInfo(name="hello.txt", size=42)
    assert info.isfile() is True


def test_quiverinfo_isdir() -> None:
    info = QuiverInfo(name="hello.txt", size=42)
    assert info.isdir() is False


def test_quiverinfo_repr() -> None:
    info = QuiverInfo(name="foo.txt", size=10)
    assert "foo.txt" in repr(info)
    assert "10" in repr(info)


# ---------------------------------------------------------------------------
# QuiverFile — construction / mode validation
# ---------------------------------------------------------------------------


def test_valid_modes() -> None:
    for mode in ("r", "w", "a"):
        qf = QuiverFile("archive.xml", mode=mode)
        assert qf._mode == mode


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError, match="Invalid mode"):
        QuiverFile("archive.xml", mode="x")


def test_open_factory_returns_quiverfile() -> None:
    qf = QuiverFile.open("archive.xml", mode="w")
    assert isinstance(qf, QuiverFile)


def test_module_open_factory(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("hello", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    qf = quiver.open(str(archive_path), mode="w")
    assert isinstance(qf, QuiverFile)
    qf.add(str(input_file))
    qf.close()


# ---------------------------------------------------------------------------
# QuiverFile — write mode: add() and close()
# ---------------------------------------------------------------------------


def test_pack_single_file_produces_xml(tmp_path: Path) -> None:
    input_file = tmp_path / "hello.txt"
    input_file.write_text("Hello, world!", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(input_file))

    xml_text = archive_path.read_text(encoding="utf-8")
    root = etree.fromstring(xml_text.encode())
    assert root.tag == "archive"
    assert root.get("version") == "1.0"
    file_elem = root.find("file")
    assert file_elem is not None
    assert file_elem.get("path", "").endswith("hello.txt")
    content_elem = file_elem.find("content")
    assert content_elem is not None
    assert content_elem.text == "Hello, world!"


def test_xml_uses_cdata_not_entity_encoding(tmp_path: Path) -> None:
    """Special characters must appear raw inside CDATA, not entity-encoded."""
    special = "a < b && c > d"
    input_file = tmp_path / "special.txt"
    input_file.write_text(special, encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(input_file))

    raw_xml = archive_path.read_text(encoding="utf-8")
    assert "<![CDATA[" in raw_xml
    assert "&lt;" not in raw_xml
    assert "&amp;" not in raw_xml
    root = etree.fromstring(raw_xml.encode())
    assert root.find(".//content").text == special  # type: ignore[union-attr]


def test_entries_sorted_alphabetically(tmp_path: Path) -> None:
    zebra = tmp_path / "zebra.txt"
    apple = tmp_path / "apple.txt"
    mango = tmp_path / "mango.txt"
    zebra.write_text("z", encoding="utf-8")
    apple.write_text("a", encoding="utf-8")
    mango.write_text("m", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(zebra))
        qf.add(str(apple))
        qf.add(str(mango))

    raw_xml = archive_path.read_text(encoding="utf-8")
    root = etree.fromstring(raw_xml.encode())
    paths = [el.get("path") for el in root.findall("file")]
    assert paths == sorted(paths)


def test_arcname_overrides_stored_path(tmp_path: Path) -> None:
    input_file = tmp_path / "real_name.txt"
    input_file.write_text("data", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(input_file), arcname="stored/as/custom.txt")

    raw_xml = archive_path.read_text(encoding="utf-8")
    root = etree.fromstring(raw_xml.encode())
    assert root.find("file").get("path") == "stored/as/custom.txt"  # type: ignore[union-attr]


def test_add_nonexistent_file_raises(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive_path), mode="w"), pytest.raises(FileNotFoundError):
        QuiverFile.open(str(archive_path), mode="w").add(str(tmp_path / "does_not_exist.txt"))


def test_add_binary_file_raises(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError, match="UTF-8"),
    ):
        qf.add(str(binary))


def test_add_directory_recursively_packs_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    docs = project / "docs"
    src.mkdir(parents=True)
    docs.mkdir(parents=True)
    (src / "main.py").write_text("print('hi')", encoding="utf-8")
    (src / "utils.py").write_text("VALUE = 1", encoding="utf-8")
    (docs / "readme.md").write_text("# Readme", encoding="utf-8")

    output = tmp_path / "archive.xml"
    with QuiverFile.open(str(output), mode="w") as qf:
        qf.add(str(project))

    root = etree.fromstring(output.read_text(encoding="utf-8").encode())
    paths = [elem.get("path") for elem in root.findall("file")]
    assert paths == ["docs/readme.md", "src/main.py", "src/utils.py"]


def test_add_directory_binary_file_raises(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "ok.txt").write_text("hello", encoding="utf-8")
    (project / "bad.bin").write_bytes(b"\xff\xfe\x00")

    output = tmp_path / "archive.xml"
    with QuiverFile.open(str(output), mode="w") as qf, pytest.raises(BinaryFileError):
        qf.add(str(project))


def test_add_directory_arcname_prefixes_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("A", encoding="utf-8")

    output = tmp_path / "archive.xml"
    with QuiverFile.open(str(output), mode="w") as qf:
        qf.add(str(project), arcname="bundle")

    root = etree.fromstring(output.read_text(encoding="utf-8").encode())
    assert root.find("file").get("path") == "bundle/nested/a.txt"  # type: ignore[union-attr]


def test_add_in_read_mode_raises(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="mode"), QuiverFile.open("dummy.xml", mode="r") as qf:
        qf.add(str(input_file))


def test_context_manager_writes_on_exit(tmp_path: Path) -> None:
    input_file = tmp_path / "foo.txt"
    input_file.write_text("foo content", encoding="utf-8")
    output = tmp_path / "out.xml"
    with QuiverFile.open(str(output), mode="w") as qf:
        qf.add(str(input_file))
    assert output.exists()


def test_close_is_idempotent(tmp_path: Path) -> None:
    input_file = tmp_path / "foo.txt"
    input_file.write_text("foo", encoding="utf-8")
    output = tmp_path / "out.xml"
    qf = QuiverFile.open(str(output), mode="w")
    qf.add(str(input_file))
    qf.close()
    qf.close()


# ---------------------------------------------------------------------------
# getnames() and getmembers()
# ---------------------------------------------------------------------------


def test_getnames_in_write_mode(tmp_path: Path) -> None:
    a_file = tmp_path / "a.txt"
    b_file = tmp_path / "b.txt"
    a_file.write_text("a", encoding="utf-8")
    b_file.write_text("b", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(a_file))
        qf.add(str(b_file))
        names = qf.getnames()
    assert any(name.endswith("a.txt") for name in names)
    assert any(name.endswith("b.txt") for name in names)


def test_getmembers_returns_quiverinfo_objects(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("hello", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(sample))
        members = qf.getmembers()
    assert len(members) == 1
    assert isinstance(members[0], QuiverInfo)
    assert members[0].name.endswith("sample.txt")
    assert members[0].size == len(b"hello")


def test_getnames_in_read_mode_raises() -> None:
    qf = QuiverFile.open("dummy.xml", mode="r")
    with pytest.raises(NotImplementedError):
        qf.getnames()


def test_getmembers_in_read_mode_raises() -> None:
    qf = QuiverFile.open("dummy.xml", mode="r")
    with pytest.raises(NotImplementedError):
        qf.getmembers()


# ---------------------------------------------------------------------------
# extractall() — scaffolded as NotImplementedError
# ---------------------------------------------------------------------------


def test_extractall_not_implemented() -> None:
    qf = QuiverFile.open("dummy.xml", mode="r")
    with pytest.raises(NotImplementedError):
        qf.extractall()


# ---------------------------------------------------------------------------
# Path normalization helper
# ---------------------------------------------------------------------------


def test_normalize_path_posix() -> None:
    result = _normalize_path(Path("subdir/file.txt"))
    assert "/" in result
    assert "\\" not in result


def test_normalize_path_strips_leading_slash() -> None:
    result = _normalize_path(Path("/abs/path/file.txt"))
    assert not result.startswith("/")


def test_normalize_path_simple_filename() -> None:
    result = _normalize_path(Path("simple.txt"))
    assert result == "simple.txt"

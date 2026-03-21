"""Tests for src/quiver/archive.py — QuiverFile and QuiverInfo core API."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from lxml import etree

import quiver
from quiver.archive import BinaryFileError, QuiverFile, QuiverInfo, _normalize_path

if TYPE_CHECKING:
    from pyfakefs.fake_filesystem import FakeFilesystem


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


def test_module_open_factory(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("input.txt", contents="hello")
    qf = quiver.open("archive.xml", mode="w")
    assert isinstance(qf, QuiverFile)
    qf.add("input.txt")
    qf.close()


# ---------------------------------------------------------------------------
# QuiverFile — write mode: add() and close()
# ---------------------------------------------------------------------------


def test_pack_single_file_produces_xml(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("hello.txt", contents="Hello, world!")
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("hello.txt")

    xml_text = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    root = etree.fromstring(xml_text.encode())
    assert root.tag == "archive"
    assert root.get("version") == "1.0"
    file_elem = root.find("file")
    assert file_elem is not None
    assert file_elem.get("path") == "hello.txt"
    content_elem = file_elem.find("content")
    assert content_elem is not None
    assert content_elem.text == "Hello, world!"


def test_xml_uses_cdata_not_entity_encoding(fake_fs: FakeFilesystem) -> None:
    """Special characters must appear raw inside CDATA, not entity-encoded."""
    special = "a < b && c > d"
    fake_fs.create_file("special.txt", contents=special)
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("special.txt")

    raw_xml = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    # CDATA markers must be present in the raw serialized XML
    assert "<![CDATA[" in raw_xml
    # Entity-encoded forms must NOT be present
    assert "&lt;" not in raw_xml
    assert "&amp;" not in raw_xml
    # The content should round-trip cleanly through lxml
    root = etree.fromstring(raw_xml.encode())
    assert root.find(".//content").text == special  # type: ignore[union-attr]


def test_entries_sorted_alphabetically(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("zebra.txt", contents="z")
    fake_fs.create_file("apple.txt", contents="a")
    fake_fs.create_file("mango.txt", contents="m")
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("zebra.txt")
        qf.add("apple.txt")
        qf.add("mango.txt")

    raw_xml = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    root = etree.fromstring(raw_xml.encode())
    paths = [el.get("path") for el in root.findall("file")]
    assert paths == sorted(paths)


def test_arcname_overrides_stored_path(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("real_name.txt", contents="data")
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("real_name.txt", arcname="stored/as/custom.txt")

    raw_xml = fake_fs.get_object("archive.xml").contents  # type: ignore[union-attr]
    root = etree.fromstring(raw_xml.encode())
    assert root.find("file").get("path") == "stored/as/custom.txt"  # type: ignore[union-attr]


def test_add_nonexistent_file_raises() -> None:
    with QuiverFile.open("archive.xml", mode="w"), pytest.raises(FileNotFoundError):
        QuiverFile.open("archive.xml", mode="w").add("does_not_exist.txt")


def test_add_binary_file_raises(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("binary.bin", contents=b"\xff\xfe\x00\x01", apply_umask=True)
    with (
        QuiverFile.open("archive.xml", mode="w") as qf,
        pytest.raises(BinaryFileError, match="UTF-8"),
    ):
        qf.add("binary.bin")


def test_add_directory_raises(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_dir("mydir")
    with QuiverFile.open("archive.xml", mode="w") as qf, pytest.raises(IsADirectoryError):
        qf.add("mydir")


def test_add_in_read_mode_raises(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("input.txt", contents="data")
    with pytest.raises(ValueError, match="mode"), QuiverFile.open("dummy.xml", mode="r") as qf:
        qf.add("input.txt")


def test_context_manager_writes_on_exit(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("foo.txt", contents="foo content")
    with QuiverFile.open("out.xml", mode="w") as qf:
        qf.add("foo.txt")
    # File must exist after context exit
    assert fake_fs.get_object("out.xml") is not None


def test_close_is_idempotent(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("foo.txt", contents="foo")
    qf = QuiverFile.open("out.xml", mode="w")
    qf.add("foo.txt")
    qf.close()
    qf.close()  # second close must not raise


# ---------------------------------------------------------------------------
# getnames() and getmembers()
# ---------------------------------------------------------------------------


def test_getnames_in_write_mode(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("a.txt", contents="a")
    fake_fs.create_file("b.txt", contents="b")
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("a.txt")
        qf.add("b.txt")
        names = qf.getnames()
    assert "a.txt" in names
    assert "b.txt" in names


def test_getmembers_returns_quiverinfo_objects(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("sample.txt", contents="hello")
    with QuiverFile.open("archive.xml", mode="w") as qf:
        qf.add("sample.txt")
        members = qf.getmembers()
    assert len(members) == 1
    assert isinstance(members[0], QuiverInfo)
    assert members[0].name == "sample.txt"
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


def test_normalize_path_posix(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("subdir/file.txt", contents="x")
    result = _normalize_path(Path("subdir/file.txt"))
    assert "/" in result
    assert "\\" not in result


def test_normalize_path_strips_leading_slash(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("/abs/path/file.txt", contents="x")
    result = _normalize_path(Path("/abs/path/file.txt"))
    assert not result.startswith("/")


def test_normalize_path_simple_filename(fake_fs: FakeFilesystem) -> None:
    fake_fs.create_file("simple.txt", contents="x")
    result = _normalize_path(Path("simple.txt"))
    assert result == "simple.txt"

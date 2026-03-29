"""Tests for src/quiver/archive.py — QuiverFile and QuiverInfo core API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lxml import etree

import quiver
from quiver.archive import BinaryFileError, QuiverFile, QuiverInfo

if TYPE_CHECKING:
    from pathlib import Path

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


def test_valid_modes(tmp_path: Path) -> None:
    for mode in ("w", "a"):
        qf = QuiverFile("archive.xml", mode=mode)
        assert qf._mode == mode
    # Read mode requires an existing archive file.
    archive = tmp_path / "archive.xml"
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with QuiverFile(str(archive), mode="w") as qf:
        qf.add(str(f))
    qf_r = QuiverFile(str(archive), mode="r")
    assert qf_r._mode == "r"


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


# ---------------------------------------------------------------------------
# XML-incompatible control character validation
# ---------------------------------------------------------------------------


def test_add_file_with_null_byte_raises(tmp_path: Path) -> None:
    """A UTF-8 file containing a NULL byte must raise BinaryFileError at add() time."""
    bad = tmp_path / "null.txt"
    bad.write_bytes(b"hello\x00world")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError, match=r"\\x00"),
    ):
        qf.add(str(bad))


def test_add_file_with_control_char_raises(tmp_path: Path) -> None:
    """A UTF-8 file containing a C0 control character must raise BinaryFileError."""
    bad = tmp_path / "ctrl.txt"
    bad.write_bytes(b"line one\nline two\x07bell")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError, match=r"\\x07"),
    ):
        qf.add(str(bad))


def test_xml_control_char_error_contains_file_path(tmp_path: Path) -> None:
    """BinaryFileError message must include the offending file's path."""
    bad = tmp_path / "offender.txt"
    bad.write_bytes(b"bad\x01byte")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError) as exc_info,
    ):
        qf.add(str(bad))

    assert "offender.txt" in str(exc_info.value)


def test_xml_control_char_error_contains_line_and_col(tmp_path: Path) -> None:
    """BinaryFileError message must include a line number and column for the bad char."""
    # Place the bad character on line 2, column 4 (1-based).
    bad = tmp_path / "located.txt"
    bad.write_bytes(b"first\nsec\x1fond")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError) as exc_info,
    ):
        qf.add(str(bad))

    msg = str(exc_info.value)
    assert "line 2" in msg
    assert "col 4" in msg
    assert r"\x1f" in msg


def test_xml_control_char_error_multiple_occurrences(tmp_path: Path) -> None:
    """BinaryFileError message reports up to 5 (MAX_REPORTED_OFFENCES) locations."""
    bad = tmp_path / "many.txt"
    # 10 forbidden chars — only the first _MAX_REPORTED_OFFENCES should appear.
    bad.write_bytes(b"\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError) as exc_info,
    ):
        qf.add(str(bad))

    msg = str(exc_info.value)
    # Should have at most 5 "line N, col M" entries.
    reported = msg.count("line ")
    assert reported == 5


def test_add_directory_with_control_char_file_raises(tmp_path: Path) -> None:
    """async pipeline: a control-char file inside a packed directory raises BinaryFileError."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "ok.txt").write_text("clean", encoding="utf-8")
    bad = project / "ctrl.txt"
    bad.write_bytes(b"text\x0bvt")
    archive_path = tmp_path / "archive.xml"

    with (
        QuiverFile.open(str(archive_path), mode="w") as qf,
        pytest.raises(BinaryFileError, match=r"\\x0b"),
    ):
        qf.add(str(project))


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
    assert paths == ["project/docs/readme.md", "project/src/main.py", "project/src/utils.py"]


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
    archive_path = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(input_file))
    with (
        pytest.raises(ValueError, match="mode"),
        QuiverFile.open(str(archive_path), mode="r") as qf,
    ):
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


# ---------------------------------------------------------------------------
# extractall() — extraction
# ---------------------------------------------------------------------------


def test_extractall_recreates_files(tmp_path: Path) -> None:
    """Round-trip: pack files then extractall() recreates the originals."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")
    nested = src / "sub"
    nested.mkdir()
    (nested / "b.txt").write_text("beta", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(src))

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive), mode="r") as qf:
        qf.extractall(path=str(dest))

    assert (dest / "src" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (dest / "src" / "sub" / "b.txt").read_text(encoding="utf-8") == "beta"


def test_extractall_creates_intermediate_dirs(tmp_path: Path) -> None:
    """Intermediate parent directories are created automatically."""
    src = tmp_path / "src"
    deep = src / "x" / "y" / "z"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("deep", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(src))

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive), mode="r") as qf:
        qf.extractall(path=str(dest))

    assert (dest / "src" / "x" / "y" / "z" / "deep.txt").read_text(encoding="utf-8") == "deep"


def test_extractall_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """extractall() with no path argument writes to the current working directory."""
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "hello.txt"
    f.write_text("hi", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f))

    with QuiverFile.open(str(archive), mode="r") as qf:
        qf.extractall()

    # The extracted file should be relative to cwd (tmp_path).
    extracted = list(tmp_path.glob("*.txt"))
    names = {p.name for p in extracted}
    assert "hello.txt" in names


def test_extractall_with_members_filter(tmp_path: Path) -> None:
    """Only the specified members are extracted when members= is given."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.txt").write_text("keep", encoding="utf-8")
    (src / "skip.txt").write_text("skip", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(src))

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive), mode="r") as qf:
        members = [m for m in qf.getmembers() if m.name.endswith("keep.txt")]
        qf.extractall(path=str(dest), members=members)

    assert (dest / "src" / "keep.txt").exists()
    assert not (dest / "src" / "skip.txt").exists()


def test_extractall_in_write_mode_raises(tmp_path: Path) -> None:
    """extractall() raises ValueError when the archive is open for writing."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf, pytest.raises(ValueError, match="mode"):
        qf.extractall()


# ---------------------------------------------------------------------------
# Path traversal protection (via extractall)
# ---------------------------------------------------------------------------


def test_extractall_rejects_absolute_path_entry(tmp_path: Path) -> None:
    """extractall() raises PathTraversalError for an archive entry with an absolute path."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("safe.txt", "ok")

    # Manually inject an absolute path that bypasses add_text normalization.
    raw = archive.read_text(encoding="utf-8")
    raw = raw.replace('path="safe.txt"', 'path="/etc/passwd"')
    archive.write_text(raw, encoding="utf-8")

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive), mode="r") as qf, pytest.raises(Exception, match="absolute"):
        qf.extractall(path=str(dest))


def test_extractall_rejects_traversal_path_entry(tmp_path: Path) -> None:
    """extractall() raises PathTraversalError for an archive entry containing '..'."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("safe.txt", "ok")

    # Manually craft the raw XML to inject a traversal path that bypasses
    # add()'s normalization (add_text also normalizes, so write the file directly).
    raw = archive.read_text(encoding="utf-8")
    raw = raw.replace('path="safe.txt"', 'path="../../escape.txt"')
    archive.write_text(raw, encoding="utf-8")

    dest = tmp_path / "out"
    with QuiverFile.open(str(archive), mode="r") as qf, pytest.raises(Exception, match=r"\.\."):
        qf.extractall(path=str(dest))


# ---------------------------------------------------------------------------
# QuiverFile read-mode round-trip
# ---------------------------------------------------------------------------


def test_read_mode_round_trip(tmp_path: Path) -> None:
    """Opening an archive in read mode returns entries matching what was packed."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("alpha", encoding="utf-8")
    f2.write_text("beta", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f1))
        qf.add(str(f2))

    with QuiverFile.open(str(archive), mode="r") as qf:
        entry_map = {info.name: content for info, content in qf.entries}
    assert any(k.endswith("a.txt") for k in entry_map)
    assert any(k.endswith("b.txt") for k in entry_map)
    assert "alpha" in entry_map.values()
    assert "beta" in entry_map.values()


# ---------------------------------------------------------------------------
# getnames() and getmembers() in read mode
# ---------------------------------------------------------------------------


def test_getnames_read_mode(tmp_path: Path) -> None:
    """getnames() works in read mode after archive is opened."""
    f = tmp_path / "sample.txt"
    f.write_text("hello", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f))

    with QuiverFile.open(str(archive), mode="r") as qf:
        names = qf.getnames()
    assert any(n.endswith("sample.txt") for n in names)


def test_getmembers_read_mode(tmp_path: Path) -> None:
    """getmembers() returns QuiverInfo objects in read mode."""
    f = tmp_path / "sample.txt"
    f.write_text("hello", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f))

    with QuiverFile.open(str(archive), mode="r") as qf:
        members = qf.getmembers()
    assert len(members) == 1
    assert isinstance(members[0], QuiverInfo)
    assert members[0].size == len(b"hello")


def test_open_read_mode_missing_archive_raises(tmp_path: Path) -> None:
    """Opening a non-existent archive in read mode raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        QuiverFile.open(str(tmp_path / "no_such.xml"), mode="r")


# ---------------------------------------------------------------------------
# Path normalization (via public API)
# ---------------------------------------------------------------------------


def test_stored_path_uses_posix_separators(tmp_path: Path) -> None:
    """Stored paths in the archive always use forward slashes."""
    nested = tmp_path / "project" / "sub"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("x", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(tmp_path / "project"))
    with QuiverFile.open(str(archive), mode="r") as qf:
        names = qf.getnames()
    assert all("/" in n for n in names)
    assert all("\\" not in n for n in names)


def test_stored_path_is_relative(tmp_path: Path) -> None:
    """Stored paths must never start with '/'."""
    f = tmp_path / "hello.txt"
    f.write_text("hi", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f))
    with QuiverFile.open(str(archive), mode="r") as qf:
        names = qf.getnames()
    assert all(not n.startswith("/") for n in names)


def test_arcname_simple_filename_stored_as_is(tmp_path: Path) -> None:
    """A simple arcname with no directory components is stored verbatim."""
    f = tmp_path / "original.txt"
    f.write_text("data", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f), arcname="simple.txt")
    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.getnames() == ["simple.txt"]


# ---------------------------------------------------------------------------
# directory_tree — integration tests
# ---------------------------------------------------------------------------


def test_xml_contains_directory_tree_element(tmp_path: Path) -> None:
    """The XML output must contain a <directory_tree> element."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "main.py").write_text("print('hi')", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(project))

    root = etree.fromstring(archive_path.read_text(encoding="utf-8").encode())
    tree_elem = root.find("directory_tree")
    assert tree_elem is not None
    assert tree_elem.text is not None
    assert "main.py" in tree_elem.text


def test_directory_tree_precedes_file_elements(tmp_path: Path) -> None:
    """<directory_tree> must be the first child of <archive>, before all <file> elements."""
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(f))

    root = etree.fromstring(archive_path.read_text(encoding="utf-8").encode())
    assert root[0].tag == "directory_tree"
    for child in root[1:]:
        assert child.tag == "file"


def test_directory_tree_deep_nesting_integration(tmp_path: Path) -> None:
    """Tree text in XML accurately reflects deeply nested directory structures."""
    project = tmp_path / "project"
    deep = project / "x" / "y" / "z"
    deep.mkdir(parents=True)
    (project / "top.txt").write_text("t", encoding="utf-8")
    (project / "x" / "y" / "other.txt").write_text("o", encoding="utf-8")
    (deep / "deep.txt").write_text("d", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(project))

    root = etree.fromstring(archive_path.read_text(encoding="utf-8").encode())
    tree_text = root.find("directory_tree").text  # type: ignore[union-attr]
    assert "x/" in tree_text
    assert "y/" in tree_text
    assert "z/" in tree_text
    assert "deep.txt" in tree_text
    assert "top.txt" in tree_text
    # Verify multi-level indentation is present
    assert "    " in tree_text


def test_directory_tree_uses_cdata(tmp_path: Path) -> None:
    """The <directory_tree> element must be serialized with CDATA, not entity encoding."""
    f = tmp_path / "file.txt"
    f.write_text("content", encoding="utf-8")
    archive_path = tmp_path / "archive.xml"

    with QuiverFile.open(str(archive_path), mode="w") as qf:
        qf.add(str(f))

    raw_xml = archive_path.read_text(encoding="utf-8")
    # The CDATA marker must appear before the first </directory_tree>
    tree_close = raw_xml.index("</directory_tree>")
    tree_open_cdata = raw_xml.index("<![CDATA[")
    assert tree_open_cdata < tree_close


def test_directory_tree_empty_archive(tmp_path: Path) -> None:
    """An archive with no files must still contain <directory_tree> with just '.'."""
    archive = tmp_path / "empty.xml"
    # Write an archive with no entries via the public API.
    with QuiverFile.open(str(archive), mode="w"):
        pass

    raw_xml = archive.read_text(encoding="utf-8")
    root = etree.fromstring(raw_xml.encode("utf-8"))  # noqa: S320
    tree_elem = root.find("directory_tree")
    assert tree_elem is not None
    assert tree_elem.text is not None
    assert tree_elem.text.strip() == "."


def test_xml_structure_validated_by_lxml(tmp_path: Path) -> None:
    """Full XML round-trip: QuiverFile output is valid XML and contains correct data.

    Builds a small project with a mix of flat files and deeply nested
    directories, packs it, reads the raw XML, and validates the structure
    using lxml as an independent parser.
    """
    # Create a realistic project layout.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "README.md").write_text("# Project", encoding="utf-8")
    (src / "main.py").write_text("print('hello')", encoding="utf-8")
    (src / "utils.py").write_text("VALUE = 42", encoding="utf-8")
    sub = src / "sub"
    sub.mkdir()
    (sub / "helper.py").write_text("def helper(): pass", encoding="utf-8")

    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(project))

    # Re-parse the raw XML with lxml to validate structure independently.
    raw_xml = archive.read_text(encoding="utf-8")
    root = etree.fromstring(raw_xml.encode("utf-8"))  # noqa: S320

    # Root element checks.
    assert root.tag == "archive"
    assert root.get("version") == "1.0"

    # <directory_tree> must be the first child and mention all paths.
    tree_elem = root[0]
    assert tree_elem.tag == "directory_tree"
    assert tree_elem.text is not None
    tree_text = tree_elem.text
    assert "README.md" in tree_text
    assert "main.py" in tree_text
    assert "utils.py" in tree_text
    assert "helper.py" in tree_text

    # All <file> elements must follow <directory_tree>.
    file_elems = root.findall("file")
    assert len(file_elems) == 4
    for child in root[1:]:
        assert child.tag == "file"

    # Paths are sorted alphabetically.
    paths = [el.get("path") for el in file_elems]
    assert paths == sorted(paths)

    # Content is stored verbatim inside <content>.
    content_map = {
        el.get("path"): el.find("content").text  # type: ignore[union-attr]
        for el in file_elems
    }
    assert any(k is not None and k.endswith("README.md") for k in content_map)
    readme_key = next(k for k in content_map if k is not None and k.endswith("README.md"))
    assert content_map[readme_key] == "# Project"
    main_key = next(
        k for k in content_map if k is not None and k.endswith("main.py") and "sub" not in k
    )
    assert content_map[main_key] == "print('hello')"
    helper_key = next(k for k in content_map if k is not None and k.endswith("helper.py"))
    assert content_map[helper_key] == "def helper(): pass"

    # Verify raw XML uses CDATA and not entity-encoded characters.
    assert "<![CDATA[" in raw_xml
    assert "&amp;" not in raw_xml
    assert "&lt;" not in raw_xml


# ---------------------------------------------------------------------------
# QuiverFile public properties (preamble, epilogue, entries)
# ---------------------------------------------------------------------------


def test_preamble_property_none_when_not_set(tmp_path: Path) -> None:
    """preamble is None when no preamble was supplied or parsed."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        assert qf.preamble is None


def test_preamble_property_returns_value(tmp_path: Path) -> None:
    """preamble returns the text supplied at open time."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w", preamble="hello\n") as qf:
        assert qf.preamble == "hello\n"


def test_preamble_property_parsed_from_archive(tmp_path: Path) -> None:
    """preamble is parsed and returned when opening an existing archive in read mode."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w", preamble="# header\n") as qf:
        f = tmp_path / "a.txt"
        f.write_text("A", encoding="utf-8")
        qf.add(str(f), arcname="a.txt")

    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.preamble == "# header\n"


def test_epilogue_property_none_when_not_set(tmp_path: Path) -> None:
    """epilogue is None when no epilogue was supplied or parsed."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        assert qf.epilogue is None


def test_epilogue_property_returns_value(tmp_path: Path) -> None:
    """epilogue returns the text supplied at open time."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w", epilogue="# footer\n") as qf:
        assert qf.epilogue == "# footer\n"


def test_epilogue_property_parsed_from_archive(tmp_path: Path) -> None:
    """epilogue is parsed and returned when opening an existing archive in read mode."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w", epilogue="# footer\n") as qf:
        f = tmp_path / "a.txt"
        f.write_text("A", encoding="utf-8")
        qf.add(str(f), arcname="a.txt")

    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.epilogue == "# footer\n"


def test_entries_property_returns_defensive_copy(tmp_path: Path) -> None:
    """entries returns a list; mutating it does not affect the archive."""
    archive = tmp_path / "archive.xml"
    f = tmp_path / "a.txt"
    f.write_text("A", encoding="utf-8")
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f), arcname="a.txt")
        copy = qf.entries
        copy.clear()
        assert len(qf.entries) == 1


def test_entries_property_contains_info_and_content(tmp_path: Path) -> None:
    """entries exposes (QuiverInfo, str) pairs with correct name and content."""
    archive = tmp_path / "archive.xml"
    f = tmp_path / "hello.txt"
    f.write_text("world", encoding="utf-8")
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add(str(f), arcname="hello.txt")

    with QuiverFile.open(str(archive), mode="r") as qf:
        pairs = qf.entries
    assert len(pairs) == 1
    info, content = pairs[0]
    assert info.name == "hello.txt"
    assert content == "world"


# ---------------------------------------------------------------------------
# QuiverFile.add_text()
# ---------------------------------------------------------------------------


def test_add_text_inserts_entry(tmp_path: Path) -> None:
    """add_text() writes in-memory content into the archive."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("notes.txt", "some notes")

    with QuiverFile.open(str(archive), mode="r") as qf:
        assert "notes.txt" in qf.getnames()
        info, content = qf.entries[0]
        assert content == "some notes"
        assert info.size == len(b"some notes")


def test_add_text_upserts_existing_entry(tmp_path: Path) -> None:
    """add_text() replaces an existing entry with the same arcname."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("notes.txt", "old")
        qf.add_text("notes.txt", "new")

    with QuiverFile.open(str(archive), mode="r") as qf:
        assert qf.getnames().count("notes.txt") == 1
        _, content = qf.entries[0]
        assert content == "new"


def test_add_text_raises_in_read_mode(tmp_path: Path) -> None:
    """add_text() raises ValueError when the archive is in read mode."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf:
        qf.add_text("a.txt", "A")

    with QuiverFile.open(str(archive), mode="r") as qf, pytest.raises(ValueError, match="mode"):
        qf.add_text("b.txt", "B")


def test_add_text_raises_after_close(tmp_path: Path) -> None:
    """add_text() raises ValueError when called after the archive is closed."""
    archive = tmp_path / "archive.xml"
    qf = QuiverFile.open(str(archive), mode="w")
    qf.close()
    with pytest.raises(ValueError, match="closed"):
        qf.add_text("a.txt", "A")


def test_add_text_rejects_absolute_arcname(tmp_path: Path) -> None:
    """add_text() raises PathTraversalError when arcname is an absolute path."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf, pytest.raises(Exception, match="absolute"):
        qf.add_text("/etc/passwd", "evil")


def test_add_text_rejects_dotdot_arcname(tmp_path: Path) -> None:
    """add_text() raises PathTraversalError when arcname contains '..'."""
    archive = tmp_path / "archive.xml"
    with QuiverFile.open(str(archive), mode="w") as qf, pytest.raises(Exception, match=r"\.\."):
        qf.add_text("../../escape.txt", "evil")


def test_add_text_rejects_xml_incompatible_content(tmp_path: Path) -> None:
    """add_text() raises BinaryFileError when content contains XML-forbidden control chars."""
    archive = tmp_path / "archive.xml"
    with (
        QuiverFile.open(str(archive), mode="w") as qf,
        pytest.raises(BinaryFileError, match=r"\\x00"),
    ):
        qf.add_text("notes.txt", "hello\x00world")

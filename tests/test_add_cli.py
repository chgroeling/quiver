"""Integration tests for the quiver CLI add (-a) command."""

from __future__ import annotations

from pathlib import Path

import lxml.etree as etree
from click.testing import CliRunner

from quiver.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_archive(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a source tree from *files* and pack it into an archive.

    Args:
        tmp_path: Pytest temporary directory.
        files: Mapping of relative path → content.

    Returns:
        Path to the created archive.
    """
    src = tmp_path / "src"
    for rel, content in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(main, ["-cf", str(archive), str(src)])
    assert result.exit_code == 0, result.output
    return archive


def _parse_archive(archive: Path) -> etree._Element:
    """Parse *archive* XML and return the root element."""
    raw = archive.read_text(encoding="utf-8")
    # Strip any preamble/epilogue before parsing
    start = raw.index("<archive")
    end = raw.index("</archive>") + len("</archive>")
    return etree.fromstring(raw[start:end].encode())


def _file_paths(root: etree._Element) -> list[str]:
    """Return all <file path=...> values from *root* in document order."""
    return [elem.get("path", "") for elem in root.iter("file")]


def _file_content(root: etree._Element, path: str) -> str | None:
    """Return the CDATA content of the <file> with the given *path*, or None."""
    for elem in root.iter("file"):
        if elem.get("path") == path:
            content_elem = elem.find("content")
            return content_elem.text if content_elem is not None else None
    return None


def _directory_tree_text(root: etree._Element) -> str:
    """Return the text of the <directory_tree> element."""
    dt = root.find("directory_tree")
    assert dt is not None
    return dt.text or ""


def _make_new_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a single new file in a dedicated 'new/' subdirectory.

    Returns the parent directory (not the file), so the CLI stores just *name*
    as the archive path — matching what ``_make_archive`` produces.
    """
    new_dir = tmp_path / "new"
    new_dir.mkdir(exist_ok=True)
    (new_dir / name).write_text(content, encoding="utf-8")
    return new_dir


# ---------------------------------------------------------------------------
# Ordered insertion
# ---------------------------------------------------------------------------


def test_add_inserts_in_alphabetical_order(tmp_path: Path) -> None:
    """New entry is inserted at the correct alphabetical position by full stored path."""
    # Initial archive: grp/aaa.txt and grp/zzz.txt.
    grp = tmp_path / "grp"
    grp.mkdir()
    (grp / "aaa.txt").write_text("aaa", encoding="utf-8")
    (grp / "zzz.txt").write_text("zzz", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(main, ["-cf", str(archive), str(grp)])
    assert result.exit_code == 0, result.output

    # Add grp/mmm.txt using a second dir with the same name in a sub-tmp location.
    # Since both dirs are named 'grp', stored paths share the prefix and sort as:
    # grp/aaa.txt < grp/mmm.txt < grp/zzz.txt.
    grp2 = tmp_path / "add" / "grp"
    grp2.mkdir(parents=True)
    (grp2 / "mmm.txt").write_text("mmm", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(grp2)])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert "grp/mmm.txt" in paths
    assert "grp/aaa.txt" in paths
    assert "grp/zzz.txt" in paths
    aaa_idx = paths.index("grp/aaa.txt")
    mmm_idx = paths.index("grp/mmm.txt")
    zzz_idx = paths.index("grp/zzz.txt")
    assert aaa_idx < mmm_idx < zzz_idx, f"Expected aaa < mmm < zzz, got paths {paths}"


def test_add_inserts_at_beginning(tmp_path: Path) -> None:
    """New entry that sorts first by full path is placed before all existing entries."""
    # Initial archive: grp/mmm.txt, grp/zzz.txt.
    grp = tmp_path / "grp"
    grp.mkdir()
    (grp / "mmm.txt").write_text("mmm", encoding="utf-8")
    (grp / "zzz.txt").write_text("zzz", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(main, ["-cf", str(archive), str(grp)])
    assert result.exit_code == 0, result.output

    # grp/aaa.txt sorts before grp/mmm.txt.
    grp2 = tmp_path / "add" / "grp"
    grp2.mkdir(parents=True)
    (grp2 / "aaa.txt").write_text("aaa", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(grp2)])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert paths[0] == "grp/aaa.txt", f"Expected first path grp/aaa.txt, got {paths}"


def test_add_inserts_at_end(tmp_path: Path) -> None:
    """New entry that sorts last by full path is appended after all existing entries."""
    # Initial archive: grp/aaa.txt, grp/mmm.txt.
    grp = tmp_path / "grp"
    grp.mkdir()
    (grp / "aaa.txt").write_text("aaa", encoding="utf-8")
    (grp / "mmm.txt").write_text("mmm", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(main, ["-cf", str(archive), str(grp)])
    assert result.exit_code == 0, result.output

    # grp/zzz.txt sorts after grp/mmm.txt.
    grp2 = tmp_path / "add" / "grp"
    grp2.mkdir(parents=True)
    (grp2 / "zzz.txt").write_text("zzz", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(grp2)])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert paths[-1] == "grp/zzz.txt", f"Expected last path grp/zzz.txt, got {paths}"


# ---------------------------------------------------------------------------
# Upsert (overwrite)
# ---------------------------------------------------------------------------


def test_add_upserts_existing_file(tmp_path: Path) -> None:
    """Adding a file whose path already exists in the archive replaces its content."""
    archive = _make_archive(tmp_path, {"hello.txt": "original content"})
    # _make_archive packs a src/ directory, so the stored path is "src/hello.txt".
    # Replicate the same directory name so the path matches and an upsert happens.
    update_src = tmp_path / "src"
    update_src.mkdir(exist_ok=True)
    (update_src / "hello.txt").write_text("updated content", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(update_src)])
    assert result.exit_code == 0, result.output

    root = _parse_archive(archive)
    # Find the entry — its stored path ends with 'hello.txt'.
    paths = _file_paths(root)
    hello_paths = [p for p in paths if p.endswith("hello.txt")]
    assert len(hello_paths) == 1, f"Expected exactly one hello.txt entry, got {paths}"
    assert _file_content(root, hello_paths[0]) == "updated content"
def test_add_upsert_does_not_duplicate_entry(tmp_path: Path) -> None:
    """After upserting an existing path the total number of <file> elements is unchanged."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    original_count = len(_file_paths(_parse_archive(archive)))

    # _make_archive stores files under src/, so use the same dir name to match.
    update_src = tmp_path / "src"
    update_src.mkdir(exist_ok=True)
    (update_src / "a.txt").write_text("A-new", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(update_src)])
    assert result.exit_code == 0, result.output

    assert len(_file_paths(_parse_archive(archive))) == original_count


# ---------------------------------------------------------------------------
# Directory tree regeneration
# ---------------------------------------------------------------------------


def test_add_regenerates_directory_tree(tmp_path: Path) -> None:
    """After adding a new file the <directory_tree> includes both old and new paths."""
    archive = _make_archive(tmp_path, {"old.txt": "old"})

    new_dir = _make_new_file(tmp_path, "new.txt", "new")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output

    tree = _directory_tree_text(_parse_archive(archive))
    assert "old.txt" in tree
    assert "new.txt" in tree


def test_add_directory_tree_reflects_upserted_path(tmp_path: Path) -> None:
    """After upserting, the <directory_tree> is consistent with the <file> list."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    # Use the same dir name as _make_archive (src/) so the path matches for an upsert.
    update_src = tmp_path / "src"
    update_src.mkdir(exist_ok=True)
    (update_src / "b.txt").write_text("B-updated", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(update_src)])
    assert result.exit_code == 0, result.output

    root = _parse_archive(archive)
    tree = _directory_tree_text(root)
    for path in _file_paths(root):
        leaf = path.split("/")[-1]
        assert leaf in tree, f"Expected {leaf!r} in directory_tree"


# ---------------------------------------------------------------------------
# Atomic swap integrity
# ---------------------------------------------------------------------------


def test_add_original_untouched_on_error(tmp_path: Path) -> None:
    """If an error occurs mid-write the original archive is left intact."""
    archive = _make_archive(tmp_path, {"safe.txt": "safe"})
    original_content = archive.read_text(encoding="utf-8")

    # Provide a non-existent input path to trigger FileNotFoundError.
    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(tmp_path / "nonexistent.txt")])
    assert result.exit_code != 0

    # Original archive content must be byte-for-byte identical.
    assert archive.read_text(encoding="utf-8") == original_content


def test_add_no_tmp_file_left_on_error(tmp_path: Path) -> None:
    """The .tmp file is cleaned up when an error is raised before the atomic swap."""
    archive = _make_archive(tmp_path, {"safe.txt": "safe"})

    runner = CliRunner()
    runner.invoke(main, ["-af", str(archive), str(tmp_path / "nonexistent.txt")])

    assert not Path(str(archive) + ".tmp").exists()


# ---------------------------------------------------------------------------
# Preamble / epilogue preservation
# ---------------------------------------------------------------------------


def test_add_preserves_preamble(tmp_path: Path) -> None:
    """Existing preamble text survives an upsert operation."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("A", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(main, ["-cf", str(archive), str(src), "--preamble", "MY PREAMBLE TEXT"])
    assert result.exit_code == 0, result.output

    new_dir = _make_new_file(tmp_path, "b.txt", "B")
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("MY PREAMBLE TEXT")


def test_add_preserves_epilogue(tmp_path: Path) -> None:
    """Existing epilogue text survives an upsert operation."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("A", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(main, ["-cf", str(archive), str(src), "--epilogue", "MY EPILOGUE TEXT"])
    assert result.exit_code == 0, result.output

    new_dir = _make_new_file(tmp_path, "b.txt", "B")
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.endswith("MY EPILOGUE TEXT")


def test_add_preserves_preamble_and_epilogue(tmp_path: Path) -> None:
    """Both preamble and epilogue survive an upsert round-trip."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("A", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-cf", str(archive), str(src), "--preamble", "PRE", "--epilogue", "POST"],
    )
    assert result.exit_code == 0, result.output

    new_dir = _make_new_file(tmp_path, "z.txt", "Z")
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("PRE")
    assert raw.endswith("POST")


# ---------------------------------------------------------------------------
# Silent-by-default & verbose
# ---------------------------------------------------------------------------


def test_add_silent_by_default(tmp_path: Path) -> None:
    """No stdout output when -v is not supplied."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})
    new_dir = _make_new_file(tmp_path, "b.txt", "B")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output
    assert result.output == ""


def test_add_verbose_produces_output(tmp_path: Path) -> None:
    """With -v some non-empty output is produced."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})
    new_dir = _make_new_file(tmp_path, "b.txt", "B")

    runner = CliRunner()
    result = runner.invoke(main, ["-avf", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() != ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_add_missing_archive_errors(tmp_path: Path) -> None:
    """Trying to add to a non-existent archive exits with code 1."""
    new_file = tmp_path / "b.txt"
    new_file.write_text("B", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(tmp_path / "no_such_archive.xml"), str(new_file)])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


def test_add_missing_input_file_errors(tmp_path: Path) -> None:
    """Trying to add a non-existent input file exits with code 1."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(tmp_path / "nonexistent.txt")])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


def test_add_requires_archive_flag(tmp_path: Path) -> None:
    """Omitting -f/--file with -a produces a usage error."""
    new_file = tmp_path / "b.txt"
    new_file.write_text("B", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-a", str(new_file)])
    assert result.exit_code != 0
    assert "-f" in result.output or "--file" in result.output or "usage" in result.output.lower()


def test_add_rejects_conflicting_modes() -> None:
    """Specifying both -a and -c produces a usage error."""
    runner = CliRunner()
    result = runner.invoke(main, ["-c", "-a", "-f", "archive.xml", "input.txt"])
    assert result.exit_code != 0
    assert "cannot" in result.output.lower() or "usage" in result.output.lower()


def test_add_requires_input_paths(tmp_path: Path) -> None:
    """Omitting input paths with -a produces a usage error."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive)])
    assert result.exit_code != 0
    assert "input" in result.output.lower() or "usage" in result.output.lower()


# ---------------------------------------------------------------------------
# Directory input
# ---------------------------------------------------------------------------


def test_add_directory_input(tmp_path: Path) -> None:
    """Passing a directory to -a recursively adds all its files."""
    archive = _make_archive(tmp_path, {"existing.txt": "existing"})

    new_dir = tmp_path / "new_dir"
    new_dir.mkdir()
    (new_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (new_dir / "beta.txt").write_text("beta", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(new_dir)])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert any("alpha.txt" in p for p in paths)
    assert any("beta.txt" in p for p in paths)
    assert any("existing.txt" in p for p in paths)


# ---------------------------------------------------------------------------
# Result is valid XML after upsert
# ---------------------------------------------------------------------------


def test_add_result_is_valid_xml(tmp_path: Path) -> None:
    """The archive remains well-formed XML after an upsert."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    updated = tmp_path / "b.txt"
    updated.write_text("B-new", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["-af", str(archive), str(updated)])
    assert result.exit_code == 0, result.output

    # _parse_archive will raise if the XML is malformed
    root = _parse_archive(archive)
    assert root.tag == "archive"
    assert root.get("version") == "1.0"

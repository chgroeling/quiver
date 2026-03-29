"""Integration tests for the quiver CLI --delete command."""

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
        files: Mapping of relative path → content. Paths are placed under a
            ``project/`` staging directory so stored paths carry that prefix.

    Returns:
        Path to the created archive.
    """
    project = tmp_path / "project"
    for rel, content in files.items():
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(main, ["-cf", str(archive), str(project)])
    assert result.exit_code == 0, result.output
    return archive


def _parse_archive(archive: Path) -> etree._Element:
    """Parse *archive* XML and return the root element."""
    raw = Path(archive).read_text(encoding="utf-8")
    start = raw.index("<archive")
    end = raw.index("</archive>") + len("</archive>")
    return etree.fromstring(raw[start:end].encode())


def _file_paths(root: etree._Element) -> list[str]:
    """Return all <file path=...> values from *root* in document order."""
    return [elem.get("path", "") for elem in root.iter("file")]


def _directory_tree_text(root: etree._Element) -> str:
    """Return the text of the <directory_tree> element."""
    dt = root.find("directory_tree")
    assert dt is not None
    return dt.text or ""


# ---------------------------------------------------------------------------
# Single file deletion
# ---------------------------------------------------------------------------


def test_delete_removes_single_file(tmp_path: Path) -> None:
    """Deleting an exact file path removes only that <file> node."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B", "c.txt": "C"})
    before = _file_paths(_parse_archive(archive))
    target = next(p for p in before if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), target])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert target not in paths
    assert len(paths) == len(before) - 1
    assert all(p for p in paths if p.endswith(("a.txt", "c.txt")))


def test_delete_does_not_affect_other_files(tmp_path: Path) -> None:
    """Deleting one file leaves all other <file> nodes and their content intact."""
    archive = _make_archive(tmp_path, {"keep.txt": "keep me", "drop.txt": "drop me"})
    root_before = _parse_archive(archive)
    all_paths = _file_paths(root_before)
    keep_path = next(p for p in all_paths if p.endswith("keep.txt"))
    drop_path = next(p for p in all_paths if p.endswith("drop.txt"))

    keep_content_before = root_before.find(f".//file[@path='{keep_path}']/content")
    assert keep_content_before is not None
    original_content = keep_content_before.text

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), drop_path])
    assert result.exit_code == 0, result.output

    root = _parse_archive(archive)
    paths = _file_paths(root)
    assert drop_path not in paths
    keep_content_after = root.find(f".//file[@path='{keep_path}']/content")
    assert keep_content_after is not None
    assert keep_content_after.text == original_content


# ---------------------------------------------------------------------------
# Directory prefix deletion
# ---------------------------------------------------------------------------


def test_delete_removes_directory_prefix(tmp_path: Path) -> None:
    """Deleting a directory prefix removes all nested files."""
    archive = _make_archive(
        tmp_path,
        {
            "utils/helper.py": "helper",
            "utils/tools.py": "tools",
            "main.py": "main",
        },
    )
    all_paths = _file_paths(_parse_archive(archive))
    # Stored prefix is project/utils
    utils_prefix = next(p for p in all_paths if p.endswith("utils/helper.py"))
    utils_dir = utils_prefix.rsplit("/helper.py", 1)[0]  # e.g. "project/utils"

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), utils_dir])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert not any(p.startswith(utils_dir + "/") for p in paths)
    assert any(p.endswith("main.py") for p in paths)


def test_delete_directory_prefix_with_subdirectories(tmp_path: Path) -> None:
    """Deleting a directory prefix removes entries in nested subdirectories too."""
    archive = _make_archive(
        tmp_path,
        {
            "src/core/a.py": "a",
            "src/core/sub/b.py": "b",
            "src/other.py": "other",
            "readme.txt": "readme",
        },
    )
    all_paths = _file_paths(_parse_archive(archive))
    # Build the stored directory prefix for src/core
    core_entry = next(p for p in all_paths if p.endswith("core/a.py"))
    core_dir = core_entry.rsplit("/a.py", 1)[0]  # e.g. "project/src/core"

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), core_dir])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert not any(p.startswith(core_dir + "/") for p in paths)
    assert any(p.endswith("other.py") for p in paths)
    assert any(p.endswith("readme.txt") for p in paths)


# ---------------------------------------------------------------------------
# Multiple deletions in one invocation
# ---------------------------------------------------------------------------


def test_delete_multiple_targets_in_one_invocation(tmp_path: Path) -> None:
    """Multiple target paths can be deleted in a single --delete call."""
    archive = _make_archive(
        tmp_path,
        {"a.txt": "A", "b.txt": "B", "c.txt": "C"},
    )
    all_paths = _file_paths(_parse_archive(archive))
    a_path = next(p for p in all_paths if p.endswith("a.txt"))
    c_path = next(p for p in all_paths if p.endswith("c.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), a_path, c_path])
    assert result.exit_code == 0, result.output

    paths = _file_paths(_parse_archive(archive))
    assert a_path not in paths
    assert c_path not in paths
    assert any(p.endswith("b.txt") for p in paths)
    assert len(paths) == 1


# ---------------------------------------------------------------------------
# Non-existent target (no-op)
# ---------------------------------------------------------------------------


def test_delete_nonexistent_target_is_noop(tmp_path: Path) -> None:
    """Deleting a path not present in the archive exits 0 and leaves it unchanged."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    raw_before = archive.read_text(encoding="utf-8")

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), "this/does/not/exist.txt"])
    assert result.exit_code == 0, result.output
    assert archive.read_text(encoding="utf-8") == raw_before


def test_delete_nonexistent_target_silent(tmp_path: Path) -> None:
    """No output is produced when deleting a non-existent target (silent by default)."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), "ghost.txt"])
    assert result.exit_code == 0
    assert result.output == ""


# ---------------------------------------------------------------------------
# Directory tree regeneration
# ---------------------------------------------------------------------------


def test_delete_regenerates_directory_tree(tmp_path: Path) -> None:
    """After deletion, <directory_tree> no longer mentions the deleted file."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), b_path])
    assert result.exit_code == 0, result.output

    tree = _directory_tree_text(_parse_archive(archive))
    assert "b.txt" not in tree
    assert "a.txt" in tree


def test_delete_directory_removes_empty_parent_from_tree(tmp_path: Path) -> None:
    """After removing all files in a directory, that directory vanishes from the tree."""
    archive = _make_archive(
        tmp_path,
        {
            "utils/helper.py": "helper",
            "main.py": "main",
        },
    )
    all_paths = _file_paths(_parse_archive(archive))
    helper_path = next(p for p in all_paths if p.endswith("helper.py"))
    utils_dir = helper_path.rsplit("/helper.py", 1)[0]

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), utils_dir])
    assert result.exit_code == 0, result.output

    tree = _directory_tree_text(_parse_archive(archive))
    assert "utils" not in tree
    assert "helper.py" not in tree
    assert "main.py" in tree


def test_delete_all_entries_produces_dot_tree(tmp_path: Path) -> None:
    """When every entry is deleted, the <directory_tree> contains only '.'."""
    archive = _make_archive(tmp_path, {"a.txt": "A"})
    all_paths = _file_paths(_parse_archive(archive))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), *all_paths])
    assert result.exit_code == 0, result.output

    root = _parse_archive(archive)
    assert _file_paths(root) == []
    tree = _directory_tree_text(root).strip()
    assert tree == "."


# ---------------------------------------------------------------------------
# Preamble / epilogue preservation
# ---------------------------------------------------------------------------


def test_delete_preserves_preamble(tmp_path: Path) -> None:
    """Preamble text is preserved verbatim after a delete operation."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("A", encoding="utf-8")
    (project / "b.txt").write_text("B", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(
        main,
        ["-cf", str(archive), str(project), "--preamble", "# My preamble text\n"],
    )
    assert result.exit_code == 0, result.output

    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), b_path])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("# My preamble text\n")


def test_delete_preserves_epilogue(tmp_path: Path) -> None:
    """Epilogue text is preserved verbatim after a delete operation."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("A", encoding="utf-8")
    (project / "b.txt").write_text("B", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(
        main,
        ["-cf", str(archive), str(project), "--epilogue", "# My epilogue text\n"],
    )
    assert result.exit_code == 0, result.output

    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), b_path])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.endswith("# My epilogue text\n")


def test_delete_preserves_preamble_and_epilogue(tmp_path: Path) -> None:
    """Both preamble and epilogue are preserved verbatim after a delete."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("A", encoding="utf-8")
    (project / "b.txt").write_text("B", encoding="utf-8")
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(
        main,
        [
            "-cf",
            str(archive),
            str(project),
            "--preamble",
            "PRE\n",
            "--epilogue",
            "POST\n",
        ],
    )
    assert result.exit_code == 0, result.output

    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), b_path])
    assert result.exit_code == 0, result.output

    raw = archive.read_text(encoding="utf-8")
    assert raw.startswith("PRE\n")
    assert raw.endswith("POST\n")


# ---------------------------------------------------------------------------
# Silent / verbose output
# ---------------------------------------------------------------------------


def test_delete_is_silent_by_default(tmp_path: Path) -> None:
    """No stdout output is produced when --verbose is not passed."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), b_path])
    assert result.exit_code == 0
    assert result.output == ""


def test_delete_verbose_produces_output(tmp_path: Path) -> None:
    """--verbose causes at least one line of output to stdout."""
    archive = _make_archive(tmp_path, {"a.txt": "A", "b.txt": "B"})
    all_paths = _file_paths(_parse_archive(archive))
    b_path = next(p for p in all_paths if p.endswith("b.txt"))

    result = CliRunner().invoke(main, ["--delete", "-vf", str(archive), b_path])
    assert result.exit_code == 0, result.output
    assert result.output.strip() != ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_delete_requires_archive_flag() -> None:
    """--delete without -f/--file exits non-zero with a usage error."""
    result = CliRunner().invoke(main, ["--delete", "some/path.txt"])
    assert result.exit_code != 0


def test_delete_requires_input_paths(tmp_path: Path) -> None:
    """--delete with -f but no target paths exits non-zero with a usage error."""
    archive = tmp_path / "archive.xml"
    archive.write_text("<archive/>", encoding="utf-8")
    result = CliRunner().invoke(main, ["--delete", "-f", str(archive)])
    assert result.exit_code != 0


def test_delete_missing_archive_exits_nonzero(tmp_path: Path) -> None:
    """--delete on a non-existent archive file exits non-zero."""
    archive = tmp_path / "missing.xml"
    result = CliRunner().invoke(main, ["--delete", "-f", str(archive), "a.txt"])
    assert result.exit_code != 0


def test_delete_rejects_conflicting_modes(tmp_path: Path) -> None:
    """Specifying --delete alongside -c raises a usage error."""
    archive = tmp_path / "archive.xml"
    result = CliRunner().invoke(main, ["-c", "--delete", "-f", str(archive), "a.txt"])
    assert result.exit_code != 0

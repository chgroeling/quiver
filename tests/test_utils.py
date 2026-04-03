"""Tests for src/mdbox/utils/__init__.py — build_directory_tree."""

from __future__ import annotations

from mdbox.utils import build_directory_tree

# ---------------------------------------------------------------------------
# build_directory_tree — unit tests
# ---------------------------------------------------------------------------


def test_build_directory_tree_empty() -> None:
    """Empty path list returns just the root marker '.'."""
    assert build_directory_tree([]) == "."


def test_build_directory_tree_single_file() -> None:
    result = build_directory_tree(["only.txt"])
    assert result == ".\n└── only.txt"


def test_build_directory_tree_basic() -> None:
    """Canonical spec example: two files in a subdirectory plus one at root."""
    result = build_directory_tree(["a/b.txt", "a/c.txt", "d.txt"])
    expected = ".\n├── a/\n│   ├── b.txt\n│   └── c.txt\n└── d.txt"
    assert result == expected


def test_build_directory_tree_deep_nesting() -> None:
    """Deeply nested paths render with correct indentation at every level."""
    result = build_directory_tree(["top.txt", "x/y/other.txt", "x/y/z/deep.txt"])
    expected = (
        ".\n"
        "├── top.txt\n"
        "└── x/\n"
        "    └── y/\n"
        "        ├── other.txt\n"
        "        └── z/\n"
        "            └── deep.txt"
    )
    assert result == expected


def test_build_directory_tree_multiple_root_files() -> None:
    """Multiple files at the root level render with correct connectors."""
    result = build_directory_tree(["a.txt", "b.txt", "c.txt"])
    expected = ".\n├── a.txt\n├── b.txt\n└── c.txt"
    assert result == expected


def test_build_directory_tree_box_drawing_characters() -> None:
    """Verify the exact box-drawing characters appear in the output."""
    result = build_directory_tree(["a/b.txt", "c.txt"])
    assert "├── " in result
    assert "└── " in result
    assert "│   " in result


def test_build_directory_tree_directory_trailing_slash() -> None:
    """Directory nodes in the tree have a trailing '/'."""
    result = build_directory_tree(["src/main.py"])
    assert "src/" in result
    # The file itself must not have a slash
    assert "main.py/" not in result

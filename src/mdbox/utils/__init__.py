"""Utility modules for mdbox."""

from __future__ import annotations

# Tree node type: maps child name -> subtree (dict) or None for file leaves.
type _Node = dict[str, _Node | None]


def build_directory_tree(paths: list[str]) -> str:
    """Build a visual Unix `tree`-style string from a list of POSIX file paths.

    Takes a sorted list of POSIX paths representing files in an archive and
    produces a human-readable directory tree using standard box-drawing characters.
    Directories are inferred from the path components and rendered with a
    trailing `/`. The root is always rendered as `.`.

    Args:
        paths: Sorted list of POSIX file paths (e.g. `["a/b.txt", "c.txt"]`).

    Returns:
        A multi-line string starting with `.` representing the tree.
        Returns `"."` if *paths* is empty.

    Example:
        ```python
        tree = build_directory_tree(["a/b.txt", "a/c.txt", "d.txt"])
        # .
        # ├── a/
        # │   ├── b.txt
        # │   └── c.txt
        # └── d.txt
        ```
    """
    if not paths:
        return "."

    def _insert(node: _Node | None, parts: list[str]) -> _Node | None:
        if node is None:
            node = {}
        if not parts:
            return node
        head, *tail = parts
        if tail:
            node[head + "/"] = _insert(node.get(head + "/"), tail)
        else:
            node[head] = None
        return node

    root: _Node = {}
    for path in paths:
        parts = path.split("/")
        _insert(root, parts)

    lines: list[str] = ["."]

    def _render(node: _Node | None, prefix: str) -> None:
        if node is None:
            return
        items = list(node.items())
        for index, (name, subtree) in enumerate(items):
            is_last = index == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + name)
            if subtree is not None:
                extension = "    " if is_last else "│   "
                _render(subtree, prefix + extension)

    _render(root, "")
    return "\n".join(lines)

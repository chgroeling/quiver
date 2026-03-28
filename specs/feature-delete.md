# Feature: Delete Files from Archive

## 1. Overview
Implement the `--delete` command to remove specific files or entire directories from an existing `quiver` archive. The asynchronous writer must process the original archive, filter out the specified paths, regenerate the directory tree, and overwrite the original archive with the updated structure.

## 2. Requirements

### 2.1. CLI & Input
* **Command:** `quiver --delete <target_path> -f <existing_archive.xml>`
* **Target Path Matching:** * If `<target_path>` matches an exact file path (e.g., `src/main.py`), only that specific file is removed.
    * If `<target_path>` is a directory path (e.g., `src/utils/`), **all** files whose `path` attribute starts with that prefix must be removed.
* **Behavior (Silent by Default):** If the `<target_path>` does not exist in the archive, the CLI must not crash or output errors (unless `--verbose` or `--debug` is active). It simply leaves the archive unchanged.
* **Flags:** Respond to `--verbose` (`rich`) and `--debug` (`structlog`).

### 2.2. Core Logic: Deletion & Modification
The asynchronous writer must perform the following steps:

1. **Boundary Detection & Parsing:**
   * Locate the `PREAMBLE`, `<archive>`, and `EPILOGUE` boundaries in the original file.
   * Parse the isolated XML content into a modifiable tree structure.

2. **Node Removal:**
   * Iterate through the `<file>` nodes in the XML.
   * **Delete:** If the `path` attribute of a `<file>` node matches the `<target_path>` (exact match or directory prefix), remove that node entirely from the XML tree.

3. **Directory Tree Regeneration:**
   * Scan the `path` attributes of all *remaining* `<file>` nodes.
   * Generate the updated visual `<directory_tree>` string (which now omits the deleted files/folders) and replace the old tree node in the XML.

4. **Finalization (Overwrite):**
   * Reassemble the file sequentially: write the original `PREAMBLE`, the updated `<archive>` XML structure, and the original `EPILOGUE` text.
   * Overwrite the `<existing_archive.xml>` asynchronously with this combined content.

## 3. Acceptance Criteria (Testing)
All tests must run asynchronously (`pytest-asyncio`), use `pyfakefs`, and enforce strict typing.

* [ ] **Test Single File Deletion:** Deleting a specific file successfully removes its `<file>` node without affecting the rest of the archive.
* [ ] **Test Directory Deletion:** Passing a directory path (e.g., `folder/`) successfully removes all nested files (e.g., `folder/a.txt`, `folder/sub/b.txt`).
* [ ] **Test Non-Existent Target:** Attempting to delete a file that is not in the archive results in a successful exit code (0) and leaves the archive perfectly intact.
* [ ] **Test Tree Regeneration:** The `<directory_tree>` is correctly rebuilt and completely omits the deleted files and their now-empty parent directories.
* [ ] **Test Preamble/Epilogue Preservation:** The surrounding text is perfectly preserved in the updated archive.
* [ ] **Type Checking:** All new parsing, filtering, and CLI logic passes `mypy --strict` without errors.
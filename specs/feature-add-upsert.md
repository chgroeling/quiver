# Feature: Add / Upsert Files to Archive (Stream & Merge)

## 1. Overview
Implement the ability to add new text files or entire directories to an existing `quiver` archive. Because the archive must remain strictly alphabetized and memory-efficient (OOM-safe), we cannot simply append to the end of the file or load the entire XML into RAM. 

Instead, this feature implements an "Upsert" (Update or Insert) logic using a **Stream & Merge** pattern: The tool reads the original archive sequentially, merges it with the new files in alphabetical order, writes to a temporary file, and performs an atomic swap upon success. Existing files with the same path are silently updated (overwritten).

## 2. Requirements

### 2.1. CLI & Input
* **Command:** `quiver -a <input_folder_or_file> -f <existing_archive.xml>`
* **Aliases:** `--add` for `-a`.
* **Behavior (Upsert):** If a file from the input already exists in the archive (exact **full path** match, including the directory name prefix), its content in the archive is replaced. If it does not exist, it is inserted at the correct alphabetical position. Full paths are used for all comparisons — `mydir/foo.txt` and `otherdir/foo.txt` are treated as distinct entries.
* **Flags:** Respond to `--verbose` (`rich`) and `--debug` (`structlog`).

### 2.2. Core Logic: The Stream & Merge Process
To guarantee OOM-safety and data integrity, the asynchronous writer must perform the following steps:

1. **Boundary Detection & Temporary File Setup:**
   * Scan the original archive file to locate the `PREAMBLE`, the `<archive>` boundaries, and the `EPILOGUE` (reusing the logic from the previous feature).
   * Open a new temporary file (e.g., `archive.xml.tmp`) for asynchronous writing.
   * Write the `PREAMBLE` to the `.tmp` file.
   * Write the opening `<archive version="1.0">` tag.

2. **Pass 1: Directory Tree Regeneration:**
   * Use `lxml.iterparse` to scan *only* the `path` attributes of the existing `<file>` nodes in the original XML (clearing elements from memory immediately after reading to prevent RAM spikes).
   * Merge these existing paths with the normalized POSIX paths of the new input files.
   * Generate the updated visual `<directory_tree>` string and write it to the `.tmp` file.

3. **Pass 2: Ordered Merge & Upsert:**
   * Stream the `<file>` nodes from the original XML archive sequentially.
   * **Insert:** Before writing an original `<file>` node to the `.tmp` file, check if any of the *new* files belong alphabetically *before* this original node. If so, generate and write the new `<file>` node(s) first.
   * **Upsert (Overwrite):** If the path of the original `<file>` node matches the path of a new file, **skip/discard** the original node. Write the new `<file>` node (with the new CDATA content) in its place.
   * **Copy:** If the original `<file>` node is not replaced and no new files need to be inserted before it, write the original node to the `.tmp` file exactly as it was.
   * *Drain:* After the original stream is fully read, write any remaining new files that sort alphabetically at the very end.

4. **Finalization & Atomic Swap:**
   * Write the closing `</archive>` tag to the `.tmp` file.
   * Write the `EPILOGUE` text.
   * Close the `.tmp` file.
   * Perform an atomic swap (e.g., `os.replace` or asynchronous equivalent) to replace the original `<existing_archive.xml>` with the newly built `.tmp` file.

## 3. Acceptance Criteria (Testing)
All tests must run asynchronously (`pytest-asyncio`), use `pyfakefs`, and enforce strict typing.

* [ ] **Test Ordered Insertion:** Adding a new file correctly places its `<file>` node in the exact alphabetical position, not just at the bottom.
* [ ] **Test Upsert (Overwrite):** Adding a file that already exists in the archive updates the `<content>` block of that file without duplicating the `<file>` node.
* [ ] **Test Tree Regeneration:** The `<directory_tree>` is correctly rebuilt and includes both the old and the newly added files.
* [ ] **Test OOM Safety (Iterparse):** The process uses `lxml.iterparse` and explicitly clears processed elements (`element.clear()`) to ensure memory usage remains flat even with massive archives.
* [ ] **Test Atomic Swap Integrity:** If an error occurs during the generation of the `.tmp` file (e.g., a simulated read error), the original archive file remains completely untouched and uncorrupted.
* [ ] **Test Preamble/Epilogue Preservation:** The surrounding text is perfectly preserved in the updated archive.
* [ ] **Type Checking:** All new streaming, merging, and file-swapping logic passes `mypy --strict` without errors.
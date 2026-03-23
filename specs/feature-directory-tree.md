# Feature: Lazy Directory Tree Generation

## 1. Overview
Implement the generation of a visual `<directory_tree>` node within the XML archive. This feature provides a human-readable, Unix `tree`-like representation of the packed folder structure. To ensure all files are accounted for and correctly sorted, the tree must be generated lazily at the end of the archiving process.

## 2. Requirements

### 2.1. Tree Generation Logic
* **Path Collection:** As the asynchronous Writer task processes files, it must maintain a complete, sorted list of all written POSIX paths.
* **String Formatting:** Implement a utility function that takes this list of paths and generates a visual tree string. 
* **Box-Drawing Characters:** Use standard Unix `tree` characters (`├──`, `└──`, `│`, and ` `) to format the hierarchy clearly.
* **File-Only Nodes:** Since `quiver` currently only packs text files, the tree should only render directories that actually contain valid packed files.

### 2.2. Lazy Finalization
* **Hook into `close()`:** The XML `<directory_tree>` node must be finalized and inserted into the archive structure only when the `QuiverFile.close()` method is called (or when the `async with` context manager exits). 
* **Placement:** The `<directory_tree>` tag must be placed as the first child of the `<archive>` root tag, directly above the `<file>` entries. *(Note for the agent: Consider how `lxml` handles memory or stream modification to insert this at the top after processing all files).*

### 2.3. Expected XML Output Format
```xml
<archive version="1.0">
  <directory_tree>
.
├── src/
│   ├── main.py
│   └── utils/
│       └── helper.py
└── tests/
    └── test_main.py
  </directory_tree>
  <file path="src/main.py">
    <content><![CDATA[...]]></content>
  </file>
  </archive>
```

## 3. Acceptance Criteria (Testing)
All tests must remain strictly typed and use `pyfakefs` for filesystem mocking.

* [ ] **Test Tree Algorithm:** A standalone unit test verifies that a given list of POSIX paths `["a/b.txt", "a/c.txt", "d.txt"]` produces the correct visual tree string using the exact box-drawing characters.
* [ ] **Test Integration:** The final XML output contains the `<directory_tree>` node at the very beginning of the `<archive>` block.
* [ ] **Test Deep Nesting:** The tree accurately reflects deeply nested structures without formatting breaks.
* [ ] **Type Checking:** All new string manipulation and tree-building functions pass `mypy --strict` without errors.

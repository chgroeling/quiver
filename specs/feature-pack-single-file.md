# Feature: Pack a Single Text File (Synchronous MVP)

## 1. Overview
Implement the foundational logic and CLI command to pack a single text file into the `quiver` XML archive format. This feature establishes the core XML generation, basic CLI routing, and the foundation for file validation.

## 2. Requirements

### 2.1. Command Line Interface
* **Command:** `quiver -c <input_file> -f <output_archive.xml>`
* **Aliases:** `--create` for `-c`, `--file` for `-f`.
* **Flags:** * `--verbose` / `-v`: Triggers UI output via `rich` (e.g., "Packing file X...").
    * `--debug`: Activates `structlog` for internal debugging.
    * **Crucial:** If neither flag is passed, the CLI must execute with zero terminal output (Silent by default).

### 2.2. Core Logic & XML Generation
* **File Reading:** Read the specified `<input_file>`. 
* **Validation:** * Verify the file exists.
    * Verify the file contains valid UTF-8 text. If it is binary or unreadable, abort with a clear error.
* **Path Normalization:** Normalize the path of the input file to a POSIX path (forward slashes `/`) for the `path` attribute.
* **XML Construction:** Use `lxml` to generate the XML structure.
    * Root element: `<archive version="1.0">`
    * Child element: `<file path="normalized/posix/path.txt">`
    * Content element: `<content>` containing the exact file text wrapped in `<![CDATA[ ... ]]>`. **Do not** use entity encoding (`&lt;`, `&gt;`) for the file content.
* **Writing:** Write the resulting XML tree to the path specified by the `-f` argument.

### 2.3. Expected XML Output Format
```xml
<archive version="1.0">
  <file path="example.txt">
    <content><![CDATA[
This is the raw content of the file.
Special characters like < and & are perfectly fine here.
]]></content>
  </file>
</archive>
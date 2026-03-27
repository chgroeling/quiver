# Feature: Archive Text Preamble & Epilogue (Embedded Archive Support)

## 1. Overview
Implement the ability to wrap the generated `quiver` XML archive with arbitrary plain text. A "preamble" is inserted before the opening `<archive>` tag, and an "epilogue" is appended after the closing `</archive>` tag. Crucially, when extracting an archive (`-x`), the tool must detect this surrounding text and save it into dedicated files named `PREAMBLE` and `EPILOGUE` within the extraction destination.

## 2. Requirements

### 2.1. CLI & Input (Creation)
* **Command Expansion:** Update the creation command (`-c`) to accept new optional flags.
* **Flags:**
    * `--preamble <text_or_filepath>`: Text to prepend before the archive. If the argument is a valid file path, read the contents of the file; otherwise, treat it as a raw string.
    * `--epilogue <text_or_filepath>`: Text to append after the archive. Same filepath-or-string logic applies.
* **Formatting:** Ensure there is a clean line break (`\n`) between the preamble, the XML archive, and the epilogue to maintain readability.

### 2.2. Core Logic (Creation)
* **Writing Phase:** When the asynchronous Single Writer finalizes the archive and calls `close()`, it must sequence the file writing as follows:
    1. Write the preamble text (if provided).
    2. Write the generated `<archive>` XML structure.
    3. Write the epilogue text (if provided).

### 2.3. Core Logic (Extraction & Parsing Updates)
* **Embedded Parsing & Splitting:** When reading an existing archive (e.g., `-x` / `--extract`), the tool must scan the file to find the boundaries of the **first** `<archive>` block.
* **Multiple Archives (First-Match Rule - CRITICAL):** If a file contains multiple `<archive>` blocks, the parser must strictly operate on the **first** one encountered. The first `</archive>` closing tag marks the absolute end of the parsed XML. Everything after this first closing tag—including any and all subsequent `<archive>` blocks—must be treated purely as plain text epilogue.
* **XML Extraction:** Feed *only* the isolated `xml_content` string/stream from the first archive to `lxml` for standard file extraction.
* **Preamble/Epilogue Extraction:**
    * If the text before the first `<archive>` tag contains non-whitespace characters, write it asynchronously to a file strictly named `PREAMBLE` in the root of the `destination_folder`.
    * If the text after the first `</archive>` tag contains non-whitespace characters, write it asynchronously to a file strictly named `EPILOGUE` in the root of the `destination_folder`.

### 2.4. Expected Output Format (Example with Multiple Archives)
```text
Here is the first archive:

<archive version="1.0">
  <file path="file1.txt"><content><![CDATA[Data 1]]></content></file>
</archive>

And here is the second archive:

<archive version="1.0">
  <file path="file2.txt"><content><![CDATA[Data 2]]></content></file>
</archive>
```

**Extraction Result:**
* `file1.txt` is extracted to the destination folder.
* A file named `PREAMBLE` is created containing: `"Here is the first archive:\n\n"`
* A file named `EPILOGUE` is created containing exactly the following (the second archive is captured as pure text):
  ```text
  

  And here is the second archive:

  <archive version="1.0">
    <file path="file2.txt"><content><![CDATA[Data 2]]></content></file>
  </archive>
  ```

## 3. Acceptance Criteria (Testing)
All tests must run asynchronously (`pytest-asyncio`), use `pyfakefs`, and enforce strict typing.

* [ ] **Test Preamble/Epilogue Creation:** Creating an archive with both `--preamble` and `--epilogue` writes the texts at the correct boundaries of the file.
* [ ] **Test Filepath Resolution:** Passing a filepath to `--preamble` correctly reads the file's content and prepends it instead of writing the literal path string.
* [ ] **Test Extraction:** Running `-x` on a wrapped archive correctly unpacks the XML files AND creates the `PREAMBLE` and `EPILOGUE` files containing the exact surrounding text.
* [ ] **Test Empty Surrounding Text:** If there is no text (or only standard whitespace) before or after the archive, the `PREAMBLE` or `EPILOGUE` files are **not** created.
* [ ] **Test Multiple Archives (Epilogue Capture):** Running `-x` on a file containing two archive blocks extracts ONLY the first one. The entire second archive block must be written into the `EPILOGUE` file, byte-for-byte, without triggering XML parsing errors.
* [ ] **Type Checking:** All boundary detection, string manipulation, and I/O logic passes `mypy --strict` without errors.

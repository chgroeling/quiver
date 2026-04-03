# MdBox Documentation

MdBox is a high-performance, cross-platform Python package and command-line tool for packing and unpacking text files into a strictly formatted, machine-readable XML format.

## Installation

```bash
pip install MdBox
```

## Usage

### Pack files into XML

```bash
MdBox -cf archive.xml ./src ./README.md ./docs
```

### Unpack XML into files

```bash
MdBox -xf archive.xml
```

## Development

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.

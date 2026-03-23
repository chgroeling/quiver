# Quiver Documentation

Quiver is a high-performance, cross-platform Python package and command-line tool for packing and unpacking text files into a strictly formatted, machine-readable XML format.

## Installation

```bash
pip install quiver
```

## Usage

### Pack files into XML

```bash
quiver -cf archive.xml ./src ./README.md ./docs
```

### Unpack XML into files

```bash
quiver -xf archive.xml
```

## Development

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.
